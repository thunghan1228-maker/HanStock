"""資金保護傘（2026-10-10 使用者：照莊爸課程裡講的「融資水位＋加權／櫃買多空轉折」做成一頁）。

資料：FinMind 公開資料（免登入）——上市融資融券總餘額（TaiwanStockTotalMarginPurchaseShortSale，證交所每天晚上公布），
加權指數 TAIEX、櫃買指數 TPEx 日K（TaiwanStockPrice），從 2020 年起。每天晚上 21:40 以後更新、存在 SQLite。

看三件事：
1. 融資水位：上市融資餘額（億）、日／週／月增減、三年百分位，對照過去幾次崩盤前的融資高點還差多少。
2. 多空轉折：加權、櫃買各自對月線（20 日）、季線（60 日）的位置；明天要守的月線價、季線價（扣抵算好的），
   最近一次站上／跌破月線是哪天。
3. 背離：近 5 日指數漲跌跟融資增減的方向（指數跌、融資反增＝散戶接刀）。
三件事合成「保護傘」：每一個轉弱的地方記一點，0 點收傘、1 點備傘、2 點半開、3 點以上全開。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
HISTORY_START = "2020-01-01"
REFETCH_DAYS = 15            # 每次重抓最近幾天（補前一晚還沒出來的）
UPDATE_AFTER = (21, 40)      # 證交所融資餘額晚上才出來
POLL_SECONDS = 20 * 60
CRASH_DROP = 0.15            # 從近半年高點跌 15% 以上算一次崩盤
CRASH_LOOKBACK = 120         # 「近半年高點」＝往回 120 個交易日
CRASH_RECOVER = 0.92         # 回到高點的 92% 以上，這一次崩盤就算結束
PEAK_WINDOW = (20, 5)        # 崩盤前融資高點：指數高點前 20 天～後 5 天的最大值
PERCENTILE_DAYS = 750        # 三年百分位
CHART_DAYS = 250
HOT_MONTH_PCT = 8.0          # 融資一個月（20 日）增加 8% 以上＝過熱
NEAR_HIGH_PCT = 3.0          # 離歷史（2020 起）融資最高不到 3%＝高檔

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "lastFetch": None, "lastError": None, "rows": 0}


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS fund_umbrella_series (series TEXT NOT NULL, trade_date TEXT NOT NULL, value REAL NOT NULL, "
        "PRIMARY KEY (series, trade_date))"
    )


def _now() -> datetime:
    return datetime.now(TW_TZ)


def _default_fetcher(params: dict[str, str]) -> Any:
    def call(extra: dict[str, str]) -> Any:
        url = FINMIND_URL + "?" + urllib.parse.urlencode({**params, **extra})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.load(response)

    try:
        return call({})                       # 這兩個資料集免登入就能抓
    except Exception:  # noqa: BLE001
        token = os.getenv("FINMIND_TOKEN", "").strip()
        if not token:
            raise
        return call({"token": token})


def parse_margin(payload: Any) -> dict[str, dict[str, float]]:
    """融資金額（億）、融資張數、融券張數。"""
    out: dict[str, dict[str, float]] = {"margin": {}, "margin_units": {}, "short_units": {}}
    names = {"MarginPurchaseMoney": ("margin", 1e8), "MarginPurchase": ("margin_units", 1.0), "ShortSale": ("short_units", 1.0)}
    for r in (payload or {}).get("data") or []:
        hit = names.get(r.get("name"))
        if hit and r.get("date") and r.get("TodayBalance") is not None:
            out[hit[0]][r["date"]] = float(r["TodayBalance"]) / hit[1]
    return out


def parse_index(payload: Any) -> dict[str, float]:
    return {r["date"]: float(r["close"]) for r in (payload or {}).get("data") or [] if r.get("date") and r.get("close")}


def _last_date(connection, series: str) -> str | None:
    row = connection.execute("SELECT MAX(trade_date) AS d FROM fund_umbrella_series WHERE series = ?", (series,)).fetchone()
    return row["d"] if row else None


def fetch(*, fetcher: Callable[[dict[str, str]], Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    call = fetcher or _default_fetcher
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        last = _last_date(connection, "margin")
    start = HISTORY_START if not last else (date.fromisoformat(last) - timedelta(days=REFETCH_DAYS)).isoformat()
    got: dict[str, dict[str, float]] = parse_margin(call({"dataset": "TaiwanStockTotalMarginPurchaseShortSale", "start_date": start}))
    got["taiex"] = parse_index(call({"dataset": "TaiwanStockPrice", "data_id": "TAIEX", "start_date": start}))
    got["tpex"] = parse_index(call({"dataset": "TaiwanStockPrice", "data_id": "TPEx", "start_date": start}))
    if not got["margin"] or not got["taiex"]:
        raise RuntimeError("融資餘額或加權指數抓到 0 筆")
    rows = [(series, d, v) for series, values in got.items() for d, v in values.items()]
    with get_connection() as connection:
        _schema(connection)
        connection.executemany("INSERT INTO fund_umbrella_series (series, trade_date, value) VALUES (?, ?, ?) "
                               "ON CONFLICT(series, trade_date) DO UPDATE SET value = excluded.value", rows)
    with _lock:
        _state.update({"lastFetch": now.isoformat(timespec="seconds"), "lastError": None, "rows": len(rows)})
    return {"rows": len(rows), "from": start, "latest": max(got["margin"])}


def _load() -> dict[str, dict[str, float]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT series, trade_date, value FROM fund_umbrella_series ORDER BY trade_date").fetchall()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["series"], {})[r["trade_date"]] = r["value"]
    return out


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _pct(a: float | None, b: float | None, digits: int = 2) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round((a / b - 1) * 100, digits)


def crashes(dates: list[str], closes: list[float], margin: dict[str, float]) -> list[dict[str, Any]]:
    """從近半年高點跌 15% 以上的每一次崩盤：高點、低點、跌幅、天數，以及崩盤前的融資高點、低點時融資剩多少。"""
    out: list[dict[str, Any]] = []
    i, n = 0, len(closes)
    while i < n:
        lo = max(0, i - CRASH_LOOKBACK + 1)
        peak_i = max(range(lo, i + 1), key=lambda k: closes[k])
        if closes[i] > closes[peak_i] * (1 - CRASH_DROP):
            i += 1
            continue
        trough_i, j = i, i
        while j < n and closes[j] < closes[peak_i] * CRASH_RECOVER:
            if closes[j] < closes[trough_i]:
                trough_i = j
            j += 1
        window = [dates[k] for k in range(max(0, peak_i - PEAK_WINDOW[0]), min(n, peak_i + PEAK_WINDOW[1] + 1)) if dates[k] in margin]
        m_peak_day = max(window, key=lambda d: margin[d]) if window else None
        m_trough = margin.get(dates[trough_i])
        out.append({
            "peakDate": dates[peak_i], "peak": round(closes[peak_i], 2), "troughDate": dates[trough_i], "trough": round(closes[trough_i], 2),
            "drop": _pct(closes[trough_i], closes[peak_i], 1), "days": trough_i - peak_i, "recovered": j < n,
            "marginPeakDate": m_peak_day, "marginPeak": round(margin[m_peak_day], 1) if m_peak_day else None,
            "marginTrough": round(m_trough, 1) if m_trough is not None else None,
            "marginDrop": _pct(m_trough, margin[m_peak_day], 1) if m_peak_day and m_trough is not None else None,
        })
        i = j
    return out


def index_state(dates: list[str], closes: list[float]) -> dict[str, Any] | None:
    """對月線、季線的位置；明天要守的月線價／季線價（扣抵算好）；最近一次站上／跌破月線。"""
    if len(closes) < 61:
        return None
    c = closes[-1]
    ma5, ma20, ma60 = _mean(closes[-5:]), _mean(closes[-20:]), _mean(closes[-60:])
    ma20_prev = _mean(closes[-21:-1])
    above20 = [closes[k] >= _mean(closes[k - 19:k + 1]) for k in range(len(closes) - 120, len(closes)) if k >= 19]
    turn_days = 0
    for flag in reversed(above20):
        if flag != above20[-1]:
            break
        turn_days += 1
    if c >= ma20 and ma20 >= ma60 and ma20 >= ma20_prev:
        state, tone = "多頭", "bull"
    elif c >= ma20:
        state, tone = "偏多", "bull"
    elif c >= ma60:
        state, tone = "整理（跌破月線）", "warn"
    else:
        state, tone = "偏空（跌破季線）", "bear"
    return {
        "date": dates[-1], "close": round(c, 2), "change": _pct(c, closes[-2]), "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma20Up": ma20 >= ma20_prev, "vs20": _pct(c, ma20), "vs60": _pct(c, ma60), "state": state, "tone": tone,
        "hold20": round(_mean(closes[-19:]), 2),      # 明天收盤 ≥ 這個價＝還在（明天的）月線上
        "hold60": round(_mean(closes[-59:]), 2),
        "deduct20": round(closes[-20], 2),            # 明天月線扣掉的那一天：明天收在它之上，月線就往上
        "above20": above20[-1], "streak": turn_days,
        "turnDate": dates[-turn_days] if turn_days < len(above20) else None,
    }


def payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    data = _load()
    margin, taiex, tpex = data.get("margin", {}), data.get("taiex", {}), data.get("tpex", {})
    days = sorted(set(margin) & set(taiex))
    if len(days) < 61:
        return {"status": "missing", "message": "資料還沒抓齊（後端剛部署時約 2 分鐘後會抓第一次）"}
    as_of = days[-1]
    t_dates = sorted(d for d in taiex if d <= as_of)
    t_close = [taiex[d] for d in t_dates]
    o_dates = sorted(d for d in tpex if d <= as_of)
    o_close = [tpex[d] for d in o_dates]
    m_dates = sorted(d for d in margin if d <= as_of)
    m_vals = [margin[d] for d in m_dates]
    cur = m_vals[-1]

    def back(k: int) -> float | None:
        return m_vals[-1 - k] if len(m_vals) > k else None

    hist = m_vals[-PERCENTILE_DAYS:]
    top_i = max(range(len(m_vals)), key=lambda k: m_vals[k])
    units, shorts = data.get("margin_units", {}), data.get("short_units", {})
    m_info = {
        "date": as_of, "balance": round(cur, 1),
        "d1": round(cur - back(1), 1) if back(1) else None, "d5": round(cur - back(5), 1) if back(5) else None,
        "d20": round(cur - back(20), 1) if back(20) else None, "d20Pct": _pct(cur, back(20)),
        "percentile": round(sum(1 for v in hist if v <= cur) / len(hist) * 100),
        "high": round(m_vals[top_i], 1), "highDate": m_dates[top_i], "vsHigh": _pct(cur, m_vals[top_i]),
        "shortRatio": round(shorts[as_of] / units[as_of] * 100, 2) if units.get(as_of) and as_of in shorts else None,
        "shortUnits": round(shorts[as_of]) if as_of in shorts else None,
    }
    crash_list = crashes(t_dates, t_close, margin)
    for c in crash_list:
        c["gap"] = round(cur - c["marginPeak"], 1) if c["marginPeak"] else None
        c["gapPct"] = _pct(cur, c["marginPeak"], 1) if c["marginPeak"] else None
    tx, otc = index_state(t_dates, t_close), index_state(o_dates, o_close) if len(o_close) > 60 else None

    # 背離：近 5 日指數漲跌 vs 融資增減
    t5, m5 = _pct(t_close[-1], t_close[-6]) if len(t_close) > 5 else None, _pct(cur, back(5))
    if t5 is None or m5 is None:
        diverge = None
    elif t5 < 0 and m5 > 0:
        diverge = {"tone": "bear", "text": f"近 5 日加權 {t5:+.2f}%、融資反而 {m5:+.2f}%：指數跌、散戶進場接刀，籌碼變亂"}
    elif t5 > 0 and m5 < 0:
        diverge = {"tone": "bull", "text": f"近 5 日加權 {t5:+.2f}%、融資 {m5:+.2f}%：指數漲、融資減，籌碼乾淨，上漲比較健康"}
    elif t5 > 0 and m5 > t5:
        diverge = {"tone": "warn", "text": f"近 5 日加權 {t5:+.2f}%、融資 {m5:+.2f}%：融資增加得比指數快，追價資金變多"}
    elif t5 < 0 and m5 < 0:
        diverge = {"tone": "warn", "text": f"近 5 日加權 {t5:+.2f}%、融資 {m5:+.2f}%：融資跟著退場，籌碼在清洗"}
    else:
        diverge = {"tone": "bull", "text": f"近 5 日加權 {t5:+.2f}%、融資 {m5:+.2f}%：融資跟著指數小幅增加，正常"}

    reasons: list[dict[str, Any]] = []
    if tx and not tx["above20"]:
        reasons.append({"pt": 1, "text": f"加權跌破月線（{tx['ma20']:,.0f}）"})
    if tx and tx["close"] < tx["ma60"]:
        reasons.append({"pt": 1, "text": f"加權跌破季線（{tx['ma60']:,.0f}）"})
    if otc and not otc["above20"]:
        reasons.append({"pt": 1, "text": f"櫃買跌破月線（{otc['ma20']:,.2f}）"})
    if m_info["d20Pct"] is not None and m_info["d20Pct"] >= HOT_MONTH_PCT:
        reasons.append({"pt": 1, "text": f"融資一個月增加 {m_info['d20Pct']:.1f}%（≥{HOT_MONTH_PCT:g}% 過熱）"})
    if m_info["vsHigh"] is not None and m_info["vsHigh"] >= -NEAR_HIGH_PCT and (tx is None or not tx["above20"]):
        reasons.append({"pt": 1, "text": "融資在 2020 年以來最高檔附近，指數卻跌破月線"})
    if diverge and diverge["tone"] == "bear":
        reasons.append({"pt": 1, "text": "指數跌、融資反增（散戶接刀）"})
    score = sum(r["pt"] for r in reasons)
    levels = [("收傘", "☀️", "bull", "多頭環境，正常操作；守住個股自己的防守價就好"),
              ("備傘", "🌤", "warn", "有一個地方轉弱，先別加碼，留意明天要守的價位"),
              ("半開", "🌂", "warn", "兩個地方轉弱：降低持股、只留強勢股，新單縮小"),
              ("全開", "☔", "bear", "多處轉弱：防守為主、現金為王，等指數重新站回月線")]
    name, icon, tone, advice = levels[min(score, 3)]

    chart_from = t_dates[-CHART_DAYS] if len(t_dates) >= CHART_DAYS else t_dates[0]
    series = [{"d": d, "t": round(taiex[d], 2), "o": round(tpex[d], 2) if d in tpex else None, "m": round(margin[d], 1) if d in margin else None}
              for d in t_dates if d >= chart_from]
    return {
        "status": "ok", "asOf": as_of, "updatedAt": _state.get("lastFetch"), "now": now.isoformat(timespec="seconds"),
        "umbrella": {"score": score, "name": name, "icon": icon, "tone": tone, "advice": advice, "reasons": reasons},
        "margin": m_info, "taiex": tx, "tpex": otc, "diverge": diverge, "crashes": crash_list, "series": series,
        "rule": (f"保護傘點數：加權跌破月線、跌破季線、櫃買跌破月線、融資月增 ≥{HOT_MONTH_PCT:g}%、融資在高檔但加權跌破月線、"
                 "近 5 日指數跌融資反增，各記 1 點；0 點收傘、1 點備傘、2 點半開、3 點以上全開。"
                 f"崩盤＝從近半年高點跌 {CRASH_DROP * 100:g}% 以上；崩盤前融資高點＝指數高點前 20 天到後 5 天的融資最大值。"),
        "source": "FinMind 公開資料（證交所上市融資融券餘額、加權指數、櫃買指數），每天晚上更新；上櫃融資之後補",
    }


def due(now: datetime, last_fetch: str | None) -> bool:
    """每天 21:40 以後抓一次（週末也抓一次無妨，抓到的會是週五的）。"""
    if (now.hour, now.minute) < UPDATE_AFTER:
        return last_fetch is None
    return last_fetch is None or last_fetch[:10] < now.date().isoformat() or last_fetch[11:16] < f"{UPDATE_AFTER[0]:02d}:{UPDATE_AFTER[1]:02d}"


def _loop() -> None:
    time.sleep(120)
    while True:
        try:
            if due(_now(), _state.get("lastFetch")):
                fetch()
        except Exception as exc:  # noqa: BLE001
            logger.warning("fund umbrella fetch failed: %s", exc)
            with _lock:
                _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
                _state["lastFetch"] = _state.get("lastFetch")
        time.sleep(POLL_SECONDS)


def start_fund_umbrella_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="fund-umbrella", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)
