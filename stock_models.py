"""選股模型＋一年回測（2026-11-26 使用者：做跟別家「十大選股模型」類似的東西，十個都做，自己分類，放首頁一欄，回測一年）。

十個模型的名字我們自己取、條件寫在下面的 MODELS（首頁照這份說明列出來），分四類：大戶籌碼、法人、營收成長、綜合。
共同條件：一般股票（4 位數、不含 ETF）、收盤 ≥ 10 元、20 日均成交值 ≥ 3000 萬；條件「剛成立」那天才算訊號（前一天沒成立）。

回測（近 BACKTEST_DAYS 個交易日）：訊號隔天開盤買（開盤就漲停 9.5% 以上買不到，跳過），持有 HOLD_DAYS 個交易日收盤賣，
中途收盤跌破月線就當天收盤賣；同一檔持有中不重複買。每筆 10 萬元，扣手續費（買賣各 0.1425%）＋證交稅 0.3%。
報酬率用「最大資金需求」（同時持有最多幾檔 × 10 萬）當本金——別家的 321% 是用固定 1 億當本金算的，實際要 3.87 億，
用實際需要的錢算才不會誇大。同期大盤用 FinMind 的加權指數（TAIEX）與櫃買指數（TPEx）收盤。

資料：日K（bars_1d，還原分割減資）、集保（tdcc_weekly，含總股東人數）、法人（官方 institutional_daily 只有 2026-09 以後，
一年份用 FinMind 依日期整個市場補到 model_inst_daily）、融資餘額與現股當沖量（FinMind 依日期補，散戶熱度用）、月營收（FinMind，用 create_time＝實際公布日，回測不會偷看還沒公布的營收）、
指數（FinMind）。FinMind 跟券商分點收集器共用同一個每小時限流。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database
from fundamentals_daily import BIG_HOLDER_LEVELS, _schema as _fundamentals_schema

logger = logging.getLogger("hanstock.stock_models")
TW_TZ = timezone(timedelta(hours=8))

BACKTEST_DAYS = 250            # 回測一年
WARMUP_DAYS = 70               # 60 日均線、20 日新高要先有的交易日
HOLD_DAYS = 10
POSITION_NTD = 100_000
FEE_PCT = 0.1425
TAX_PCT = 0.3
COST_PCT = FEE_PCT * 2 + TAX_PCT
MIN_PRICE = 10.0
MIN_VALUE_NTD = 30_000_000     # 20 日均成交值
LIMIT_UP_GAP_PCT = 9.5
THOUSAND_LEVEL = 15            # 集保第 15 級＝1000 張以上
REVENUE_MONTHS = 27            # 回測一年要算年增、還要比上個月：一年＋一年＋幾個月
INST_RETRY_RECENT_SECONDS = 3 * 3600
POLL_SECONDS = 30 * 60
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"

CATEGORIES = [
    {"key": "chip", "name": "大戶籌碼"},
    {"key": "inst", "name": "法人"},
    {"key": "growth", "name": "營收成長"},
    {"key": "combo", "name": "綜合"},
]
MODELS: list[dict[str, Any]] = [
    {
        "key": "bigInst", "cat": "chip", "name": "大戶法人同步", "ref": 4,
        "idea": "集保大戶和外資投信同一段時間一起買，籌碼往大戶、法人集中。",
        "rules": [
            "集保 400 張以上大戶持股比，這週比上週多 ≥ 0.5 個百分點",
            "近 5 個交易日外資＋投信合計買超，至少 3 天是買超",
            "收盤站上月線（20 日均線）",
        ],
    },
    {
        "key": "kiloBull", "cat": "chip", "name": "千張大戶連增多頭", "ref": 8,
        "idea": "千張大戶連續加碼，量能放大、均線多頭、股價創 20 日新高。",
        "rules": [
            "集保 1000 張以上大戶持股比連 2 週增加，兩週合計 ≥ 0.3 個百分點",
            "5 日均量 ≥ 20 日均量 1.3 倍",
            "收盤 > 5 日 > 20 日 > 60 日均線，20 日均線比 5 天前高",
            "收盤創 20 日新高",
        ],
    },
    {
        "key": "mainVsRetail", "cat": "chip", "name": "大戶進散戶退", "ref": 2,
        "idea": "大戶在買、小股東在走（集保總人數變少），營收同時轉強。",
        "rules": [
            "集保 400 張以上大戶持股比這週比上週多 ≥ 0.3 個百分點",
            "集保總股東人數這週比上週少 ≥ 1%",
            "最新公布的月營收年增 > 0，而且比上個月的年增高",
            "收盤站上月線",
        ],
    },
    {
        "key": "whaleHot", "cat": "chip", "name": "大戶吸籌當沖熱", "ref": 6,
        "idea": "千張大戶默默加碼，同時散戶當沖很熱（大戶跟散戶對做）。",
        "rules": [
            "集保 1000 張以上大戶持股比這週比上週多 ≥ 0.5 個百分點",
            "近 5 個交易日平均當沖比（現股當沖量 ÷ 成交量）≥ 30%",
            "收盤站上月線",
        ],
    },
    {
        "key": "trustGrowth", "cat": "inst", "name": "投信卡位營收成長", "ref": 1,
        "idea": "投信連續買進、量能放大，而且月營收年增在加速。",
        "rules": [
            "近 10 個交易日投信至少 5 天買超，累計 ≥ 200 張且 ≥ 這 10 天成交量 3%",
            "5 日均量 ≥ 20 日均量 1.2 倍",
            "最新公布的月營收年增 ≥ 10%，而且比上個月的年增高",
            "收盤站上月線（20 日均線）",
        ],
    },
    {
        "key": "bigInstFire", "cat": "inst", "name": "大戶法人合買點火", "ref": 5,
        "idea": "大戶加碼的那週，外資投信同一天一起買，價量同步點火。",
        "rules": [
            "集保 400 張以上大戶持股比這週比上週多 ≥ 0.3 個百分點",
            "當天外資、投信同時買超",
            "當天漲幅 ≥ 2%，成交量 ≥ 20 日均量 1.5 倍",
            "收盤站上月線",
        ],
    },
    {
        "key": "instThenRetail", "cat": "inst", "name": "法人先買散戶跟進", "ref": 7,
        "idea": "法人先進場定方向，之後融資（散戶）跟著加碼追價。",
        "rules": [
            "10～6 個交易日前這 5 天，外資＋投信至少 4 天買超",
            "近 5 個交易日融資餘額增加 ≥ 5%（5 天前融資 ≥ 100 張）",
            "近 5 個交易日股價上漲，收盤站上月線",
        ],
    },
    {
        "key": "instFirst", "cat": "inst", "name": "法人首次進場", "ref": 9,
        "idea": "外資或投信第一次大買，散戶熱度還沒起來時先卡位。",
        "rules": [
            "當天外資或投信買超 ≥ 當天成交量 5%",
            "前 20 個交易日同一個法人買超不超過 2 天、累計沒有買超",
            "近 5 個交易日平均當沖比 < 20%（熱度還沒起來）",
            "收盤站上月線",
        ],
    },
    {
        "key": "revBoom", "cat": "growth", "name": "營收起飛爆量", "ref": 3,
        "idea": "月營收創一年新高、年增大增，股價帶量突破。",
        "rules": [
            "最新公布的月營收年增 ≥ 20%，而且是近 12 個月最高",
            "當天成交量 ≥ 20 日均量 2 倍，漲幅 ≥ 3%",
            "收盤創 20 日新高",
        ],
    },
    {
        "key": "multiCombo", "cat": "combo", "name": "多模型共振", "ref": 10,
        "idea": "近 5 個交易日內被兩個以上模型選到，再加上均線多頭。",
        "rules": [
            "近 5 個交易日內，前面九個模型裡至少兩個發出訊號",
            "收盤 > 20 日 > 60 日均線",
        ],
    },
]
COMMON_RULES = [
    "一般股票（不含 ETF）、收盤 ≥ 10 元、20 日均成交值 ≥ 3000 萬",
    "條件剛成立那天才算訊號（前一天沒成立）",
]
BACKTEST_RULES = [
    "訊號隔天開盤買（開盤就漲停買不到的跳過），持有 10 個交易日收盤賣；中途收盤跌破月線就當天收盤賣",
    "同一檔持有中不重複買；每筆 10 萬元，已扣手續費（買賣各 0.1425%）＋證交稅 0.3%",
    "報酬率的本金＝最大資金需求（同時持有最多幾檔 × 10 萬），不是固定本金",
]

_state: dict[str, Any] = {"running": False, "lastRunAt": None, "lastError": None, "backfill": {}, "computedKey": None, "computeSeconds": None}
_lock = threading.Lock()
_started = False


# ------------------------------------------------------------------ 資料表

def _schema(connection: Any) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_inst_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            foreign_net INTEGER NOT NULL, trust_net INTEGER NOT NULL, dealer_net INTEGER NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS model_margin_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, balance INTEGER NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS model_daytrade_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, lots INTEGER NOT NULL,
            PRIMARY KEY (trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS model_revenue (
            stock_code TEXT NOT NULL, ym TEXT NOT NULL, revenue INTEGER NOT NULL, announced TEXT,
            PRIMARY KEY (stock_code, ym)
        );
        CREATE TABLE IF NOT EXISTS model_index_daily (
            index_id TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL,
            PRIMARY KEY (index_id, trade_date)
        );
        CREATE TABLE IF NOT EXISTS model_fetch_log (
            kind TEXT NOT NULL, key TEXT NOT NULL, rows INTEGER NOT NULL, at TEXT NOT NULL,
            PRIMARY KEY (kind, key)
        );
        CREATE TABLE IF NOT EXISTS stock_models_cache (
            key TEXT PRIMARY KEY, input_key TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL
        );
        """
    )


def _now() -> datetime:
    return datetime.now(TW_TZ)


# ------------------------------------------------------------------ FinMind 解析（純函式，測試用）

def parse_institutional(payload: Any) -> dict[str, dict[str, tuple[int, int, int]]]:
    """FinMind TaiwanStockInstitutionalInvestorsBuySell（一天整個市場）→ {日期: {代號: (外資淨, 投信淨, 自營淨)}}，單位股。"""
    out: dict[str, dict[str, list[int]]] = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_id") or "").strip().upper()
        day = str(row.get("date") or "")[:10]
        if not code or len(day) != 10:
            continue
        try:
            net = int(row.get("buy") or 0) - int(row.get("sell") or 0)
        except (TypeError, ValueError):
            continue
        name = str(row.get("name") or "")
        slot = out.setdefault(day, {}).setdefault(code, [0, 0, 0])
        if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
            slot[0] += net
        elif name == "Investment_Trust":
            slot[1] += net
        elif name in ("Dealer_self", "Dealer_Hedging", "Dealer"):
            slot[2] += net
    return {d: {c: (v[0], v[1], v[2]) for c, v in m.items()} for d, m in out.items()}


def parse_margin(payload: Any) -> dict[str, dict[str, int]]:
    """FinMind TaiwanStockMarginPurchaseShortSale → {日期: {代號: 融資餘額（張）}}。"""
    out: dict[str, dict[str, int]] = {}
    for row in (payload.get("data") if isinstance(payload, dict) else None) or []:
        try:
            out.setdefault(str(row["date"])[:10], {})[str(row["stock_id"]).strip().upper()] = int(row.get("MarginPurchaseTodayBalance") or 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def parse_daytrade(payload: Any) -> dict[str, dict[str, int]]:
    """FinMind TaiwanStockDayTrading → {日期: {代號: 現股當沖量（張）}}（Volume 是股）。"""
    out: dict[str, dict[str, int]] = {}
    for row in (payload.get("data") if isinstance(payload, dict) else None) or []:
        try:
            out.setdefault(str(row["date"])[:10], {})[str(row["stock_id"]).strip().upper()] = int(round(float(row.get("Volume") or 0) / 1000))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def parse_revenue(payload: Any) -> list[tuple[str, str, int, str | None]]:
    """FinMind TaiwanStockMonthRevenue → [(代號, 營收月份 YYYY-MM, 營收, 公布日)]。"""
    out = []
    rows = payload.get("data") if isinstance(payload, dict) else None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_id") or "").strip().upper()
        try:
            y, m, rev = int(row.get("revenue_year")), int(row.get("revenue_month")), int(row.get("revenue") or 0)
        except (TypeError, ValueError):
            continue
        if not code or not (1 <= m <= 12) or rev <= 0:
            continue
        announced = str(row.get("create_time") or "")[:10] or None
        out.append((code, f"{y:04d}-{m:02d}", rev, announced if announced and len(announced) == 10 else None))
    return out


def parse_index(payload: Any) -> dict[str, float]:
    out = {}
    rows = payload.get("data") if isinstance(payload, dict) else None
    for row in rows or []:
        try:
            out[str(row["date"])[:10]] = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ 回補

def _finmind(params: dict[str, str]) -> Any:
    from finmind_broker_branch_collector import _request_json   # 共用 FinMind 每小時限流與 token

    return _request_json(FINMIND_DATA_URL, params)


def _logged(connection: Any, kind: str) -> dict[str, tuple[int, str]]:
    return {str(r["key"]): (int(r["rows"]), str(r["at"])) for r in connection.execute("SELECT key, rows, at FROM model_fetch_log WHERE kind = ?", (kind,))}


def _log(connection: Any, kind: str, key: str, rows: int) -> None:
    connection.execute("INSERT OR REPLACE INTO model_fetch_log (kind, key, rows, at) VALUES (?, ?, ?, ?)", (kind, key, int(rows), _now().isoformat(timespec="seconds")))


def _retry_ok(entry: tuple[int, str] | None, recent: bool) -> bool:
    """抓過但空的：最近幾天的隔 3 小時再試，舊的一天試一次。抓到資料的不再抓。"""
    if entry is None:
        return True
    rows, at = entry
    if rows > 0:
        return False
    try:
        age = (_now() - datetime.fromisoformat(at)).total_seconds()
    except ValueError:
        return True
    return age >= (INST_RETRY_RECENT_SECONDS if recent else 86400)


DAILY_DATASETS: dict[str, tuple[str, Callable[[Any], dict[str, dict[str, Any]]], str, str]] = {
    "inst": ("TaiwanStockInstitutionalInvestorsBuySell", parse_institutional, "model_inst_daily", "foreign_net, trust_net, dealer_net"),
    "margin": ("TaiwanStockMarginPurchaseShortSale", parse_margin, "model_margin_daily", "balance"),
    "daytrade": ("TaiwanStockDayTrading", parse_daytrade, "model_daytrade_daily", "lots"),
}


def backfill(trade_dates: list[str], *, fetch: Callable[[dict[str, str]], Any] | None = None, max_calls: int = 1000) -> dict[str, Any]:
    """補法人、融資、當沖（每個交易日一次、整個市場）、月營收（每月一次）、指數。trade_dates 新到舊。"""
    fetch = fetch or _finmind
    initialize_database()
    calls = 0
    result: dict[str, Any] = {"inst": 0, "margin": 0, "daytrade": 0, "revenue": 0, "index": 0, "errors": []}
    today = _now().strftime("%Y-%m-%d")
    for kind, (dataset, parse, table, cols) in DAILY_DATASETS.items():
        with get_connection() as connection:
            _schema(connection)
            done = _logged(connection, kind)
        fails = 0
        for i, day in enumerate(trade_dates):
            if calls >= max_calls or fails >= 5:
                break
            if day == today and _now().strftime("%H:%M") < "16:30":   # 今天的盤後才有
                continue
            if not _retry_ok(done.get(day), recent=i < 3):
                continue
            try:
                calls += 1
                parsed = parse(fetch({"dataset": dataset, "start_date": day, "end_date": day})).get(day, {})
            except Exception as exc:  # noqa: BLE001
                fails += 1
                result["errors"].append(f"{kind} {day}: {type(exc).__name__}: {exc}"[:200])
                continue
            values = [(day, code, *(v if isinstance(v, tuple) else (v,))) for code, v in parsed.items()]
            with get_connection() as connection:
                _schema(connection)
                connection.executemany(f"INSERT OR REPLACE INTO {table} (trade_date, stock_code, {cols}) VALUES (?, ?, {','.join('?' for _ in cols.split(','))})", values)
                _log(connection, kind, day, len(parsed))
            result[kind] = result.get(kind, 0) + 1
    # 月營收：FinMind 的 date＝營收月份的下個月 1 號；每個 date 抓一次
    now = _now()
    with get_connection() as connection:
        _schema(connection)
        done_rev = _logged(connection, "revenue")
    y, m = now.year, now.month
    for back in range(REVENUE_MONTHS):
        if calls >= max_calls:
            break
        yy, mm = y, m - back
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        key = f"{yy:04d}-{mm:02d}-01"
        if not _retry_ok(done_rev.get(key), recent=back < 2):
            continue
        try:
            calls += 1
            rows = parse_revenue(fetch({"dataset": "TaiwanStockMonthRevenue", "start_date": key, "end_date": key}))
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"revenue {key}: {type(exc).__name__}: {exc}"[:200])
            continue
        with get_connection() as connection:
            _schema(connection)
            connection.executemany("INSERT OR REPLACE INTO model_revenue (stock_code, ym, revenue, announced) VALUES (?, ?, ?, ?)", rows)
            _log(connection, "revenue", key, len(rows))
        result["revenue"] += 1
    # 指數：每次補最近一年多（2 次呼叫），一天一次就好
    with get_connection() as connection:
        _schema(connection)
        done_idx = _logged(connection, "index")
    stamp = now.strftime("%Y-%m-%d") + ("pm" if now.strftime("%H:%M") >= "15:00" else "am")
    if trade_dates and done_idx.get("all", (0, ""))[1][:10] + ("pm" if done_idx.get("all", (0, "T00:00"))[1][11:16] >= "15:00" else "am") != stamp:
        start = (date.fromisoformat(trade_dates[-1]) - timedelta(days=10)).isoformat()
        total = 0
        for index_id in ("TAIEX", "TPEx"):
            try:
                calls += 1
                closes = parse_index(fetch({"dataset": "TaiwanStockPrice", "data_id": index_id, "start_date": start}))
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"index {index_id}: {type(exc).__name__}: {exc}"[:200])
                continue
            with get_connection() as connection:
                _schema(connection)
                connection.executemany("INSERT OR REPLACE INTO model_index_daily (index_id, trade_date, close) VALUES (?, ?, ?)", [(index_id, d, c) for d, c in closes.items()])
            total += len(closes)
        with get_connection() as connection:
            _schema(connection)
            _log(connection, "index", "all", total)
        result["index"] = total
    result["calls"] = calls
    return result


# ------------------------------------------------------------------ 載入

def _trade_dates(limit: int) -> list[str]:
    from daily_bars_store import recent_bar_dates

    return recent_bar_dates(limit)


def _load_inst(since: str, until: str) -> dict[str, dict[str, tuple[int, int]]]:
    """{代號: {日期: (外資淨, 投信淨)}}（股）。FinMind 回補的為主，沒有的那天用官方 institutional_daily。"""
    from chips_daily import _schema as chips_schema

    out: dict[str, dict[str, tuple[int, int]]] = {}
    with get_connection() as connection:
        _schema(connection)
        chips_schema(connection)
        for row in connection.execute("SELECT trade_date, stock_code, foreign_net, trust_net FROM institutional_daily WHERE trade_date >= ? AND trade_date <= ?", (since, until)):
            out.setdefault(str(row["stock_code"]).upper(), {})[str(row["trade_date"])] = (int(row["foreign_net"]), int(row["trust_net"]))
        for row in connection.execute("SELECT trade_date, stock_code, foreign_net, trust_net FROM model_inst_daily WHERE trade_date >= ? AND trade_date <= ?", (since, until)):
            out.setdefault(str(row["stock_code"]).upper(), {})[str(row["trade_date"])] = (int(row["foreign_net"]), int(row["trust_net"]))
    return out


def _inst_dates(since: str, until: str) -> set[str]:
    with get_connection() as connection:
        _schema(connection)
        a = {str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM model_inst_daily WHERE trade_date >= ? AND trade_date <= ?", (since, until))}
        try:
            b = {str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM institutional_daily WHERE trade_date >= ? AND trade_date <= ?", (since, until))}
        except Exception:  # noqa: BLE001
            b = set()
    return a | b


def _load_tdcc(codes: list[str]) -> dict[str, list[tuple[str, float, float, int]]]:
    """{代號: [(集保結算日, 400 張以上持股比%, 1000 張以上持股比%, 總股東人數)] 舊到新}。"""
    out: dict[str, dict[str, list[float]]] = {}
    levels = ",".join(str(x) for x in (*BIG_HOLDER_LEVELS, 17))
    with get_connection() as connection:
        _fundamentals_schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"SELECT data_date, stock_code, level, holders, pct FROM tdcc_weekly WHERE stock_code IN ({','.join('?' for _ in batch)}) AND level IN ({levels})",
                tuple(batch),
            ).fetchall()
            for row in rows:
                slot = out.setdefault(str(row["stock_code"]).upper(), {}).setdefault(str(row["data_date"]), [0.0, 0.0, 0])
                level = int(row["level"])
                if level == 17:
                    slot[2] = int(row["holders"])
                    continue
                slot[0] += float(row["pct"])
                if level == THOUSAND_LEVEL:
                    slot[1] += float(row["pct"])
    return {c: sorted((d, v[0], v[1], int(v[2])) for d, v in m.items()) for c, m in out.items()}


def _load_daily_map(table: str, col: str, since: str, until: str) -> tuple[dict[str, dict[str, int]], set[str]]:
    """融資／當沖：{代號: {日期: 值}}、有資料的日期。"""
    out: dict[str, dict[str, int]] = {}
    days: set[str] = set()
    with get_connection() as connection:
        _schema(connection)
        for row in connection.execute(f"SELECT trade_date, stock_code, {col} AS v FROM {table} WHERE trade_date >= ? AND trade_date <= ?", (since, until)):
            d = str(row["trade_date"])
            days.add(d)
            out.setdefault(str(row["stock_code"]).upper(), {})[d] = int(row["v"])
    return out, days


def _load_revenue() -> dict[str, dict[str, tuple[int, str | None]]]:
    out: dict[str, dict[str, tuple[int, str | None]]] = {}
    with get_connection() as connection:
        _schema(connection)
        for row in connection.execute("SELECT stock_code, ym, revenue, announced FROM model_revenue"):
            out.setdefault(str(row["stock_code"]).upper(), {})[str(row["ym"])] = (int(row["revenue"]), row["announced"])
    return out


def _load_index(since: str) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    with get_connection() as connection:
        _schema(connection)
        for row in connection.execute("SELECT index_id, trade_date, close FROM model_index_daily WHERE trade_date >= ? ORDER BY trade_date", (since,)):
            out.setdefault(str(row["index_id"]), []).append((str(row["trade_date"]), float(row["close"])))
    return out


def _names() -> dict[str, str]:
    with get_connection() as connection:
        return {str(r["stock_code"]).upper(): str(r["stock_name"]) for r in connection.execute("SELECT stock_code, stock_name FROM stocks")}


# ------------------------------------------------------------------ 特徵與訊號（純函式）

def _ma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= n:
            acc -= values[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def _ym_minus(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - n
    while m <= 0:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def revenue_yoy_at(revenue: dict[str, tuple[int, str | None]], day: str) -> tuple[str, float, float | None] | None:
    """day 那天看得到的最新一個月營收的 (月份, 年增%, 上個月年增%)。公布日用 FinMind create_time，沒有就當營收月份隔月 10 號。"""
    best = None
    for ym, (_, announced) in revenue.items():
        ann = announced or (_ym_minus(ym, -1) + "-10")
        if ann <= day and (best is None or ym > best):
            best = ym
    if best is None:
        return None

    def yoy(ym: str) -> float | None:
        cur, base = revenue.get(ym), revenue.get(_ym_minus(ym, 12))
        return (cur[0] / base[0] - 1) * 100 if cur and base and base[0] > 0 else None

    now_yoy = yoy(best)
    if now_yoy is None:
        return None
    return best, now_yoy, yoy(_ym_minus(best, 1))


class StockSeries:
    """一檔股票的日K（還原後）與對齊到同一串日期的特徵。"""

    def __init__(self, code: str, bars: list[tuple], inst: dict[str, tuple[int, int]], inst_days: set[str],
                 tdcc: list[tuple[str, float, float, int]], revenue: dict[str, tuple[int, str | None]],
                 margin: dict[str, int] | None = None, margin_days: set[str] | None = None,
                 daytrade: dict[str, int] | None = None, daytrade_days: set[str] | None = None):
        self.code = code
        self.dates = [b[0] for b in bars]
        self.open = [float(b[1]) for b in bars]
        self.close = [float(b[4]) for b in bars]
        self.vol = [float(b[5]) for b in bars]   # 張
        self.ma5, self.ma20, self.ma60 = _ma(self.close, 5), _ma(self.close, 20), _ma(self.close, 60)
        self.v5, self.v20 = _ma(self.vol, 5), _ma(self.vol, 20)
        self.value20 = _ma([c * v * 1000 for c, v in zip(self.close, self.vol)], 20)
        self.inst = inst
        self.inst_days = inst_days
        self.tdcc = tdcc
        self.tdcc_dates = [w[0] for w in tdcc]
        self.revenue = revenue
        self.margin, self.margin_days = margin or {}, margin_days or set()
        self.daytrade, self.daytrade_days = daytrade or {}, daytrade_days or set()
        self.hits: dict[str, set[int]] = {}    # 其他模型發訊號的那幾天（多模型共振用）

    def dt_ratio(self, i: int, n: int) -> float | None:
        """近 n 天平均當沖比（%）；那幾天有一天整個市場沒有當沖資料就不判斷。"""
        if i < n - 1:
            return None
        vals = []
        for j in range(i - n + 1, i + 1):
            d = self.dates[j]
            if d not in self.daytrade_days or self.vol[j] <= 0:
                return None
            vals.append(min(100.0, self.daytrade.get(d, 0) / self.vol[j] * 100))
        return sum(vals) / n

    def margin_change(self, i: int, n: int) -> tuple[float, int] | None:
        """近 n 天融資餘額變化（%）與 n 天前的餘額（張）。"""
        if i < n:
            return None
        a, b = self.dates[i - n], self.dates[i]
        if a not in self.margin_days or b not in self.margin_days:
            return None
        base = self.margin.get(a, 0)
        if base <= 0:
            return None
        return (self.margin.get(b, 0) / base - 1) * 100, base

    def revenue_high12(self, ym: str) -> bool:
        cur = self.revenue.get(ym)
        if not cur:
            return False
        prev = [self.revenue.get(_ym_minus(ym, k)) for k in range(1, 12)]
        prev = [p[0] for p in prev if p]
        return len(prev) >= 9 and cur[0] > max(prev)

    def basic_ok(self, i: int) -> bool:
        v = self.value20[i]
        return self.close[i] >= MIN_PRICE and v is not None and v >= MIN_VALUE_NTD and self.ma20[i] is not None

    def weeks_before(self, i: int, n: int) -> list[tuple[str, float, float, int]] | None:
        """當天以前已經結算的最近 n 週集保（結算日 < 當天，週五結算、週末公布，當天開盤前看得到），舊到新。"""
        k = bisect_left(self.tdcc_dates, self.dates[i])
        if k < n:
            return None
        weeks = self.tdcc[k - n:k]
        # 週跟週之間差太多天（缺一週）就不比
        for a, b in zip(weeks, weeks[1:]):
            if (date.fromisoformat(b[0]) - date.fromisoformat(a[0])).days > 10:
                return None
        return weeks

    def inst_window(self, i: int, n: int) -> list[tuple[int, int]] | None:
        if i < n - 1:
            return None
        days = self.dates[i - n + 1:i + 1]
        if any(d not in self.inst_days for d in days):
            return None   # 那幾天整個市場都沒有法人資料（還沒回補）就不判斷
        return [self.inst.get(d, (0, 0)) for d in days]


def cond_big_inst(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i]:
        return None
    weeks = s.weeks_before(i, 2)
    if not weeks:
        return None
    big_up = weeks[1][1] - weeks[0][1]
    if big_up < 0.5:
        return None
    win = s.inst_window(i, 5)
    if not win:
        return None
    nets = [f + t for f, t in win]
    if sum(nets) <= 0 or sum(1 for x in nets if x > 0) < 3:
        return None
    return {"bigUp": round(big_up, 2), "week": weeks[1][0], "inst5": round(sum(nets) / 1000), "buyDays": sum(1 for x in nets if x > 0)}


def cond_kilo_bull(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or i < 25:
        return None
    ma5, ma20, ma60 = s.ma5[i], s.ma20[i], s.ma60[i]
    if ma60 is None or not (s.close[i] > ma5 > ma20 > ma60) or s.ma20[i - 5] is None or ma20 <= s.ma20[i - 5]:
        return None
    if s.close[i] < max(s.close[i - 20:i]):
        return None
    if s.v5[i] is None or s.v20[i] is None or s.v20[i] <= 0 or s.v5[i] < 1.3 * s.v20[i]:
        return None
    weeks = s.weeks_before(i, 3)
    if not weeks:
        return None
    d1, d2 = weeks[1][2] - weeks[0][2], weeks[2][2] - weeks[1][2]
    if d1 <= 0 or d2 <= 0 or d1 + d2 < 0.3:
        return None
    return {"kiloUp": round(d1 + d2, 2), "week": weeks[2][0], "volX": round(s.v5[i] / s.v20[i], 2)}


def cond_trust_growth(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i]:
        return None
    if s.v5[i] is None or s.v20[i] is None or s.v20[i] <= 0 or s.v5[i] < 1.2 * s.v20[i]:
        return None
    win = s.inst_window(i, 10)
    if not win:
        return None
    trust = [t for _, t in win]
    total_lots = sum(trust) / 1000
    vol10 = sum(s.vol[i - 9:i + 1])
    if sum(1 for x in trust if x > 0) < 5 or total_lots < 200 or vol10 <= 0 or total_lots < 0.03 * vol10:
        return None
    rev = revenue_yoy_at(s.revenue, s.dates[i])
    if not rev or rev[1] < 10 or rev[2] is None or rev[1] <= rev[2]:
        return None
    return {"trust10": round(total_lots), "trustDays": sum(1 for x in trust if x > 0), "revMonth": rev[0], "yoy": round(rev[1], 1), "prevYoy": round(rev[2], 1), "volX": round(s.v5[i] / s.v20[i], 2)}


def cond_main_vs_retail(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i]:
        return None
    weeks = s.weeks_before(i, 2)
    if not weeks or weeks[0][3] <= 0 or weeks[1][3] <= 0:
        return None
    big_up = weeks[1][1] - weeks[0][1]
    holders = (weeks[1][3] / weeks[0][3] - 1) * 100
    if big_up < 0.3 or holders > -1:
        return None
    rev = revenue_yoy_at(s.revenue, s.dates[i])
    if not rev or rev[1] <= 0 or rev[2] is None or rev[1] <= rev[2]:
        return None
    return {"bigUp": round(big_up, 2), "holdersChg": round(holders, 2), "week": weeks[1][0], "revMonth": rev[0], "yoy": round(rev[1], 1)}


def cond_whale_hot(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i]:
        return None
    weeks = s.weeks_before(i, 2)
    if not weeks:
        return None
    kilo_up = weeks[1][2] - weeks[0][2]
    if kilo_up < 0.5:
        return None
    dt = s.dt_ratio(i, 5)
    if dt is None or dt < 30:
        return None
    return {"kiloUp": round(kilo_up, 2), "week": weeks[1][0], "dayTrade": round(dt, 1)}


def cond_big_inst_fire(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i] or i < 1:
        return None
    pct = (s.close[i] / s.close[i - 1] - 1) * 100 if s.close[i - 1] else 0
    if pct < 2 or s.v20[i] is None or s.v20[i] <= 0 or s.vol[i] < 1.5 * s.v20[i]:
        return None
    weeks = s.weeks_before(i, 2)
    if not weeks:
        return None
    big_up = weeks[1][1] - weeks[0][1]
    if big_up < 0.3:
        return None
    win = s.inst_window(i, 1)
    if not win or win[0][0] <= 0 or win[0][1] <= 0:
        return None
    return {"bigUp": round(big_up, 2), "week": weeks[1][0], "foreign": round(win[0][0] / 1000), "trust": round(win[0][1] / 1000), "volX": round(s.vol[i] / s.v20[i], 2)}


def cond_inst_then_retail(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i] or i < 10:
        return None
    if s.close[i] <= s.close[i - 5]:
        return None
    win = s.inst_window(i - 5, 5)
    if not win or sum(1 for f, t in win if f + t > 0) < 4:
        return None
    mc = s.margin_change(i, 5)
    if not mc or mc[1] < 100 or mc[0] < 5:
        return None
    return {"instDays": sum(1 for f, t in win if f + t > 0), "marginChg": round(mc[0], 1)}


def cond_inst_first(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.close[i] <= s.ma20[i] or s.vol[i] <= 0:
        return None
    win = s.inst_window(i, 21)
    if not win:
        return None
    today, before = win[-1], win[:-1]
    who = None
    for k, label in ((0, "外資"), (1, "投信")):
        if today[k] / 1000 >= 0.05 * s.vol[i]:
            prior = [x[k] for x in before]
            if sum(1 for x in prior if x > 0) <= 2 and sum(prior) <= 0:
                who = (k, label)
                break
    if not who:
        return None
    dt = s.dt_ratio(i, 5)
    if dt is None or dt >= 20:
        return None
    return {"who": who[1], "lots": round(today[who[0]] / 1000), "pctOfVol": round(today[who[0]] / 1000 / s.vol[i] * 100, 1), "dayTrade": round(dt, 1)}


def cond_rev_boom(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or i < 21:
        return None
    pct = (s.close[i] / s.close[i - 1] - 1) * 100 if s.close[i - 1] else 0
    if pct < 3 or s.v20[i] is None or s.v20[i] <= 0 or s.vol[i] < 2 * s.v20[i] or s.close[i] < max(s.close[i - 20:i]):
        return None
    rev = revenue_yoy_at(s.revenue, s.dates[i])
    if not rev or rev[1] < 20 or not s.revenue_high12(rev[0]):
        return None
    return {"revMonth": rev[0], "yoy": round(rev[1], 1), "pct": round(pct, 2), "volX": round(s.vol[i] / s.v20[i], 2)}


def cond_multi_combo(s: StockSeries, i: int) -> dict[str, Any] | None:
    if not s.basic_ok(i) or s.ma60[i] is None or not (s.close[i] > s.ma20[i] > s.ma60[i]):
        return None
    which = sorted(k for k, idx in s.hits.items() if any(i - 4 <= j <= i for j in idx))
    if len(which) < 2:
        return None
    return {"models": which}


CONDITIONS: dict[str, Callable[[StockSeries, int], dict[str, Any] | None]] = {
    "bigInst": cond_big_inst, "kiloBull": cond_kilo_bull, "mainVsRetail": cond_main_vs_retail, "whaleHot": cond_whale_hot,
    "trustGrowth": cond_trust_growth, "bigInstFire": cond_big_inst_fire, "instThenRetail": cond_inst_then_retail,
    "instFirst": cond_inst_first, "revBoom": cond_rev_boom, "multiCombo": cond_multi_combo,
}


def signals_for(s: StockSeries, cond: Callable[[StockSeries, int], dict[str, Any] | None], start: int) -> list[tuple[int, dict[str, Any]]]:
    """條件剛成立的那幾天（前一天沒成立）。"""
    out = []
    prev = cond(s, start - 1) if start > 0 else None
    for i in range(start, len(s.dates)):
        cur = cond(s, i)
        if cur and not prev:
            out.append((i, cur))
        prev = cur
    return out


def simulate(s: StockSeries, signals: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    """照回測規則把訊號變成交易。還沒到出場日的是持有中（open=True、照最新收盤算）。"""
    trades = []
    busy_until = -1
    n = len(s.dates)
    for i, why in signals:
        if i <= busy_until or i + 1 >= n:
            continue
        e = i + 1
        entry = s.open[e]
        if entry <= 0 or entry >= s.close[i] * (1 + LIMIT_UP_GAP_PCT / 100):
            continue
        last = min(e + HOLD_DAYS - 1, n - 1)
        x, reason = last, "hold"
        for j in range(e, last + 1):
            if s.ma20[j] is not None and s.close[j] < s.ma20[j]:
                x, reason = j, "ma20"
                break
        is_open = reason == "hold" and e + HOLD_DAYS - 1 > n - 1
        exit_px = s.close[x]
        ret = (exit_px / entry - 1) * 100 - COST_PCT
        trades.append({"code": s.code, "signal": s.dates[i], "entryDate": s.dates[e], "entry": round(entry, 2), "exitDate": s.dates[x], "exit": round(exit_px, 2),
                       "ret": round(ret, 2), "reason": reason, "open": is_open, "entryIdx": e, "exitIdx": x})
        busy_until = x
    return trades


def summarize(trades: list[dict[str, Any]], series: dict[str, StockSeries], days: list[str], index: dict[str, list[tuple[str, float]]]) -> dict[str, Any]:
    """勝率、盈虧比、最大資金需求、用最大資金需求當本金的報酬率、最大回撤、權益曲線（跟加權、櫃買比）。"""
    closed = [t for t in trades if not t["open"]]
    wins = [t["ret"] for t in closed if t["ret"] > 0]
    losses = [t["ret"] for t in closed if t["ret"] <= 0]
    day_pos = {d: k for k, d in enumerate(days)}
    held = [0] * len(days)
    pnl_day = [0.0] * len(days)       # 當天收盤的累計損益（已實現＋未實現）
    realized_on = [0.0] * len(days)
    for t in trades:
        s = series[t["code"]]
        for j in range(t["entryIdx"], t["exitIdx"] + 1):
            k = day_pos.get(s.dates[j])
            if k is None:
                continue
            held[k] += 1
            if j == t["exitIdx"] and not t["open"]:
                realized_on[k] += POSITION_NTD * t["ret"] / 100          # 出場那天：已實現（含手續費、稅）
            else:
                pnl_day[k] += POSITION_NTD * (s.close[j] / t["entry"] - 1)   # 持有中：照當天收盤的未實現
    # 每天收盤的累計損益＝之前已實現的合計＋當天還抱著的未實現
    curve = []
    acc = 0.0
    for k in range(len(days)):
        acc += realized_on[k]
        curve.append(acc + pnl_day[k])
    max_conc = max(held) if held else 0
    capital = max_conc * POSITION_NTD
    total = curve[-1] if curve else 0.0
    mdd = 0.0
    if capital > 0:
        peak = capital
        for v in curve:
            eq = capital + v
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100)

    def idx_ret(series_: list[tuple[str, float]]) -> tuple[float | None, list[list[Any]]]:
        if not series_ or not days:
            return None, []
        m = dict(series_)
        base = next((m[d] for d in days if d in m), None)
        if not base:
            return None, []
        pts = [[d, round((m[d] / base - 1) * 100, 2)] for d in days if d in m]
        return (pts[-1][1] if pts else None), pts

    taiex_ret, taiex_pts = idx_ret(index.get("TAIEX", []))
    otc_ret, otc_pts = idx_ret(index.get("TPEx", []))
    step = max(1, len(days) // 125)
    sum_w, sum_l = sum(wins), -sum(losses)
    return {
        "trades": len(closed), "open": len(trades) - len(closed), "wins": len(wins), "losses": len(losses),
        "winRate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avgRet": round(sum(t["ret"] for t in closed) / len(closed), 2) if closed else None,
        "avgWin": round(sum_w / len(wins), 2) if wins else None,
        "avgLoss": round(-sum_l / len(losses), 2) if losses else None,
        "payoff": round((sum_w / len(wins)) / (sum_l / len(losses)), 2) if wins and losses and sum_l > 0 else None,
        "profitFactor": round(sum_w / sum_l, 2) if sum_l > 0 else None,
        "maxConcurrent": max_conc, "maxCapital": capital,
        "totalPnl": round(total), "retOnCapital": round(total / capital * 100, 1) if capital else None,
        "mdd": round(mdd, 1), "taiexRet": taiex_ret, "otcRet": otc_ret,
        "curve": [[days[k], round(curve[k] / capital * 100, 2) if capital else 0.0] for k in range(0, len(days), step)] + ([[days[-1], round(curve[-1] / capital * 100, 2) if capital else 0.0]] if days and (len(days) - 1) % step else []),
        "taiex": taiex_pts[::step], "otc": otc_pts[::step],
    }


# ------------------------------------------------------------------ 個股日K附加（個股頁主力成本線、散戶副圖用）

def daily_extras(code: str) -> dict[str, dict[str, int]]:
    """{日期: {"instNet": 外資＋投信淨買（張）, "marginBal": 融資餘額（張）}}。2026-11-26 使用者：個股頁加主力成本線（估）、散戶副圖（近似）。
    法人／融資先用官方表，沒有的日子用 FinMind 回補的（一年）。"""
    from chips_daily import _schema as chips_schema
    from margin_balance import _schema as margin_schema

    code = str(code).strip().upper()
    out: dict[str, dict[str, int]] = {}
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        chips_schema(connection)
        margin_schema(connection)
        for row in connection.execute("SELECT trade_date, foreign_net, trust_net FROM model_inst_daily WHERE stock_code = ?", (code,)):
            out.setdefault(str(row["trade_date"]), {})["instNet"] = round((int(row["foreign_net"]) + int(row["trust_net"])) / 1000)
        for row in connection.execute("SELECT trade_date, foreign_net, trust_net FROM institutional_daily WHERE stock_code = ?", (code,)):
            out.setdefault(str(row["trade_date"]), {})["instNet"] = round((int(row["foreign_net"]) + int(row["trust_net"])) / 1000)
        for row in connection.execute("SELECT trade_date, balance FROM model_margin_daily WHERE stock_code = ?", (code,)):
            out.setdefault(str(row["trade_date"]), {})["marginBal"] = int(row["balance"])
        for row in connection.execute("SELECT trade_date, margin_balance FROM margin_daily WHERE stock_code = ?", (code,)):
            out.setdefault(str(row["trade_date"]), {})["marginBal"] = int(row["margin_balance"])
    return out


# ------------------------------------------------------------------ 計算

def compute(now: datetime | None = None) -> dict[str, Any]:
    from heilong_backtest import _load_bars, _market_codes

    started = time.monotonic()
    initialize_database()
    days_all = _trade_dates(BACKTEST_DAYS + WARMUP_DAYS + 5)   # 新到舊
    if len(days_all) < WARMUP_DAYS + 20:
        return {"status": "empty", "reason": "日K不夠"}
    days_all = list(reversed(days_all))
    since, until = days_all[0], days_all[-1]
    bt_days = days_all[-BACKTEST_DAYS:]
    codes = _market_codes(bt_days[0])
    bars = _load_bars(codes, since=since, until=until, adjusted=True)
    inst = _load_inst(since, until)
    inst_days = _inst_dates(since, until)
    tdcc = _load_tdcc(codes)
    revenue = _load_revenue()
    index = _load_index(bt_days[0])
    margin, margin_days = _load_daily_map("model_margin_daily", "balance", since, until)
    daytrade, daytrade_days = _load_daily_map("model_daytrade_daily", "lots", since, until)
    names = _names()
    series: dict[str, StockSeries] = {}
    for code in codes:
        b = bars.get(code)
        if not b or len(b) < 30 or b[-1][0] != until:
            continue
        series[code] = StockSeries(code, b, inst.get(code, {}), inst_days, tdcc.get(code, []), revenue.get(code, {}),
                                   margin.get(code, {}), margin_days, daytrade.get(code, {}), daytrade_days)
    # 先算前九個（多模型共振要看它們發訊號的日子），最後算共振；輸出照 MODELS 的順序
    order = [m for m in MODELS if m["key"] != "multiCombo"] + [m for m in MODELS if m["key"] == "multiCombo"]
    by_key: dict[str, dict[str, Any]] = {}
    for spec in order:
        cond = CONDITIONS[spec["key"]]
        trades: list[dict[str, Any]] = []
        picks = []
        recent = []
        for code, s in series.items():
            start = bisect_left(s.dates, bt_days[0])
            sig = signals_for(s, cond, max(1, start))
            if spec["key"] != "multiCombo":
                s.hits[spec["key"]] = {i for i, _ in sig}
            trades.extend(simulate(s, sig))
            for i, why in sig:
                if s.dates[i] == until:
                    picks.append({"code": code, "name": names.get(code, ""), "close": round(s.close[i], 2),
                                  "pct": round((s.close[i] / s.close[i - 1] - 1) * 100, 2) if i > 0 and s.close[i - 1] else None, "why": why})
                elif len(s.dates) - i <= 5:
                    recent.append({"code": code, "name": names.get(code, ""), "date": s.dates[i], "why": why})
        stats = summarize(trades, series, bt_days, index)
        if spec is not order[0]:
            stats.pop("taiex", None)
            stats.pop("otc", None)
        closed = sorted((t for t in trades if not t["open"]), key=lambda t: t["exitDate"], reverse=True)
        holding = [t for t in trades if t["open"]]
        clean = lambda t: {k: v for k, v in t.items() if k not in ("entryIdx", "exitIdx")} | {"name": names.get(t["code"], "")}   # noqa: E731
        by_key[spec["key"]] = ({
            **{k: spec[k] for k in ("key", "cat", "name", "ref", "idea", "rules")},
            "picks": sorted(picks, key=lambda p: p["code"]),
            "recent": sorted(recent, key=lambda p: (p["date"], p["code"]), reverse=True)[:30],
            "stats": stats,
            "lastTrades": [clean(t) for t in closed[:20]],
            "holding": [clean(t) for t in sorted(holding, key=lambda t: t["entryDate"], reverse=True)[:30]],
        })
    models_out = [by_key[m["key"]] for m in MODELS]
    first_stats = by_key[order[0]["key"]]["stats"]
    index_out = {"taiex": first_stats.pop("taiex", []), "otc": first_stats.pop("otc", []), "taiexRet": first_stats.get("taiexRet"), "otcRet": first_stats.get("otcRet")}
    inst_cov = sorted(d for d in inst_days if d >= bt_days[0])
    return {
        "status": "ok", "asOf": until, "generatedAt": (now or _now()).isoformat(timespec="seconds"),
        "backtest": {"from": bt_days[0], "to": until, "days": len(bt_days), "holdDays": HOLD_DAYS, "position": POSITION_NTD, "costPct": COST_PCT},
        "commonRules": COMMON_RULES, "backtestRules": BACKTEST_RULES, "categories": CATEGORIES, "models": models_out,
        "index": index_out,
        "coverage": {"stocks": len(series), "instDays": len(inst_cov), "instFrom": inst_cov[0] if inst_cov else None,
                     "tdccWeeks": len({w[0] for v in tdcc.values() for w in v}), "revenueStocks": len(revenue),
                     "marginDays": len([d for d in margin_days if d >= bt_days[0]]), "daytradeDays": len([d for d in daytrade_days if d >= bt_days[0]]),
                     "indexDays": {k: len(v) for k, v in index.items()}},
        "computeSeconds": round(time.monotonic() - started, 1),
    }


def _input_key() -> str:
    days = _trade_dates(1)
    with get_connection() as connection:
        _schema(connection)
        _fundamentals_schema(connection)
        inst = connection.execute("SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM model_fetch_log WHERE kind = 'inst' AND rows > 0").fetchone()
        rev = connection.execute("SELECT COUNT(*) AS n FROM model_revenue").fetchone()
        tdcc = connection.execute("SELECT MAX(data_date) AS d FROM tdcc_weekly").fetchone()
        idx = connection.execute("SELECT MAX(trade_date) AS d FROM model_index_daily").fetchone()
    return "|".join(str(x) for x in (days[0] if days else "", inst["d"], inst["n"], rev["n"], tdcc["d"], idx["d"]))


def cached() -> dict[str, Any] | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT payload FROM stock_models_cache WHERE key = 'latest'").fetchone()
    return json.loads(row["payload"]) if row else None


def run_once(*, force: bool = False) -> dict[str, Any]:
    with _lock:
        if _state["running"]:
            return {"status": "busy"}
        _state["running"] = True
    try:
        days = _trade_dates(BACKTEST_DAYS + 5)
        _state["backfill"] = backfill(days)
        key = _input_key()
        if not force and key == _state.get("computedKey") and cached():
            return {"status": "unchanged", "key": key}
        payload = compute()
        if payload.get("status") == "ok":
            with get_connection() as connection:
                _schema(connection)
                connection.execute("INSERT OR REPLACE INTO stock_models_cache (key, input_key, payload, at) VALUES ('latest', ?, ?, ?)",
                                   (key, json.dumps(payload, ensure_ascii=False), _now().isoformat(timespec="seconds")))
            _state["computedKey"] = key
            _state["computeSeconds"] = payload.get("computeSeconds")
        _state["lastError"] = None
        return {"status": payload.get("status"), "key": key}
    except Exception as exc:  # noqa: BLE001
        logger.exception("選股模型計算失敗")
        _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
        return {"status": "error", "error": _state["lastError"]}
    finally:
        _state["running"] = False
        _state["lastRunAt"] = _now().isoformat(timespec="seconds")


def status() -> dict[str, Any]:
    return {"status": "ok", **{k: v for k, v in _state.items()}}


def start_stock_models_collector() -> None:
    global _started
    if _started or os.getenv("HANSTOCK_STOCK_MODELS_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return
    _started = True

    def loop() -> None:
        time.sleep(max(0, int(os.getenv("HANSTOCK_STOCK_MODELS_STARTUP_DELAY", "180"))))
        while True:
            hm = _now().strftime("%H:%M")
            if not ("08:30" <= hm <= "13:40"):   # 盤中不算，不跟即時行情搶資源
                run_once()
            time.sleep(POLL_SECONDS)

    threading.Thread(target=loop, name="stock-models", daemon=True).start()
