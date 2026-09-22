"""櫃買指數 5 分 K 本機持久化。

OtcIndexHub 只活在記憶體裡：每次重新部署、或跨日 rollover 之後，MA20 需要的
歷史 5 分 K 都得重新跟 Shioaji kbars 要。kbars 一旦失敗（最常見是當日歷史流量
額度被個股回補用完），櫃買盤勢就整天卡在「資料蒐集中」。這裡把已完成的
5 分 K 存進既有的 bars_5m 表（stock_code 用 OTC_INDEX），bootstrap 時跟 kbars
結果合併，kbars 拿不到也還有本機資料可以撐住 MA20。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from database import get_connection, initialize_database, save_bars
from otc_index import OTC_INDEX_HUB_CODE, TW_TZ, is_regular_otc_session


def save_index_bars_5m(bars: list[dict[str, Any]]) -> int:
    rows = []
    for bar in bars:
        try:
            ts = int(bar["ts"])
            row = {
                "time": datetime.fromtimestamp(ts / 1000, TW_TZ),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": max(0, int(bar.get("volume", 0) or 0)),
            }
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not is_regular_otc_session(ts) or min(row["open"], row["high"], row["low"], row["close"]) <= 0:
            continue
        rows.append(row)
    if not rows:
        return 0
    initialize_database()
    return save_bars("bars_5m", OTC_INDEX_HUB_CODE, rows)


def load_index_bars_5m(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """回傳 start_date~end_date（含）之間已存的 5 分 K，bar-start ts 由小到大。"""
    end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT bar_time, open, high, low, close, volume
               FROM bars_5m
               WHERE stock_code = ? AND bar_time >= ? AND bar_time < ?
               ORDER BY bar_time""",
            (OTC_INDEX_HUB_CODE, start_date, end_exclusive),
        ).fetchall()
    bars: list[dict[str, Any]] = []
    for row in rows:
        try:
            moment = datetime.fromisoformat(str(row["bar_time"]))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=TW_TZ)
        bars.append({
            "ts": int(moment.timestamp() * 1000),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "tick_count": 1,
        })
    return bars
