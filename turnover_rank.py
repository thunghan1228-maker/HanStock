"""週轉率排行（2026-11-19 使用者：首頁強中強下面加「週轉率高」，每天前 20 檔——當沖最愛的高週轉率股票）。

週轉率＝今天成交量 ÷ 已發行股數 × 100%。
- 成交量：證交所 MIS 即時報價（全市場一般個股，跟飆股雷達同一個抓法 grail_radar.fetch_live_bars），
  盤中每 3 分鐘重抓一次（全市場約 25 次請求，抓太勤會被證交所擋），盤外 15 分鐘。收盤後 MIS 回的就是今天全天。
- 股數：fundamentals_daily 的 stock_shares（上市證交所 t187ap03、上櫃鏡像，每天更新）。
- MIS 抓不到、或回的檔數太少（例如開盤前試撮還沒成交）就用最近一個交易日的官方日K（bars_1d）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from fundamentals_daily import shares_map
from grail_radar import _universe, fetch_live_bars
from trading_days import is_trading_day

logger = logging.getLogger("hanstock.turnover_rank")
TW_TZ = timezone(timedelta(hours=8))
MAX_TOP = 50
LIVE_TTL_SECONDS = 180      # 盤中：3 分鐘重抓一次
IDLE_TTL_SECONDS = 900      # 盤外：15 分鐘
MIN_LIVE_QUOTES = 300       # MIS 回的檔數少於這個就當今天還沒開始，改用官方日K

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": None, "payload": None}


def _now() -> datetime:
    return datetime.now(TW_TZ)


def _session_open(now: datetime) -> bool:
    if not is_trading_day(now.date()):
        return False
    return dt_time(8, 55) <= now.time().replace(tzinfo=None) <= dt_time(13, 40)


def _rank(universe: dict[str, dict[str, str]], quotes: dict[str, dict[str, Any]], shares: dict[str, int]) -> list[dict[str, Any]]:
    """quotes：{代號: {close, high, low, prevClose, volume(張)}}；回照週轉率排的全部列（大到小）。"""
    rows = []
    for code, q in quotes.items():
        info, count = universe.get(code), shares.get(code)
        volume = float(q.get("volume") or 0)
        if not info or not count or count <= 0 or volume <= 0:
            continue
        price, prev, high, low = q.get("close"), q.get("prevClose"), q.get("high"), q.get("low")
        rows.append({
            "code": code, "name": info["name"], "market": info["market"],
            "price": round(float(price), 2) if price else None,
            "chg": round((float(price) / float(prev) - 1) * 100, 2) if price and prev else None,
            "volume": int(round(volume)),                                # 張
            "turnover": round(volume * 1000 / count * 100, 2),           # %
            "sharesLots": int(round(count / 1000)),
            "amp": round((float(high) - float(low)) / float(prev) * 100, 2) if high and low and prev else None,   # 振幅 %（券商「高週轉振幅」）
        })
    rows.sort(key=lambda r: (-r["turnover"], r["code"]))
    return rows


def _latest_bars(codes: set[str]) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """最近一個交易日的官方日K：(日期, {代號: {close, high, low, prevClose, volume}})。"""
    initialize_database()
    since = (_now().date() - timedelta(days=20)).isoformat()
    series: dict[str, list[tuple[str, float, float, float, float]]] = {}
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT stock_code, substr(bar_time, 1, 10) AS d, high, low, close, volume FROM bars_1d WHERE bar_time >= ? ORDER BY stock_code, bar_time",
            (since,),
        )
        for row in rows:
            code = str(row["stock_code"]).strip().upper()
            if code in codes:
                series.setdefault(code, []).append((str(row["d"]), float(row["close"]), float(row["volume"] or 0), float(row["high"]), float(row["low"])))
    day = max((bars[-1][0] for bars in series.values() if bars), default=None)
    out = {}
    for code, bars in series.items():
        if bars[-1][0] != day:
            continue
        out[code] = {"close": bars[-1][1], "high": bars[-1][3], "low": bars[-1][4], "prevClose": bars[-2][1] if len(bars) > 1 else None, "volume": bars[-1][2]}
    return day, out


def build(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    now = now or _now()
    universe = _universe()
    shares = shares_map(sorted(universe))
    if not shares:
        return {"status": "empty", "message": "還沒有已發行股數資料", "rows": [], "updatedAt": now.isoformat(timespec="seconds")}
    wanted = {code: universe[code] for code in universe if code in shares}
    source, day, as_of, quotes = "mis", None, None, {}
    try:
        live = fetch_live_bars(wanted, fetcher=fetcher)
        days = Counter(bar.get("date") for bar in live.values() if bar.get("date"))
        if days:
            day = days.most_common(1)[0][0]
            quotes = {code: bar for code, bar in live.items() if bar.get("date") == day}
            as_of = max((str(bar.get("time") or "") for bar in quotes.values()), default="") or None
    except Exception:  # noqa: BLE001
        logger.exception("週轉率排行：MIS 即時報價抓不到，改用官方日K")
        quotes = {}
    if len(quotes) < MIN_LIVE_QUOTES:
        source, as_of = "bars", None
        day, quotes = _latest_bars(set(wanted))
    rows = _rank(universe, quotes, shares)
    return {
        "status": "ok" if rows else "empty", "date": day, "source": source, "asOf": as_of,
        "session": _session_open(now), "universe": len(wanted), "quoted": len(quotes),
        "updatedAt": now.isoformat(timespec="seconds"), "rows": rows[:MAX_TOP],
    }


def turnover_rank(top: int = 20, *, now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """週轉率前 top 名（快取：盤中 3 分鐘、盤外 15 分鐘；同一時間只抓一次）。"""
    now = now or _now()
    top = max(1, min(MAX_TOP, int(top)))
    with _lock:
        ttl = LIVE_TTL_SECONDS if _session_open(now) else IDLE_TTL_SECONDS
        cached, at = _cache["payload"], _cache["at"]
        if cached is None or at is None or time.monotonic() - at >= ttl:
            cached = build(now, fetcher)
            _cache.update({"payload": cached, "at": time.monotonic()})
    payload = dict(cached)
    payload["rows"] = cached["rows"][:top]
    payload["top"] = top
    payload["refreshSeconds"] = LIVE_TTL_SECONDS if payload.get("session") else IDLE_TTL_SECONDS
    return payload
