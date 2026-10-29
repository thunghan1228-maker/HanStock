"""K 棒型態道場（2026-10-30 使用者：老師有 /kbar 型態道場，我們也做一個第一版）。

五種型態（紅劍、黑豹、黑飛舞、三角收斂、穿山惡龍），在我們自己的日K歷史（約 3 年）裡掃出真實案例，出成考題：
框起來的K棒是哪一種型態？答完還能「跑圖」看後面 10 天怎麼走、有沒有跌破防守線。
型態的量化門檻是我們自己的示範值（老師的頁面也寫「量化門檻為示範，待莊爸確認」），文字說明用我們自己的寫法；
價格用分割減資＋除息還原後的日K（跟均線分數同一套），跑圖的後 10 天一定要有資料才出題。僅供教學，非投資建議。

做法：
- detect(bars, i)：第 i 根（含）以前的資料判斷那根是不是某個型態的最後一根，回 {pattern, box: (起, 迄), end, defense}。
- scan(bars)：掃整檔，同一型態兩次案例至少隔 SPACING 根，後面要有 FUTURE 根才收。
- pool()：全部族群股的案例池，記憶體裡快取（背景執行緒建，第一次要二十來秒）；quiz／lessons 從池裡抽。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("hanstock.kbar_dojo")
TW_TZ = timezone(timedelta(hours=8))

# (日期, 開, 高, 低, 收, 量張)
Bar = tuple[str, float, float, float, float, int]

PATTERNS = ("sword", "panther", "dance", "triangle", "dragon")
NAMES = {"sword": "紅劍", "panther": "黑豹", "dance": "黑飛舞", "triangle": "三角收斂", "dragon": "穿山惡龍"}

MIN_HISTORY = 130       # 前面至少要有幾根（穿山惡龍看 120 根的漲幅）
FUTURE = 10             # 跑圖：後面要有 10 根
SHOW_BEFORE = 75        # 題目圖：型態最後一根以前給幾根（左邊 20 根只拿來算均線）
SPACING = 25            # 同一檔同一型態兩個案例至少隔幾根
POOL_YEARS_DAYS = 1100  # 往前掃幾個日曆天
POOL_TTL = 6 * 3600     # 案例池快取幾秒
PER_PATTERN_CAP = 400   # 每個型態最多留幾個案例（隨機抽，避免池太大）

# ---- 紅劍：盤中一度拉成長紅、收盤被壓回、留下長上影，當天爆量，起漲第一根
SWORD_HIGH_PCT = 0.06          # 最高價比昨收高 ≥6%（盤中一度長紅）
SWORD_UPPER_RATIO = 0.5        # 上影線佔整根振幅 ≥50%
SWORD_UPPER_PCT = 0.03         # 上影線（最高−收盤）≥ 昨收的 3%
SWORD_VOL_MULT = 2.5           # 量 ≥ 前 5 日均量 2.5 倍
SWORD_MIN_LOTS = 1000          # 至少 1000 張（太冷門的不出）
SWORD_NOT_RAN = 1.08           # 起漲第一根：昨天比 4 天前漲不到 8%
SWORD_BELOW_MA20 = 1.12        # 收盤不高於月線 12%（已經漲一大段的不算起漲）
SWORD_DEFENSE_NEAR = 0.03      # 劍柄（收盤）離劍低 3% 內防守劍低，太遠防守劍柄

# ---- 黑豹：前一天漲停、今天開高、爆量收長黑
PANTHER_LIMIT_PCT = 0.095      # 前一天收盤比前前天漲 ≥9.5%（漲停）
PANTHER_GAP = 0.01             # 今天開盤比昨收高 ≥1%（開高）
PANTHER_VOL_MULT = 1.5         # 今天量 ≥ 前一天的 1.5 倍（爆量）
PANTHER_BODY = 0.05            # 黑K實體（開盤−收盤）≥ 開盤的 5%（長黑）
PANTHER_MIN_LOTS = 1000

# ---- 黑飛舞：股價跳空創新高卻收黑，隔天量縮、拉回五日線附近沒跌破（最多破 2.5%）
DANCE_NEW_HIGH = 60            # 創 60 日新高
DANCE_VOL_SHRINK = 0.75        # 隔天量 ≤ 黑K那天的 75%
DANCE_MA5_BAND = 0.025         # 隔天收盤離五日線最多低 2.5%
DANCE_MA5_NEAR = 0.03          # 隔天最低價要碰到五日線上 3% 以內（拉回）
DANCE_MIN_LOTS = 800

# ---- 三角收斂：高點越來越低、低點越來越高、波動縮小，末端第一根長紅站上壓力
TRI_WINDOWS = (24, 30, 36)     # 往前看幾根（切三段：每一段的最高都比前一段低、最低都比前一段高）
TRI_STEP = 0.01                # 每一段的高點比前一段低、低點比前一段高至少 1%
TRI_SHRINK = 0.65              # 最後一段的寬度（最高−最低）≤ 第一段的 65%
TRI_BREAK_RED = 0.035          # 突破那根收紅（收盤比開盤高）≥3.5%
TRI_BREAK_VOL = 1.3            # 突破那天量 ≥ 前 20 日均量 1.3 倍
TRI_MIN_LOTS = 800

# ---- 穿山惡龍：強勢股（漲 3 成以上、月線向上）先跌破月線（破惡），再站回月線（穿惡）
DRAGON_RISE = 1.30             # 近 120 根最高收盤比最低收盤 ≥ 1.3 倍
DRAGON_MA20_UP = 1.02          # 昨天的月線比 20 根前高 ≥2%
DRAGON_BREAK_WITHIN = 10       # 昨天以前 10 根內有收在月線下（破惡）
DRAGON_ABOVE = 0.03            # 今天收盤比月線高 ≥3%（穿惡）
DRAGON_VOL_MULT = 1.2          # 今天量 ≥ 前 5 日均量 1.2 倍
DRAGON_MIN_LOTS = 1000


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ma(closes: list[float], i: int, n: int) -> float | None:
    """第 i 根（含）往前 n 根的收盤均價。"""
    return _mean(closes[i - n + 1:i + 1]) if i >= n - 1 else None


def _vol_avg(bars: list[Bar], i: int, n: int) -> float:
    """第 i 根「以前」n 根的均量（不含第 i 根）。"""
    return _mean([float(b[5]) for b in bars[max(0, i - n):i]])


# ------------------------------------------------------------------ 五個型態

def detect_sword(bars: list[Bar], closes: list[float], i: int) -> dict[str, Any] | None:
    d, o, h, l, c, v = bars[i]
    pc = bars[i - 1][4]
    if pc <= 0 or h <= l or v < SWORD_MIN_LOTS or c <= o or c < pc:
        return None
    upper = h - max(o, c)
    if h < pc * (1 + SWORD_HIGH_PCT) or upper / (h - l) < SWORD_UPPER_RATIO or (h - c) < pc * SWORD_UPPER_PCT:
        return None
    if v < SWORD_VOL_MULT * _vol_avg(bars, i, 5):
        return None
    ma20 = _ma(closes, i, 20)
    if ma20 is None or c > ma20 * SWORD_BELOW_MA20 or closes[i - 1] >= closes[i - 4] * SWORD_NOT_RAN:
        return None
    defense = l if c <= l * (1 + SWORD_DEFENSE_NEAR) else c
    return {"pattern": "sword", "box": (i, i), "end": i, "defense": round(defense, 2),
            "defenseNote": "劍柄離劍低 3% 內 → 防守劍低；離太遠 → 防守劍柄（收盤價）"}


def detect_panther(bars: list[Bar], closes: list[float], i: int) -> dict[str, Any] | None:
    d, o, h, l, c, v = bars[i]
    pd_, po, ph, pl, pc, pv = bars[i - 1]
    ppc = bars[i - 2][4]
    if ppc <= 0 or pc < ppc * (1 + PANTHER_LIMIT_PCT) or pc < ph * 0.99:    # 前一天漲停（收在最高附近）
        return None
    if o < pc * (1 + PANTHER_GAP) or c >= o or (o - c) < o * PANTHER_BODY:     # 開高、收長黑
        return None
    if v < pv * PANTHER_VOL_MULT or v < PANTHER_MIN_LOTS:
        return None
    return {"pattern": "panther", "box": (i - 1, i), "end": i, "defense": round(l, 2),
            "defenseNote": "收盤入場，跌破黑K低點就出場"}


def detect_dance(bars: list[Bar], closes: list[float], i: int) -> dict[str, Any] | None:
    """i＝拉回那天（黑K的隔天）；黑K＝i-1。"""
    if i < DANCE_NEW_HIGH + 2:
        return None
    d0, o0, h0, l0, c0, v0 = bars[i - 1]
    d, o, h, l, c, v = bars[i]
    if c0 >= o0 or o0 <= bars[i - 2][2] or v0 < DANCE_MIN_LOTS:        # 黑K、跳空（開盤高過前一天最高）
        return None
    if h0 < max(b[2] for b in bars[i - 1 - DANCE_NEW_HIGH:i - 1]):      # 黑K那天最高創 60 日新高
        return None
    ma5 = _ma(closes, i, 5)
    if ma5 is None or v > v0 * DANCE_VOL_SHRINK:
        return None
    if c < ma5 * (1 - DANCE_MA5_BAND) or l > ma5 * (1 + DANCE_MA5_NEAR):
        return None
    ma10 = _ma(closes, i, 10) or ma5
    return {"pattern": "dance", "box": (i - 1, i), "end": i, "defense": round(min(ma5, ma10), 2),
            "defenseNote": "創高不追高，拉回五日線附近收盤入場，防守五日線或十日線"}


def detect_triangle(bars: list[Bar], closes: list[float], i: int) -> dict[str, Any] | None:
    d, o, h, l, c, v = bars[i]
    if v < TRI_MIN_LOTS or c < o * (1 + TRI_BREAK_RED) or v < TRI_BREAK_VOL * _vol_avg(bars, i, 20):
        return None
    for w in TRI_WINDOWS:
        start = i - w
        if start < 0:
            continue
        seg = w // 3
        parts = [range(start + k * seg, start + (k + 1) * seg if k < 2 else i) for k in range(3)]     # 突破那根不算在收斂裡
        hi_at = [max(parts[k], key=lambda j: bars[j][2]) for k in range(3)]
        lo_at = [min(parts[k], key=lambda j: bars[j][3]) for k in range(3)]
        highs = [bars[j][2] for j in hi_at]
        lows = [bars[j][3] for j in lo_at]
        if not (highs[0] > highs[1] * (1 + TRI_STEP) and highs[1] > highs[2] * (1 + TRI_STEP)):
            continue
        if not (lows[2] > lows[1] * (1 + TRI_STEP) and lows[1] > lows[0] * (1 + TRI_STEP)):
            continue
        width0, width2 = highs[0] - lows[0], highs[2] - lows[2]
        if width0 <= 0 or width2 > TRI_SHRINK * width0:
            continue
        if c <= highs[2] or bars[i - 1][4] > highs[2] * 1.01:             # 今天才站上最後一段的高點（昨天還沒突破）
            continue
        return {"pattern": "triangle", "box": (start, i), "end": i, "defense": round(l, 2),
                "defenseNote": "第一根長紅突破壓力線就是發動訊號，防守突破K棒的低點",
                "lines": {"top": [hi_at[0], round(highs[0], 2), hi_at[2], round(highs[2], 2)],
                          "bottom": [lo_at[0], round(lows[0], 2), lo_at[2], round(lows[2], 2)]}}
    return None


def detect_dragon(bars: list[Bar], closes: list[float], i: int) -> dict[str, Any] | None:
    d, o, h, l, c, v = bars[i]
    ma20, ma20_prev = _ma(closes, i, 20), _ma(closes, i - 1, 20)
    ma20_old = _ma(closes, i - 21, 20)
    if ma20 is None or ma20_prev is None or ma20_old is None or v < DRAGON_MIN_LOTS:
        return None
    if closes[i - 1] >= ma20_prev or c < ma20 * (1 + DRAGON_ABOVE):             # 昨天收在月線下、今天站回月線上 3%
        return None
    if ma20_prev < ma20_old * DRAGON_MA20_UP:                                   # 月線一路往上
        return None
    window = closes[i - 120:i]
    if max(window) < min(window) * DRAGON_RISE:                                  # 強勢股：近 120 根漲了三成以上
        return None
    if v < DRAGON_VOL_MULT * _vol_avg(bars, i, 5):
        return None
    prev_low = bars[i - 1][3]
    return {"pattern": "dragon", "box": (i - 1, i), "end": i, "defense": round(min(prev_low, ma20), 2),
            "defenseNote": "進場後防守昨日低點或月線"}


DETECTORS = {"sword": detect_sword, "panther": detect_panther, "dance": detect_dance, "triangle": detect_triangle, "dragon": detect_dragon}


def scan(bars: list[Bar], *, future: int = FUTURE) -> list[dict[str, Any]]:
    """掃一檔：回案例（依日期舊到新）。同一型態兩個案例隔 SPACING 根以上，後面要有 future 根。"""
    out: list[dict[str, Any]] = []
    last: dict[str, int] = {}
    n = len(bars)
    closes = [b[4] for b in bars]
    for i in range(MIN_HISTORY, n - future):
        if bars[i][4] <= 0 or bars[i][2] <= bars[i][3]:
            continue
        for name, fn in DETECTORS.items():
            if i - last.get(name, -10 ** 9) < SPACING:
                continue
            hit = fn(bars, closes, i)
            if hit:
                last[name] = i
                out.append(hit)
    return out


# ------------------------------------------------------------------ 出題

def case_payload(code: str, name: str, bars: list[Bar], hit: dict[str, Any], *, reveal: bool = True) -> dict[str, Any]:
    """一個案例的前端資料：型態最後一根以前 SHOW_BEFORE 根（含）＋後 FUTURE 根；box 換成相對這段的位置。"""
    end = hit["end"]
    start = max(0, end - SHOW_BEFORE + 1)
    shown = bars[start:end + 1]
    future = bars[end + 1:end + 1 + FUTURE]
    shift = start

    def rel(k: int) -> int:
        return k - shift

    out = {
        "id": f"{hit['pattern']}-{code}-{bars[end][0]}", "pattern": hit["pattern"], "label": NAMES[hit["pattern"]],
        "code": code, "name": name, "date": bars[end][0],
        "bars": [[b[0], b[1], b[2], b[3], b[4], b[5]] for b in shown],
        "future": [[b[0], b[1], b[2], b[3], b[4], b[5]] for b in future],
        "box": [max(0, rel(hit["box"][0])), rel(hit["box"][1])],
        "defense": hit["defense"], "defenseNote": hit["defenseNote"],
        "lead": min(20, len(shown) - 1),
    }
    if "lines" in hit:
        t, b = hit["lines"]["top"], hit["lines"]["bottom"]
        out["lines"] = {"top": [rel(t[0]), t[1], rel(t[2]), t[3]], "bottom": [rel(b[0]), b[1], rel(b[2]), b[3]]}
    return out


_lock = threading.Lock()
_state: dict[str, Any] = {"building": False, "builtAt": None, "error": None, "cases": {}, "stocks": 0, "count": 0}


def _build() -> None:
    from brew_launch import group_codes
    from brew_launch_history import group_and_name
    from heilong_backtest import _load_bars

    try:
        today = datetime.now(TW_TZ).date()
        since = (today - timedelta(days=POOL_YEARS_DAYS)).isoformat()
        codes = group_codes()
        loaded = _load_bars(codes, since=since, until=today.isoformat(), adjusted=True)
        rng = random.Random(20261030)
        cases: dict[str, list[dict[str, Any]]] = {p: [] for p in PATTERNS}
        for code, bars in loaded.items():
            if len(bars) < MIN_HISTORY + FUTURE + 5:
                continue
            name = group_and_name(code)[1] or code
            for hit in scan(bars):
                cases[hit["pattern"]].append(case_payload(code, name, bars, hit))
        for p in PATTERNS:
            if len(cases[p]) > PER_PATTERN_CAP:
                cases[p] = rng.sample(cases[p], PER_PATTERN_CAP)
        with _lock:
            _state.update({"cases": cases, "builtAt": time.time(), "error": None, "stocks": len(loaded), "count": sum(len(v) for v in cases.values())})
        logger.info("kbar dojo pool built: %s", {p: len(v) for p, v in cases.items()})
    except Exception as exc:  # noqa: BLE001
        logger.exception("kbar dojo pool failed")
        with _lock:
            _state["error"] = str(exc)
    finally:
        with _lock:
            _state["building"] = False


def ensure_pool(*, wait: bool = False) -> dict[str, Any]:
    """案例池準備好了就回 ready；沒有（或過期）就開背景執行緒建，先回 building。"""
    with _lock:
        fresh = _state["builtAt"] is not None and time.time() - _state["builtAt"] < POOL_TTL
        if not fresh and not _state["building"]:
            _state["building"] = True
            threading.Thread(target=_build, name="kbar-dojo-pool", daemon=True).start()
        building = _state["building"]
        have = _state["builtAt"] is not None
    if wait:
        while True:
            with _lock:
                if not _state["building"]:
                    break
            time.sleep(0.2)
        have, building = True, False
    return {"ready": have, "building": building and not have, "error": _state["error"], "stocks": _state["stocks"], "count": _state["count"]}


def _per_pattern_counts() -> dict[str, int]:
    with _lock:
        return {p: len(v) for p, v in _state["cases"].items()}


def quiz(n: int = 10, seed: int | None = None) -> dict[str, Any]:
    """出 n 題，五個型態平均（n=10 就每種 2 題），同一檔不重複。池還沒好回 status building。"""
    info = ensure_pool()
    if not info["ready"]:
        return {"status": "building", **info}
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    n = max(1, min(int(n), 20))
    with _lock:
        pool = {p: list(v) for p, v in _state["cases"].items()}
    picked: list[dict[str, Any]] = []
    used: set[str] = set()
    order = list(PATTERNS)
    k = 0
    while len(picked) < n and order:
        p = order[k % len(order)]
        k += 1
        candidates = [c for c in pool.get(p, []) if c["code"] not in used]
        if not candidates:
            order.remove(p)
            continue
        c = rng.choice(candidates)
        pool[p].remove(c)
        used.add(c["code"])
        picked.append(c)
    rng.shuffle(picked)
    return {"status": "ok", "questions": picked, "patterns": [{"id": p, "label": NAMES[p]} for p in PATTERNS], "counts": _per_pattern_counts(), **info}


def lessons() -> dict[str, Any]:
    """每個型態挑一個教學用的案例（池裡跑圖後 10 天結果漂亮、型態清楚的優先：這裡簡單取最近日期、後 10 天沒破防守的）。"""
    info = ensure_pool()
    if not info["ready"]:
        return {"status": "building", **info}
    with _lock:
        pool = {p: list(v) for p, v in _state["cases"].items()}
    out = []
    for p in PATTERNS:
        cases = pool.get(p, [])
        good = [c for c in cases if c["future"] and min(b[3] for b in c["future"]) >= c["defense"]]
        best = max(good or cases, key=lambda c: c["date"], default=None)
        if best:
            out.append(best)
    return {"status": "ok", "examples": out, "patterns": [{"id": p, "label": NAMES[p]} for p in PATTERNS], **info}
