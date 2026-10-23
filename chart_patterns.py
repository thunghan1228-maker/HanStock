"""形態選股（2026-10-23 使用者：VCP 收斂、C 不破 A 的低、W 底、頭肩底；盤後用日K找候選，盤中現價突破觸發價才算發動）。

做法：先用 ZigZag 找出日K的波段高低點（漲跌 ≥ 門檻才算一個轉折），再用轉折點套規則：
  W 底      ：低 L1 → 反彈高 H（頸線）→ 低 L2，兩個低點差不多高（L2 比 L1 低 ≤3%、高 ≤4%），中間反彈 ≥8%，兩低點隔 10～70 根，前面有一段下跌。
  頭肩底    ：左肩 → 高 → 頭（最低）→ 高 → 右肩，頭比兩肩低 ≥3%，兩肩差 ≤8%，兩個反彈高點差 ≤6%（頸線接近水平），整段 25～120 根。
  C 不破 A  ：低 A → 反彈高 B → 低 C，C 比 A 高 ≥4%（低點墊高）、反彈 B 比 A 高 ≥10%，現價在季線（60 日線）之上＝相對強勢。
  VCP 收斂  ：連續 ≥2 次回檔，每一次回檔的深度都比前一次小（≤ 0.9 倍），最後一次 ≤12%，低點不破前一個低點，量縮，價在季線之上、季線向上。
每一種算出：觸發價（W／頭肩底＝頸線、C 不破 A＝前高 B、VCP＝最後一次收縮前的高點）、停損參考、目標價（形態高度往上量）。
state：candidate＝形態成立、現價還在觸發價之下（等突破）；breakout＝最近 3 根內收盤站上觸發價（盤中現價站上也算，由前端用即時價判斷）。
這是用價格規則找出來的「候選」，形態本來就有主觀成分，會漏也會誤判，請看圖確認；不是買進訊號。
"""

from __future__ import annotations

from typing import Any

# bars：舊到新，(日期, 最高, 最低, 收盤, 成交量張)；最後一根是資料日
Bar = tuple[str, float, float, float, float]

ZIG = {"w": 0.07, "hs": 0.07, "hl": 0.08, "vcp": 0.045}   # 各形態用的 ZigZag 轉折門檻
MIN_BARS = 80

W_LOW_DOWN, W_LOW_UP = 0.03, 0.04      # 第二個低點比第一個低最多 3%、高最多 4%
W_BOUNCE = 0.08
W_GAP = (10, 70)
PRIOR_DROP = 0.12                      # 底部形態前面要有一段 ≥12% 的下跌
HS_HEAD = 0.03
HS_SHOULDER = 0.08
HS_NECK = 0.06
HS_SPAN = (25, 120)
HL_HIGHER = 0.04
HL_BOUNCE = 0.10
VCP_SHRINK = 0.9
VCP_LAST_MAX = 0.12
VCP_FIRST = (0.10, 0.45)
RECENT_BARS = 30                       # 最後一個低點要在最近 30 根內
BREAKOUT_FRESH = 3                     # 站上觸發價幾根內算「剛突破」


def zigzag(bars: list[Bar], pct: float) -> list[dict[str, Any]]:
    """波段轉折點：[{i, kind: 'H'|'L', price, date, confirmed}]。最後一個還沒被反向走勢確認的極值也會列（confirmed False）。"""
    n = len(bars)
    if n < 2:
        return []
    out: list[dict[str, Any]] = []
    hi, hi_i, lo, lo_i = bars[0][1], 0, bars[0][2], 0
    last: str | None = None

    def add(i: int, kind: str, price: float) -> None:
        out.append({"i": i, "kind": kind, "price": price, "date": bars[i][0], "confirmed": True})

    for i in range(1, n):
        _, h, l, _c, _v = bars[i]
        if last is None:
            if h > hi:
                hi, hi_i = h, i
            if l < lo:
                lo, lo_i = l, i
            if hi >= lo * (1 + pct) and hi_i > lo_i:
                add(lo_i, "L", lo); last = "L"
            elif lo <= hi * (1 - pct) and lo_i > hi_i:
                add(hi_i, "H", hi); last = "H"
        elif last == "L":
            if h > hi:
                hi, hi_i = h, i
            if l <= hi * (1 - pct):
                add(hi_i, "H", hi); last = "H"; lo, lo_i = l, i
        else:
            if l < lo:
                lo, lo_i = l, i
            if h >= lo * (1 + pct):
                add(lo_i, "L", lo); last = "L"; hi, hi_i = h, i
    if last == "L" and hi_i > out[-1]["i"]:
        out.append({"i": hi_i, "kind": "H", "price": hi, "date": bars[hi_i][0], "confirmed": False})
    elif last == "H" and lo_i > out[-1]["i"]:
        out.append({"i": lo_i, "kind": "L", "price": lo, "date": bars[lo_i][0], "confirmed": False})
    return out


def _ma(bars: list[Bar], k: int, end: int | None = None) -> float | None:
    end = len(bars) if end is None else end
    if end < k:
        return None
    return sum(b[3] for b in bars[end - k:end]) / k


NEAR_TRIGGER = 0.85                    # 候選：現價要在觸發價的 85% 以上（離突破還不太遠）


def _state(bars: list[Bar], trigger: float) -> str | None:
    """candidate：現價（最後收盤）還在觸發價之下、但離它不到 15%；breakout：最近 BREAKOUT_FRESH 根內剛站上；站上太久（已經跑掉）或離太遠回 None。"""
    close = bars[-1][3]
    if close <= trigger:
        return "candidate" if close >= trigger * NEAR_TRIGGER else None
    since = 0
    for b in reversed(bars):
        if b[3] > trigger:
            since += 1
        else:
            break
    return "breakout" if since <= BREAKOUT_FRESH else None


def _mk(name: str, state: str, trigger: float, stop: float, target: float, **extra: Any) -> dict[str, Any]:
    return {"name": name, "state": state, "trigger": round(trigger, 2), "stop": round(stop, 2), "target": round(target, 2), **extra}


def _prior_drop(pivs: list[dict[str, Any]], k: int) -> float | None:
    """第 k 個轉折（低點）前面那個高點到它的跌幅。"""
    if k < 1 or pivs[k - 1]["kind"] != "H":
        return None
    h, l = pivs[k - 1]["price"], pivs[k]["price"]
    return (h - l) / h if h > 0 else None


def detect_w(bars: list[Bar]) -> dict[str, Any] | None:
    pivs = zigzag(bars, ZIG["w"])
    n = len(bars)
    for k in range(len(pivs) - 1, 1, -1):
        l2, h, l1 = pivs[k], pivs[k - 1], pivs[k - 2]
        if not (l2["kind"] == "L" and h["kind"] == "H" and l1["kind"] == "L"):
            continue
        if n - 1 - l2["i"] > RECENT_BARS:
            break
        gap = l2["i"] - l1["i"]
        ratio = l2["price"] / l1["price"] - 1
        if not (W_GAP[0] <= gap <= W_GAP[1] and -W_LOW_DOWN <= ratio <= W_LOW_UP):
            continue
        low = min(l1["price"], l2["price"])
        if h["price"] < max(l1["price"], l2["price"]) * (1 + W_BOUNCE):
            continue
        drop = _prior_drop(pivs, k - 2)
        if drop is None or drop < PRIOR_DROP:
            continue
        if bars[-1][3] < l2["price"] * 0.99:      # 已經跌破右底，形態失敗
            continue
        st = _state(bars, h["price"])
        if st is None:
            continue
        return _mk("W底", st, h["price"], low * 0.98, h["price"] + (h["price"] - low), low1={"date": l1["date"], "price": l1["price"]},
                   low2={"date": l2["date"], "price": l2["price"]}, neck={"date": h["date"], "price": h["price"]}, heightPct=round((h["price"] / low - 1) * 100, 1))
    return None


def detect_hs(bars: list[Bar]) -> dict[str, Any] | None:
    pivs = zigzag(bars, ZIG["hs"])
    n = len(bars)
    for k in range(len(pivs) - 1, 3, -1):
        r, h2, hd, h1, lf = pivs[k], pivs[k - 1], pivs[k - 2], pivs[k - 3], pivs[k - 4]
        if [p["kind"] for p in (lf, h1, hd, h2, r)] != ["L", "H", "L", "H", "L"]:
            continue
        if n - 1 - r["i"] > RECENT_BARS:
            break
        span = r["i"] - lf["i"]
        if not (HS_SPAN[0] <= span <= HS_SPAN[1]):
            continue
        head, ls, rs = hd["price"], lf["price"], r["price"]
        if not (head <= ls * (1 - HS_HEAD) and head <= rs * (1 - HS_HEAD)):
            continue
        if abs(rs / ls - 1) > HS_SHOULDER or abs(h1["price"] / h2["price"] - 1) > HS_NECK:
            continue
        neck = max(h1["price"], h2["price"])
        if neck < head * 1.10:
            continue
        if bars[-1][3] < rs * 0.99:
            continue
        st = _state(bars, neck)
        if st is None:
            continue
        return _mk("頭肩底", st, neck, rs * 0.98, neck + (neck - head), left={"date": lf["date"], "price": ls}, head={"date": hd["date"], "price": head},
                   right={"date": r["date"], "price": rs}, heightPct=round((neck / head - 1) * 100, 1))
    return None


def detect_hl(bars: list[Bar]) -> dict[str, Any] | None:
    """C 不破 A 的低：低 A → 反彈 B → 低 C，C 明顯比 A 高（低點墊高），價在季線之上。"""
    pivs = zigzag(bars, ZIG["hl"])
    n = len(bars)
    ma60 = _ma(bars, 60)
    if ma60 is None or bars[-1][3] < ma60:
        return None
    for k in range(len(pivs) - 1, 1, -1):
        c, b, a = pivs[k], pivs[k - 1], pivs[k - 2]
        if not (c["kind"] == "L" and b["kind"] == "H" and a["kind"] == "L"):
            continue
        if n - 1 - c["i"] > RECENT_BARS:
            break
        if c["price"] < a["price"] * (1 + HL_HIGHER) or b["price"] < a["price"] * (1 + HL_BOUNCE):
            continue
        if not (10 <= c["i"] - a["i"] <= 90):
            continue
        if (b["price"] - c["price"]) / b["price"] > 0.30:        # C 的回檔太深就不算強
            continue
        if bars[-1][3] < c["price"] * 0.99:
            continue
        st = _state(bars, b["price"])
        if st is None:
            continue
        return _mk("C不破A低", st, b["price"], c["price"] * 0.98, b["price"] + (b["price"] - c["price"]), a={"date": a["date"], "price": a["price"]},
                   b={"date": b["date"], "price": b["price"]}, c={"date": c["date"], "price": c["price"]}, higherLowPct=round((c["price"] / a["price"] - 1) * 100, 1))
    return None


def detect_vcp(bars: list[Bar]) -> dict[str, Any] | None:
    pivs = zigzag(bars, ZIG["vcp"])
    n = len(bars)
    ma60, ma120 = _ma(bars, 60), _ma(bars, 120)
    ma60_prev = _ma(bars, 60, n - 20)
    if ma60 is None or ma60_prev is None or bars[-1][3] < ma60 or ma60 < ma60_prev:
        return None
    if ma120 is not None and ma60 < ma120 * 0.97:
        return None
    # 量縮：近 10 日均量 ≤ 近 50 日均量 × 0.95
    v10 = sum(b[4] for b in bars[-10:]) / 10
    v50 = sum(b[4] for b in bars[-50:]) / 50 if n >= 50 else 0
    if v50 <= 0 or v10 > v50 * 0.95:
        return None
    # 從最後一個低點往前，收集 (高, 低) 一組一組的回檔
    end = len(pivs) - 1
    while end >= 0 and pivs[end]["kind"] != "L":
        end -= 1
    if end < 1 or n - 1 - pivs[end]["i"] > RECENT_BARS:
        return None
    legs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    k = end
    while k >= 1 and pivs[k]["kind"] == "L" and pivs[k - 1]["kind"] == "H":
        legs.append((pivs[k - 1], pivs[k]))
        k -= 2
    legs.reverse()                                       # 舊 → 新
    depths = [(h["price"] - l["price"]) / h["price"] for h, l in legs]
    # 取最長的「越來越小」那一段結尾
    best = 0
    for start in range(len(depths) - 1):
        ok = 1
        for j in range(start + 1, len(depths)):
            if depths[j] <= depths[j - 1] * VCP_SHRINK and legs[j][1]["price"] >= legs[j - 1][1]["price"] * 0.99:
                ok += 1
            else:
                ok = 1
        best = max(best, ok)
    use = len(depths)
    while use >= 2:
        seg = depths[-use:]
        segl = legs[-use:]
        if all(seg[j] <= seg[j - 1] * VCP_SHRINK and segl[j][1]["price"] >= segl[j - 1][1]["price"] * 0.99 for j in range(1, use)):
            break
        use -= 1
    if use < 2:
        return None
    seg, segl = depths[-use:], legs[-use:]
    if not (VCP_FIRST[0] <= seg[0] <= VCP_FIRST[1]) or seg[-1] > VCP_LAST_MAX:
        return None
    last_h, last_l = segl[-1]
    if bars[-1][3] < last_l["price"] * 0.99:
        return None
    trigger = last_h["price"]
    st = _state(bars, trigger)
    if st is None:
        return None
    return _mk("VCP收斂", st, trigger, last_l["price"] * 0.97, trigger + (segl[0][0]["price"] - segl[0][1]["price"]) * 0.5,
               contractions=[round(d * 100, 1) for d in seg], volRatio=round(v10 / v50, 2), pivotDate=last_h["date"])


DETECTORS = (("vcp", detect_vcp), ("hl", detect_hl), ("w", detect_w), ("hs", detect_hs))


def detect_all(bars: list[Bar]) -> dict[str, dict[str, Any]]:
    """{'vcp'|'hl'|'w'|'hs': 形態}，沒有的不放。日K不到 MIN_BARS 根回空。"""
    if len(bars) < MIN_BARS:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, fn in DETECTORS:
        try:
            r = fn(bars)
        except Exception:  # noqa: BLE001
            r = None
        if r:
            out[key] = r
    return out


def payload(as_of: str | None = None, codes: list[str] | None = None) -> dict[str, Any]:
    """全部族群股（或指定代號）在 as_of（預設最新日K）那天的形態候選：只列至少符合一種形態的，每檔 patterns＝{vcp, hl, w, hs}。
    日K用 brew_launch 的載入（約 420 個日曆天）。給系統選股、醞釀／發動的形態標籤、盤中突破提醒用。"""
    from datetime import datetime, timedelta

    from brew_launch import _latest_bar_date, _load_bars
    from brew_launch_history import group_and_name
    from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

    as_of = as_of or _latest_bar_date()
    if not as_of:
        return {"status": "empty", "date": None, "reason": "還沒有日K資料"}
    if codes:
        wanted = sorted({str(c).strip().upper() for c in codes if str(c).strip()})
    else:
        wanted = sorted({str(c).strip().upper() for g, members in STOCK_GROUPS.items() if g not in SPECIAL_GROUP_NAMES for c, _n in members})
    session = (datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    bars_by_code = _load_bars(wanted, session=session)
    rows: list[dict[str, Any]] = []
    counts = {"vcp": 0, "hl": 0, "w": 0, "hs": 0}
    for code in wanted:
        bars = [b for b in bars_by_code.get(code, []) if b[0] <= as_of]
        if len(bars) < MIN_BARS or bars[-1][0] != as_of:
            continue
        found = detect_all(bars)
        if not found:
            continue
        group, name = group_and_name(code)
        for k in found:
            counts[k] += 1
            found[k]["distPct"] = round((found[k]["trigger"] / bars[-1][3] - 1) * 100, 1)
        rows.append({"code": code, "name": name, "group": group, "close": bars[-1][3], "patterns": found})
    rows.sort(key=lambda r: (min(p["distPct"] for p in r["patterns"].values()), r["code"]))
    return {"status": "ok", "date": as_of, "counts": counts, "rows": rows,
            "rule": {"zigzag": ZIG, "nearTrigger": NEAR_TRIGGER, "breakoutFresh": BREAKOUT_FRESH}}
