"""個股 5 分 K 本機持久化：讓五分鐘K訊號的 MA20 跨日接續。

五分鐘K訊號監視器（intraday_kline_signals）的 state.closes 原本每天從零開始累積：
5 分 K MA20 要 20 根、MA20 斜率再多 1 根，也就是 10:45 之後才算得出來。12空（一二空）
第③步破位要「20MA 正在下彎」，10:45 前根本不可能成立，跟一般看盤軟體的 5 分 K MA20
（跨日連續，09:10 就有值、有斜率）不一樣；2026-09-23 盤中一二空整天是 0 就是這個原因。

這裡把每根走完的個股 5 分 K 存進既有的 bars_5m 表（跟櫃買指數 OTC_INDEX 共用同一張表、
同一種 bar_time 格式），隔天開盤前把昨天最後幾根種進 state，MA20／斜率從當天第一根起
就跟看盤軟體一致。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from database import get_connection, initialize_database, save_bars
from otc_index import OTC_INDEX_HUB_CODE, TW_TZ, is_regular_otc_session

# 只留最近幾個日曆天：660 檔 × 每天 54 根，留太久資料庫會一直長；種子只要昨天那幾根。
KEEP_CALENDAR_DAYS = 15


def _bar_row(bar: dict[str, Any]) -> dict[str, Any] | None:
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
        return None
    if not is_regular_otc_session(ts) or min(row["open"], row["high"], row["low"], row["close"]) <= 0:
        return None
    return row


def save_stock_bars_5m(code: str, bars: list[dict[str, Any]]) -> int:
    """存一檔股票的 5 分 K（bar 用 ts 毫秒＋OHLCV，跟 hub / kbars 同格式）；回傳存了幾根。"""
    code = str(code).strip().upper()
    if not code or code == OTC_INDEX_HUB_CODE:
        return 0
    rows = [row for row in (_bar_row(bar) for bar in bars) if row is not None]
    if not rows:
        return 0
    initialize_database()
    return save_bars("bars_5m", code, rows)


def save_stock_bars_5m_many(bars_by_code: dict[str, list[dict[str, Any]]]) -> int:
    """多檔股票一次寫進同一個交易：即時路徑每 5 分鐘一百多檔同時走完一根 K，
    不要在 tick 執行緒上每檔各開一次交易。"""
    rows: list[tuple[Any, ...]] = []
    for code, bars in bars_by_code.items():
        code = str(code).strip().upper()
        if not code or code == OTC_INDEX_HUB_CODE:
            continue
        for bar in bars:
            row = _bar_row(bar)
            if row is None:
                continue
            rows.append((
                code, row["time"].isoformat(), row["open"], row["high"], row["low"], row["close"], row["volume"],
            ))
    if not rows:
        return 0
    initialize_database()
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO bars_5m (stock_code, bar_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, bar_time) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )
    return len(rows)


def load_stock_bars_5m_before(code: str, trade_date: str, limit: int) -> list[dict[str, Any]]:
    """回傳 trade_date 之前、最後 limit 根已存的 5 分 K（bar-start ts 由小到大）。"""
    code = str(code).strip().upper()
    if not code or int(limit) <= 0:
        return []
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT bar_time, open, high, low, close, volume
               FROM bars_5m
               WHERE stock_code = ? AND bar_time < ?
               ORDER BY bar_time DESC
               LIMIT ?""",
            (code, str(trade_date)[:10], int(limit)),
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
        })
    bars.reverse()
    return bars


def prune_stock_bars_5m(keep_calendar_days: int = KEEP_CALENDAR_DAYS, today: str | None = None) -> int:
    """刪掉個股太舊的 5 分 K；櫃買指數 OTC_INDEX 那些由 otc_index_store 自己管，不動。"""
    today_date = date.fromisoformat(str(today)[:10]) if today else datetime.now(TW_TZ).date()
    cutoff = (today_date - timedelta(days=max(1, int(keep_calendar_days)))).isoformat()
    initialize_database()
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM bars_5m WHERE stock_code != ? AND bar_time < ?",
            (OTC_INDEX_HUB_CODE, cutoff),
        )
        return int(cursor.rowcount or 0)
