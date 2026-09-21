"""Persist explicitly requested date repairs, including partially collected days.

Only the existing repair worker processes jobs. Empty/quota-limited attempts retain
the request and the original bars; a restart or a new trading day cannot lose it.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from database import get_connection

TW_TZ = timezone(timedelta(hours=8))


def _schema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS main_force_backfill_jobs (
        stock_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', next_attempt REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT,
        PRIMARY KEY(stock_code, trade_date)
    )""")


def request_main_force_backfill(code, trade_date, *, now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, TW_TZ).date()
    requested_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
    if not today - timedelta(days=400) <= requested_date <= today:
        raise ValueError("主力回補日期須在過去 400 天內，且不可為未來日期")
    # The API validates the ticker before entering this function.
    with get_connection() as connection:
        _schema(connection)
        connection.execute("""INSERT OR IGNORE INTO main_force_backfill_jobs
            (stock_code, trade_date, next_attempt) VALUES (?, ?, ?)""", (code, trade_date, now))
        row = connection.execute("""SELECT status, attempts, next_attempt, result_json
            FROM main_force_backfill_jobs WHERE stock_code=? AND trade_date=?""", (code, trade_date)).fetchone()
    return {"queued": row["status"] == "pending", "status": row["status"],
            "attempts": row["attempts"], "nextAttemptAt": row["next_attempt"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None}


def list_main_force_backfill_jobs(code):
    """查詢指定股票所有已排入的主力副圖回補工作狀態；用來診斷「為什麼歷史
    主力買賣力還沒補回來」——是根本沒排到、卡在pending重試、還是已經跑過
    但失敗了(result裡會有錯誤訊息)。"""
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            """SELECT trade_date, status, attempts, next_attempt, result_json
               FROM main_force_backfill_jobs WHERE stock_code=?
               ORDER BY trade_date DESC""",
            (code,),
        ).fetchall()
    return [
        {
            "tradeDate": row["trade_date"],
            "status": row["status"],
            "attempts": row["attempts"],
            "nextAttemptAt": row["next_attempt"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }
        for row in rows
    ]


def queue_backfill_for_codes(codes, dates, *, now=None):
    """一次性把多檔股票 x 多個交易日排進回補佇列，用單一連線+executemany，
    比逐一呼叫request_main_force_backfill快很多(避免成百上千次個別連線)。
    不驗證日期範圍——呼叫端(queue_backfill_for_all_group_stocks)自己產生的
    日期一定合法，這裡只負責快速批次寫入。重複排(已經pending/complete的
    組合)用INSERT OR IGNORE，安全、不會重置重試進度。"""
    now = time.time() if now is None else now
    rows = [(code, date, now) for code in codes for date in dates]
    if not rows:
        return 0
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT OR IGNORE INTO main_force_backfill_jobs
               (stock_code, trade_date, next_attempt) VALUES (?, ?, ?)""",
            rows,
        )
    return len(rows)


def queue_backfill_for_all_group_stocks(days=5, *, now=None):
    """把stock_groups.py所有族群的股票(去重)過去days個平日(不含今天)排進
    主力副圖回補佇列；讓即使沒被使用者手動點開過的股票，之後打開圖表時
    主力買賣力也補得回來，不用每支股票各自等第一次被瀏覽才開始回補。
    呼叫成本低(純SQLite寫入，不含任何Shioaji連線)，重複呼叫(例如每次
    重新部署)對已經排過的組合是安全的no-op，可以放心在啟動時執行。"""
    import stock_groups
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, TW_TZ).date()
    codes = sorted({code for members in stock_groups.STOCK_GROUPS.values() for code, _name in members})
    dates: list[str] = []
    cursor = today - timedelta(days=1)
    while len(dates) < days:
        if cursor.weekday() < 5:
            dates.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    attempted = queue_backfill_for_codes(codes, dates, now=now)
    return {"stockCount": len(codes), "dates": dates, "attempted": attempted}


def process_main_force_backfill_job(*, service=None, now=None, backfill=None):
    now = time.time() if now is None else now
    # Lease one job without holding a SQLite write transaction during broker I/O.
    with get_connection() as connection:
        _schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("""SELECT stock_code, trade_date FROM main_force_backfill_jobs
            WHERE status='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 1""", (now,)).fetchone()
        if row is None:
            return None
        code, date = row["stock_code"], row["trade_date"]
        connection.execute("""UPDATE main_force_backfill_jobs SET next_attempt=?, attempts=attempts+1
            WHERE stock_code=? AND trade_date=?""", (now + 900, code, date))
    if backfill is None:
        from stock_bar_bootstrap import backfill_main_force_date
        backfill = backfill_main_force_date
    try:
        result = backfill(code, date, service=service, now_ms=int(now * 1000))
    except Exception as error:
        result = {"error": type(error).__name__, "main_force_ok": False}
    # A morning response is not a completed whole-day repair. Recheck after close.
    closed_at = datetime.fromisoformat(date).replace(hour=13, minute=35, tzinfo=TW_TZ).timestamp()
    complete = (now >= closed_at and result.get("history_ok") is True
                and result.get("main_force_ok") is True
                and result.get("saved_1m", 0) > 0 and result.get("saved_5m", 0) > 0)
    delay = 300 if "history_quota_exhausted" in str(result.get("error", "")) else 900
    next_attempt = max(now + delay, closed_at) if result.get("main_force_ok") else now + delay
    with get_connection() as connection:
        connection.execute("""UPDATE main_force_backfill_jobs SET status=?, next_attempt=?, result_json=?
            WHERE stock_code=? AND trade_date=?""",
            ("complete" if complete else "pending", next_attempt, json.dumps(result, ensure_ascii=False), code, date))
    return {"code": code, "tradeDate": date, "status": "complete" if complete else "pending", **result}
