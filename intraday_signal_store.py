"""HanStock 盤中 5 分鐘訊號的 Railway SQLite 永久保存層。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from database import get_connection

ONCE_PER_DAY_KINDS = {
    "break905d",
    "a8short",
    "firstCross905High",
    "firstCrossUp20ma",
    "firstCrossDown20ma",
    "short12",
    "combo12Bull",
    "oneTwoShort",
    "blackDragon",
    "triangleNearBreakout",
    "triangleBreakoutPendingVolume",
    "triangleVolumeBreakout",
    "fourGateBuy",
    "fourGateSell",
    "mainForceFlipBull",
    "mainForceFlipBear",
}
ONCE_PER_BAR_KINDS = {"daytradeEarlySell50", "daytradeEarlyBuy50"}
INSTANT_LARGE_KINDS = {"instantLargeBuy", "instantLargeSell"}
EARLY_SIGNAL_COOLDOWN_MS = 5 * 60 * 1000


def _ensure_table() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS intraday_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                bar_ts INTEGER NOT NULL,
                price REAL NOT NULL,
                ma20_down INTEGER,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (trade_date, ticker, kind, bar_ts, note)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_date_ts
            ON intraday_signals (trade_date, bar_ts DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_ticker_date_ts
            ON intraday_signals (ticker, trade_date, bar_ts ASC, id ASC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_intraday_signals_kind_date
            ON intraday_signals (kind, trade_date, bar_ts DESC)
            """
        )


def _clean_signal(raw: dict[str, Any]) -> dict[str, Any] | None:
    try:
        trade_date = str(raw.get("tradeDate") or raw.get("trade_date") or "").strip()
        datetime.strptime(trade_date, "%Y-%m-%d")
        ticker = str(raw.get("ticker") or "").strip()[:16]
        name = str(raw.get("name") or ticker).strip()[:80]
        group_name = str(raw.get("groupName") or raw.get("group_name") or "").strip()[:80]
        kind = str(raw.get("kind") or "").strip()[:64]
        label = str(raw.get("label") or kind).strip()[:160]
        bar_ts = int(raw.get("barTs") or raw.get("bar_ts") or 0)
        price = float(raw.get("price"))
        note_raw = raw.get("note")
        note = "" if note_raw is None else str(note_raw).strip()[:160]
        ma20_raw = raw.get("ma20Down") if "ma20Down" in raw else raw.get("ma20_down")
        ma20_down = None if ma20_raw is None else (1 if bool(ma20_raw) else 0)
    except (TypeError, ValueError):
        return None
    if not ticker or not kind or bar_ts <= 0 or price <= 0:
        return None
    return {
        "trade_date": trade_date,
        "ticker": ticker,
        "name": name or ticker,
        "group_name": group_name,
        "kind": kind,
        "label": label or kind,
        "bar_ts": bar_ts,
        "price": price,
        "ma20_down": ma20_down,
        "note": note,
    }


def _resolve_stock_name(ticker: str) -> str:
    """全市場 stocks 表反查中文股名；查不到才退回代號。"""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT stock_name FROM stocks WHERE stock_code = ?",
            (ticker,),
        ).fetchone()
    return row["stock_name"] if row and row["stock_name"] else ticker


def _to_api(row: Any) -> dict[str, Any]:
    ticker = str(row["ticker"])
    stored_name = str(row["name"])
    # 讀取時重新反查股名，不只信任寫入當下存的值：這樣即使某筆訊號是
    # 在股名反查邏輯修好之前就已經寫入（代號當名稱存進去），現在讀出來
    # 也會自動修正，不用手動回補舊資料。
    name = stored_name if stored_name and stored_name != ticker else _resolve_stock_name(ticker)
    return {
        "tradeDate": str(row["trade_date"]),
        "ticker": ticker,
        "name": name,
        "groupName": str(row["group_name"]),
        "kind": str(row["kind"]),
        "label": str(row["label"]),
        "barTs": int(row["bar_ts"]),
        "price": float(row["price"]),
        "ma20Down": None if row["ma20_down"] is None else bool(row["ma20_down"]),
        "note": str(row["note"]) if str(row["note"]) else None,
    }


def save_intraday_signals(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    _ensure_table()
    inserted: list[dict[str, Any]] = []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_connection() as connection:
        for raw in rows:
            signal = _clean_signal(raw)
            if signal is None:
                continue
            if signal["kind"] in ONCE_PER_DAY_KINDS:
                exists = connection.execute(
                    """
                    SELECT id FROM intraday_signals
                    WHERE trade_date = ? AND ticker = ? AND kind = ?
                    LIMIT 1
                    """,
                    (signal["trade_date"], signal["ticker"], signal["kind"]),
                ).fetchone()
                if exists is not None:
                    continue
            if signal["kind"] in ONCE_PER_BAR_KINDS:
                exists = connection.execute(
                    """
                    SELECT id FROM intraday_signals
                    WHERE trade_date = ? AND ticker = ?
                      AND kind IN ('daytradeEarlySell50', 'daytradeEarlyBuy50')
                      AND bar_ts > ? AND bar_ts < ?
                    LIMIT 1
                    """,
                    (
                        signal["trade_date"], signal["ticker"],
                        signal["bar_ts"] - EARLY_SIGNAL_COOLDOWN_MS,
                        signal["bar_ts"] + EARLY_SIGNAL_COOLDOWN_MS,
                    ),
                ).fetchone()
                if exists is not None:
                    continue
            if signal["kind"] in INSTANT_LARGE_KINDS:
                exists = connection.execute(
                    """
                    SELECT id FROM intraday_signals
                    WHERE trade_date = ? AND ticker = ? AND kind = ?
                      AND bar_ts > ? AND bar_ts < ?
                    LIMIT 1
                    """,
                    (
                        signal["trade_date"], signal["ticker"], signal["kind"],
                        signal["bar_ts"] - EARLY_SIGNAL_COOLDOWN_MS,
                        signal["bar_ts"] + EARLY_SIGNAL_COOLDOWN_MS,
                    ),
                ).fetchone()
                if exists is not None:
                    continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO intraday_signals (
                    trade_date, ticker, name, group_name, kind, label,
                    bar_ts, price, ma20_down, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["trade_date"], signal["ticker"], signal["name"],
                    signal["group_name"], signal["kind"], signal["label"],
                    signal["bar_ts"], signal["price"], signal["ma20_down"],
                    signal["note"], now,
                ),
            )
            if cursor.rowcount:
                inserted.append({
                    "tradeDate": signal["trade_date"],
                    "ticker": signal["ticker"],
                    "name": signal["name"],
                    "groupName": signal["group_name"],
                    "kind": signal["kind"],
                    "label": signal["label"],
                    "barTs": signal["bar_ts"],
                    "price": signal["price"],
                    "ma20Down": None if signal["ma20_down"] is None else bool(signal["ma20_down"]),
                    "note": signal["note"] or None,
                })
    return inserted


def load_latest_signals(
    trade_date: str,
    limit: int = 20,
    market_only: bool = False,
    *,
    include_chart_kinds: bool = False,
) -> list[dict[str, Any]]:
    _ensure_table()
    # 活躍盤勢中一日全部訊號種類加起來可能遠超過200筆。跟
    # load_latest_signals_by_kind同樣的教訓：這裡若先截成200，網站即使
    # 要求完整交易日也只會拿到「最新的一小段」（ORDER BY bar_ts DESC），
    # 早盤紀錄不是沒發生，是被這個上限直接砍掉、看起來像消失了。
    # 2026-09-22 同樣的事在 5000 筆上限再發生一次：一天的圖表用 5 分 K 訊號（905／20MA
    # 穿越、MA520 等）就超過 5000 筆，早盤的主力翻多空整批被砍掉、只剩 12:19 那筆。訊號
    # 中心根本不顯示那些 kind（K 線圖走 /stock/{code} 端點自己拿），當日總表預設不回它們。
    limit = max(1, min(int(limit), 20000))
    where = "trade_date = ?"
    params: list[Any] = [trade_date]
    if market_only:
        where += " AND kind = 'break15kLow'"
    else:
        where += " AND kind <> 'break15kLow'"
        if not include_chart_kinds:
            chart_only = tuple(sorted(CHART_ONLY_KLINE_KINDS))
            where += f" AND kind NOT IN ({', '.join('?' for _ in chart_only)})"
            params.extend(chart_only)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM intraday_signals
            WHERE {where}
            ORDER BY bar_ts DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_latest_signals_by_kind(
    trade_date: str,
    kind: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_table()
    # 族群瞬間大單在活躍盤勢中一日可能超過 500 筆。這裡若先截成
    # 500，網站即使要求完整交易日也只能拿到最後一小段，早盤紀錄會
    # 看似消失。公開 API 仍有自己的上限；儲存層允許一次讀回完整日。
    limit = max(1, min(int(limit), 5000))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM intraday_signals
            WHERE trade_date = ? AND kind = ?
            ORDER BY bar_ts DESC, id DESC
            LIMIT ?
            """,
            (trade_date, kind, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_signals_for_ticker(
    ticker: str,
    trade_date: str | None = None,
    since_ts: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """單一股票的所有K線訊號，依bar_ts由舊到新排序，供K線圖疊圖標記使用。
    ticker不會在這裡正規化，呼叫端要自己先轉大寫（例如用
    hanstock_app._normalize_stock_code），跟存進來的資料格式一致才會查到。"""
    _ensure_table()
    limit = max(1, min(int(limit), 2000))
    clauses = ["ticker = ?"]
    params: list[Any] = [ticker]
    if trade_date:
        clauses.append("trade_date = ?")
        params.append(trade_date)
    if since_ts is not None:
        clauses.append("bar_ts >= ?")
        params.append(int(since_ts))
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM intraday_signals
            WHERE {' AND '.join(clauses)}
            ORDER BY bar_ts ASC, id ASC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_to_api(row) for row in rows]


def load_recent_trade_dates(limit: int = 10) -> list[str]:
    _ensure_table()
    limit = max(1, min(int(limit), 60))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, MAX(bar_ts) AS latest_ts
            FROM intraday_signals
            GROUP BY trade_date
            ORDER BY latest_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [str(row["trade_date"]) for row in rows]


def purge_early_signals(trade_date: str, kind: str, cutoff_ts: int) -> int:
    _ensure_table()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM intraday_signals
            WHERE trade_date = ? AND kind = ? AND bar_ts < ?
            """,
            (trade_date, kind, int(cutoff_ts)),
        )
        return max(0, int(cursor.rowcount or 0))


# 5分鐘K線訊號家族的kind清單（intraday_kline_signals.py emit()的完整19種），
# 獨立在這裡列一份而不是從那邊import，避免跟這個模組的循環依賴
# （intraday_kline_signals.py本身就是import這個模組）。
KLINE_SIGNAL_KINDS = {
    "a8short",
    "blackDragon",
    "break905d",
    "combo12Bull",
    "crossDown20ma",
    "crossUp20ma",
    "crossUp905",
    "crossUpPrevHigh",
    "enhanced12short",
    "firstCross905High",
    "firstCrossDown20ma",
    "firstCrossUp20ma",
    "ma20turnDown",
    "ma20turnUp",
    "ma520Down",
    "ma520Up",
    "oneTwoShort",
    "short12",
    "watch12short",
}
# 訊號中心有專屬分頁的 5 分 K 訊號；其餘 K 線訊號只在 K 線圖上疊符號（走 /stock/{code} 端點），
# 當日總表預設不回，免得幾千筆圖表用訊號把早盤的其他訊號擠出 limit。
SIGNAL_CENTER_KLINE_KINDS = {"oneTwoShort", "combo12Bull", "blackDragon"}
CHART_ONLY_KLINE_KINDS = KLINE_SIGNAL_KINDS - SIGNAL_CENTER_KLINE_KINDS


def _out_of_session_kline_where(kinds: tuple[str, ...]) -> str:
    # bar_ts是UTC毫秒，+8小時轉台北時間後取分鐘數：09:00=540, 13:30=810。
    placeholders = ", ".join("?" for _ in kinds)
    return f"""
        kind IN ({placeholders})
        AND (
            CAST((bar_ts + 28800000) / 60000 AS INTEGER) % 1440 < 540
            OR CAST((bar_ts + 28800000) / 60000 AS INTEGER) % 1440 >= 810
        )
    """


def find_out_of_session_kline_signals(trade_date: str) -> list[dict[str, Any]]:
    """稽核用：找出K線訊號家族中bar_ts落在09:00~13:30正常盤中時段之外的
    異常資料列（盤前試撮tick混入bar聚合器的舊bug留下的髒資料，bug已在
    market_data_hub修掉，這裡只是用來清點bug修復之前寫入的舊資料）。
    只讀不刪，給人工確認用。"""
    _ensure_table()
    kinds = tuple(sorted(KLINE_SIGNAL_KINDS))
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM intraday_signals
            WHERE trade_date = ? AND {_out_of_session_kline_where(kinds)}
            ORDER BY bar_ts ASC, id ASC
            """,
            (trade_date, *kinds),
        ).fetchall()
    return [_to_api(row) for row in rows]


def purge_out_of_session_kline_signals(trade_date: str) -> int:
    """實際刪除find_out_of_session_kline_signals()會找到的那些髒資料列。"""
    _ensure_table()
    kinds = tuple(sorted(KLINE_SIGNAL_KINDS))
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            DELETE FROM intraday_signals
            WHERE trade_date = ? AND {_out_of_session_kline_where(kinds)}
            """,
            (trade_date, *kinds),
        )
        return max(0, int(cursor.rowcount or 0))


def delete_kline_signals_for_ticker(trade_date: str, ticker: str) -> int:
    """刪除單一股票在trade_date當天、K線訊號家族(19種kind)的所有已保存
    紀錄，只影響這個家族，不會動到同一張表裡其他訊號家族(大單/四項精選/
    三角收斂等)的資料。

    用途：backfill_today_kline_signals()重播某檔股票當天的Shioaji歷史
    kbars之前，先清空這檔股票舊有的K線訊號紀錄，讓回補後的結果完全
    以歷史kbars重新算出來的版本為準——不這樣做的話，ONCE_PER_DAY_KINDS
    的去重機制（同一天同一檔同一kind只認第一筆）會讓「即時路徑因為
    這檔股票訂閱較晚才啟動、算出時間錯誤的舊紀錄」卡住新算出來的正確
    時間，回補等於白做。"""
    return delete_signals_for_ticker(trade_date, ticker, KLINE_SIGNAL_KINDS)


def delete_signals_for_ticker(trade_date: str, ticker: str, kinds: Iterable[str]) -> int:
    """刪除單一股票在 trade_date 當天、指定 kind 家族的所有已保存紀錄；只影響指定
    家族。各家族回補重播前用，讓重播結果完全以重算版本為準。"""
    kind_tuple = tuple(sorted({str(kind) for kind in kinds if str(kind)}))
    if not kind_tuple:
        return 0
    _ensure_table()
    placeholders = ", ".join("?" for _ in kind_tuple)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            DELETE FROM intraday_signals
            WHERE trade_date = ? AND ticker = ? AND kind IN ({placeholders})
            """,
            (trade_date, ticker, *kind_tuple),
        )
        return max(0, int(cursor.rowcount or 0))


def intraday_signal_count() -> int:
    _ensure_table()
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS n FROM intraday_signals").fetchone()
    return int(row["n"] if row else 0)
