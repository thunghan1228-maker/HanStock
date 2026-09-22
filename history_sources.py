"""歷史分 K 備援來源鏈：永豐 kbars 額度用完或失敗時，改向 FinMind、再向 Yahoo 拿每分鐘價量。

只提供價量（open/high/low/close/volume），沒有內外盤與主力欄位，給 K 線圖歷史、櫃買指數
MA20、收盤後 5 分 K 訊號校正、主力累計翻多空重播用；主力副圖的逐筆回補仍只有永豐能做。
兩個來源的細節（FinMind 分 K 資料表欄位、Yahoo 非官方 chart API）在開發環境連不到外網
無法驗證，所以解析寫得寬鬆、任何來源拿不到就往下一個走，並由 /api/hub/history-sources
提供自檢（probe 會真的各打一次，回傳筆數與首尾 K 棒）。FinMind 的資料集名稱與成交量單位
也在正式環境自己對：被 422 拒絕就從回應的允許清單挑分 K 資料集重打，成交量第一次拿到時
跟 Yahoo 同幾天的總量比一次，差上千倍就是股數。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from otc_index import ONE_MIN_MS, TW_TZ, is_regular_otc_session, taipei_trade_date

logger = logging.getLogger("hanstock.history_sources")
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
# FinMind 的台股分 K 資料表叫 TaiwanStockKBar（欄位 date/minute/stock_id/open/max/min/close/volume）；
# 正式環境實測舊預設 TaiwanStockPriceMinute 被 422 拒絕。FinMind 的 422 內容會把全部允許的
# 資料集名稱列出來，所以被拒時直接從清單挑分 K 資料集重打一次，不用再改設定重新部署。
FINMIND_MINUTE_DATASET = os.getenv("HANSTOCK_FINMIND_MINUTE_DATASET", "").strip() or "TaiwanStockKBar"
FINMIND_MINUTE_DATASET_CANDIDATES = ("TaiwanStockKBar", "TaiwanStockPriceMinute", "TaiwanStockMinutePrice")
# 正式環境實測：分 K 資料表一次只給一天（帶 end_date 跨日就回 400「size is too large, we only send
# one day data」），所以多日範圍要逐個交易日打；一檔最多打最近幾天，免得 30 天的 K 線歷史一檔
# 就吃掉幾十次額度（Yahoo 本來也只有最近 7 天的 1 分 K）。
FINMIND_MAX_DAYS_PER_CALL = max(1, int(os.getenv("HANSTOCK_FINMIND_MAX_DAYS_PER_CALL", "") or 7))
# FinMind 日 K 的 Trading_Volume 是「股」，逐筆與分 K 多半是「張」，沒驗證過就別猜：auto 會在
# 第一次拿到分 K 時拿 Yahoo 同幾天的總量比一次（差上千倍就是股數），環境變數也可硬指定 lots/shares。
FINMIND_MINUTE_VOLUME_UNIT = os.getenv("HANSTOCK_FINMIND_MINUTE_VOLUME_UNIT", "").strip().lower() or "auto"
FINMIND_SHARES_RATIO_THRESHOLD = 30.0
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_MAX_DAYS_PER_REQUEST = 7
OTC_INDEX_YAHOO_SYMBOL = os.getenv("HANSTOCK_OTC_INDEX_YAHOO_SYMBOL", "^TWOII")
OTC_INDEX_CODE = "OTC_INDEX"
# FinMind 的指數 data_id：日 K 資料表用 TAIEX / TPEx，分 K 是否也給指數未驗證，只在 Yahoo 兩種
# 週期都拿不到時才試，被拒也不開斷路器（別因為指數拖累個股）。
FINMIND_OTC_INDEX_ID = os.getenv("HANSTOCK_FINMIND_OTC_INDEX_ID", "").strip() or "TPEx"
YAHOO_INDEX_INTERVALS = ("1m", "5m")
SOURCE_ORDER = ("finmind", "yahoo")
# FinMind 回 4xx（資料集名稱/參數被拒、權限不足）時不是打第二次就會好：連續失敗就
# 先停一段時間，不然全族群幾百檔每檔都白打一次。429 只是限流，停短一點。
FINMIND_REJECT_BLOCK_SECONDS = 600.0
FINMIND_RATE_LIMIT_BLOCK_SECONDS = 60.0
Fetcher = Callable[[str, dict[str, str]], Any]


class SourceHttpError(RuntimeError):
    """帶 HTTP 狀態碼與回應內容的錯誤：FinMind 的驗證錯誤內容會列出允許的資料集名稱，
    probe 看得到才有辦法對出正確參數。"""

    def __init__(self, code: int, reason: str, body: str) -> None:
        super().__init__(f"HTTP {code} {reason}: {body[:600]}".strip())
        self.code = code
        self.body = body


_status_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {
    name: {
        "calls": 0, "successes": 0, "lastSuccessAt": None, "lastBars": 0,
        "lastError": None, "lastErrorAt": None, "lastSymbol": None,
    }
    for name in SOURCE_ORDER
}
_finmind_blocked_until = 0.0
_finmind_dataset_override: Optional[str] = None
_finmind_permitted_datasets: list[str] = []
_finmind_volume_unit_detected: Optional[str] = None
_finmind_volume_calibration: Optional[dict[str, Any]] = None
_yahoo_suffix_cache: dict[str, str] = {}


def _reset_runtime_state() -> None:
    """測試用：清掉斷路器、自動選到的資料集、成交量單位校準與 Yahoo 後綴快取。"""
    global _finmind_blocked_until, _finmind_dataset_override, _finmind_permitted_datasets
    global _finmind_volume_unit_detected, _finmind_volume_calibration
    with _status_lock:
        _finmind_blocked_until = 0.0
        _finmind_dataset_override = None
        _finmind_permitted_datasets = []
        _finmind_volume_unit_detected = None
        _finmind_volume_calibration = None
        _yahoo_suffix_cache.clear()


def _finmind_token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


def _default_fetcher(url: str, params: dict[str, str]) -> Any:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"Accept": "application/json,text/plain,*/*", "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"},
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            # FinMind 422 會把允許的資料集整份列兩次（msg 與 ctx.expected），要留夠長才對得出名稱。
            body = exc.read().decode("utf-8", errors="replace")[:20000]
        except Exception:  # noqa: BLE001
            body = ""
        raise SourceHttpError(exc.code, str(exc.reason), body) from exc


def _record(name: str, *, symbol: str, bars: Optional[list] = None, error: Any = None) -> None:
    with _status_lock:
        entry = _status[name]
        entry["calls"] += 1
        entry["lastSymbol"] = symbol
        now = datetime.now(TW_TZ).isoformat(timespec="seconds")
        if error is None:
            entry["lastBars"] = len(bars or [])
            if bars:
                entry["successes"] += 1
                entry["lastSuccessAt"] = now
        else:
            entry["lastError"] = f"{type(error).__name__}: {error}"[:600]
            entry["lastErrorAt"] = now


def _finmind_block(error: Exception) -> None:
    global _finmind_blocked_until
    code = getattr(error, "code", None)
    if code == 429:
        seconds = FINMIND_RATE_LIMIT_BLOCK_SECONDS
    elif isinstance(code, int) and 400 <= code < 500:
        seconds = FINMIND_REJECT_BLOCK_SECONDS
    else:
        return
    with _status_lock:
        _finmind_blocked_until = max(_finmind_blocked_until, time.monotonic() + seconds)


def _finmind_blocked_seconds() -> float:
    with _status_lock:
        return max(0.0, _finmind_blocked_until - time.monotonic())


def _tw_epoch(date_text: str, hour: int = 0, minute: int = 0, second: int = 0) -> int:
    moment = datetime.strptime(date_text, "%Y-%m-%d").replace(hour=hour, minute=minute, second=second, tzinfo=TW_TZ)
    return int(moment.timestamp())


def _lots(value: Any, unit: str = "shares") -> int:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if unit == "shares":
        number = number / 1000
    return max(0, int(number))


def _make_bar(ts_ms: int, open_: Any, high: Any, low: Any, close: Any, volume_lots: int) -> Optional[dict[str, Any]]:
    try:
        values = [float(open_), float(high), float(low), float(close)]
    except (TypeError, ValueError):
        return None
    if any(value != value for value in values) or min(values) <= 0:
        return None
    if not is_regular_otc_session(ts_ms):
        return None
    return {
        "ts": ts_ms, "open": values[0], "high": values[1], "low": values[2], "close": values[3],
        "volume": volume_lots, "tick_count": 1,
    }


def _dedupe_sorted(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {int(bar["ts"]): bar for bar in bars}
    return [merged[ts] for ts in sorted(merged)]


def _date_chunks(start_date: str, end_date: str, max_days: int):
    cursor = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        yield cursor.isoformat(), chunk_end.isoformat()
        cursor = chunk_end + timedelta(days=1)


# ---------------------------------------------------------------- FinMind

_LABEL_RE = re.compile(r"^(\d{1,2}):(\d{2})")
_QUOTED_NAME_RE = re.compile(r"'([A-Za-z][A-Za-z0-9_]*)'")
_MINUTE_DATASET_RE = re.compile(r"^TaiwanStock.*(KBar|Minute)", re.IGNORECASE)


def _finmind_dataset() -> str:
    with _status_lock:
        return _finmind_dataset_override or FINMIND_MINUTE_DATASET


def permitted_datasets_from_error(error: Any) -> list[str]:
    """從 FinMind 的 422 驗證錯誤撈出允許的資料集名稱：FastAPI 的 enum 錯誤會在 msg 與
    ctx.expected 用單引號把整份清單列出來。內容被截斷、不是完整 JSON 時退回用正則從原文撈。"""
    body = str(getattr(error, "body", "") or "")
    if "dataset" not in body:
        return []
    texts: list[str] = []
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        details = payload.get("detail")
        for item in details if isinstance(details, list) else []:
            if not isinstance(item, dict):
                continue
            if "dataset" not in [str(part) for part in (item.get("loc") or [])]:
                continue
            texts.append(str(item.get("msg") or ""))
            context = item.get("ctx")
            if isinstance(context, dict):
                texts.append(str(context.get("expected") or ""))
    else:
        texts.append(body)
    names: list[str] = []
    for chunk in texts:
        for name in _QUOTED_NAME_RE.findall(chunk):
            if name not in names:
                names.append(name)
    return names


def _pick_minute_dataset(permitted: list[str], rejected: str) -> Optional[str]:
    """允許清單裡挑分 K 資料集：先看設定值與已知候選，再找名稱像分 K 的。"""
    names = [name for name in permitted if name != rejected]
    for candidate in (FINMIND_MINUTE_DATASET, *FINMIND_MINUTE_DATASET_CANDIDATES):
        if candidate in names:
            return candidate
    for name in names:
        if _MINUTE_DATASET_RE.match(name):
            return name
    return None


def _note_rejected_dataset(error: Exception, rejected: str) -> Optional[str]:
    """被拒的資料集若附了允許清單就記下來，回傳可改用的分 K 資料集（清單裡沒有就 None）。"""
    global _finmind_dataset_override, _finmind_permitted_datasets
    permitted = permitted_datasets_from_error(error)
    if not permitted:
        return None
    replacement = _pick_minute_dataset(permitted, rejected)
    with _status_lock:
        _finmind_permitted_datasets = list(permitted)
        if replacement:
            _finmind_dataset_override = replacement
    if replacement:
        logger.warning("[HistorySources] FinMind 拒絕資料集 %s，改用允許清單裡的 %s", rejected, replacement)
    else:
        logger.warning("[HistorySources] FinMind 拒絕資料集 %s，允許清單裡找不到分 K 資料集: %s", rejected, ", ".join(permitted))
    return replacement


def finmind_request_days(start_date: str, end_date: str, max_days: Optional[int] = None) -> list[str]:
    """範圍內要逐日打的交易日（跳過週末），只留最近 max_days 天，由舊到新。"""
    limit = max_days if max_days is not None else FINMIND_MAX_DAYS_PER_CALL
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days[-limit:] if limit > 0 else days


def _finmind_request(call: Fetcher, code: str, day: str, token: str) -> Any:
    """打一天的分 K（FinMind 分 K 一次只給一天，end_date 要留空）。資料集被拒且回應附了
    允許清單就換名立刻重打一次。"""
    dataset = _finmind_dataset()
    for attempt in (1, 2):
        try:
            payload = call(FINMIND_DATA_URL, {"dataset": dataset, "data_id": code, "start_date": day, "token": token})
        except Exception as exc:
            replacement = _note_rejected_dataset(exc, dataset)
            if replacement and attempt == 1:
                dataset = replacement
                continue
            raise
        if isinstance(payload, dict) and payload.get("status") not in (None, 200) and not payload.get("data"):
            raise SourceHttpError(int(payload.get("status") or 0), "FinMind", str(payload.get("msg"))[:300])
        return payload
    return None


def parse_finmind_minute_rows(rows: Any, start_date: str, end_date: str, *, volume_unit: str = "lots") -> list[dict[str, Any]]:
    """把 FinMind 分 K 列轉成 bar-start 1 分 K。分鐘標籤是收棒還是起始時間官方沒說死：
    同一天若出現 09:00 就當起始時間，否則當收棒時間（跟 Shioaji kbars 一樣減一分鐘）。
    volume_unit 是列裡成交量的單位（lots 原樣、shares 除以 1000）。"""
    if not isinstance(rows, list):
        return []
    by_date: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_text = str(row.get("date") or "")[:10]
        if not (start_date <= date_text <= end_date):
            continue
        label = str(row.get("minute") or row.get("Time") or row.get("time") or "").strip()
        match = _LABEL_RE.match(label)
        if not match:
            continue
        by_date.setdefault(date_text, []).append((int(match.group(1)) * 60 + int(match.group(2)), row))
    bars: list[dict[str, Any]] = []
    for date_text, items in by_date.items():
        labels_are_bar_start = any(minute_of_day == 9 * 60 for minute_of_day, _row in items)
        day_start_ms = _tw_epoch(date_text) * 1000
        for minute_of_day, row in items:
            start_minute = minute_of_day if labels_are_bar_start else minute_of_day - 1
            volume = _lots(row.get("volume", row.get("Trading_Volume", 0)), volume_unit)
            bar = _make_bar(
                day_start_ms + start_minute * ONE_MIN_MS,
                row.get("open"), row.get("max", row.get("high")), row.get("min", row.get("low")), row.get("close"), volume,
            )
            if bar:
                bars.append(bar)
    return _dedupe_sorted(bars)


def _volume_by_date(bars: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for bar in bars:
        day = taipei_trade_date(int(bar["ts"]))
        totals[day] = totals.get(day, 0) + int(bar.get("volume") or 0)
    return totals


def _finmind_volume_unit(
    code: str,
    start_date: str,
    end_date: str,
    raw_bars: list[dict[str, Any]],
    *,
    market: Optional[str],
    fetcher: Optional[Fetcher],
) -> str:
    """分 K 成交量單位：環境變數指定就照用；auto 則第一次拿到資料時跟 Yahoo 同幾天的總量比一次，
    差上千倍就是股數，之後整個程序沿用。Yahoo 拿不到就先當張數，下一次再校準。"""
    global _finmind_volume_unit_detected, _finmind_volume_calibration
    if FINMIND_MINUTE_VOLUME_UNIT in ("lots", "shares"):
        return FINMIND_MINUTE_VOLUME_UNIT
    with _status_lock:
        detected = _finmind_volume_unit_detected
    if detected:
        return detected
    finmind_by_date = _volume_by_date(raw_bars)
    try:
        yahoo_bars = fetch_yahoo_minute_bars(code, start_date, end_date, market=market, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[HistorySources] FinMind 成交量單位暫時校準不了（Yahoo %s 失敗）: %s", code, exc)
        return "lots"
    yahoo_by_date = _volume_by_date(yahoo_bars)
    common = sorted(day for day, total in finmind_by_date.items() if total > 0 and yahoo_by_date.get(day, 0) > 0)
    if not common:
        return "lots"
    finmind_total = sum(finmind_by_date[day] for day in common)
    yahoo_total = sum(yahoo_by_date[day] for day in common)
    ratio = finmind_total / yahoo_total
    unit = "shares" if ratio >= FINMIND_SHARES_RATIO_THRESHOLD else "lots"
    with _status_lock:
        _finmind_volume_unit_detected = unit
        _finmind_volume_calibration = {
            "code": code, "dates": common, "finmindRaw": finmind_total, "yahooLots": yahoo_total,
            "ratio": round(ratio, 3), "at": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        }
    logger.info("[HistorySources] FinMind 分 K 成交量判定為 %s（%s 與 Yahoo 總量比 %.3f）", unit, code, ratio)
    return unit


def fetch_finmind_minute_bars(
    code: str,
    start_date: str,
    end_date: str,
    *,
    market: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    ignore_block: bool = False,
    block_on_error: bool = True,
) -> list[dict[str, Any]]:
    token = _finmind_token()
    if not token:
        return []
    if not ignore_block and _finmind_blocked_seconds() > 0:
        return []
    call = fetcher or _default_fetcher
    is_index = str(code).strip().upper() == OTC_INDEX_CODE
    symbol = FINMIND_OTC_INDEX_ID if is_index else str(code).strip().upper()
    rows: list[Any] = []
    try:
        # 逐日打；中途失敗（限流、被拒）就整個放棄讓鏈往 Yahoo 走，不然拿到缺天的資料還當成功。
        for day in finmind_request_days(start_date, end_date):
            payload = _finmind_request(call, symbol, day, token)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                rows.extend(data)
    except Exception as exc:
        _record("finmind", symbol=symbol, error=exc)
        if block_on_error:
            _finmind_block(exc)
        raise
    bars = parse_finmind_minute_rows(rows, start_date, end_date)
    # 指數沒有成交量可比，不做單位校準。
    if bars and not is_index and _finmind_volume_unit(code, start_date, end_date, bars, market=market, fetcher=fetcher) == "shares":
        for bar in bars:
            bar["volume"] = int(bar["volume"] / 1000)
    _record("finmind", symbol=symbol, bars=bars)
    return bars


# ------------------------------------------------------------------ Yahoo

def _yahoo_symbols(code: str, market: Optional[str]) -> list[str]:
    code = str(code).strip().upper()
    if code == OTC_INDEX_CODE:
        return [OTC_INDEX_YAHOO_SYMBOL]
    if code.startswith("^"):
        return [code]
    market = (market or "").upper()
    if market == "OTC":
        return [f"{code}.TWO"]
    if market == "TSE":
        return [f"{code}.TW"]
    return [f"{code}.TW", f"{code}.TWO"]


def parse_yahoo_chart(payload: Any, start_date: str, end_date: str) -> list[dict[str, Any]]:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        return []
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        return []
    result = results[0] or {}
    timestamps = result.get("timestamp") or []
    quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens = quote_block.get("open") or []
    highs = quote_block.get("high") or []
    lows = quote_block.get("low") or []
    closes = quote_block.get("close") or []
    volumes = quote_block.get("volume") or []
    bars: list[dict[str, Any]] = []
    for index, ts_sec in enumerate(timestamps):
        try:
            ts_ms = int(ts_sec) * 1000
        except (TypeError, ValueError):
            continue
        if not (start_date <= taipei_trade_date(ts_ms) <= end_date):
            continue

        def pick(values: list[Any]) -> Any:
            return values[index] if index < len(values) else None

        bar = _make_bar(ts_ms, pick(opens), pick(highs), pick(lows), pick(closes), _lots(pick(volumes), "shares"))
        if bar:
            bars.append(bar)
    return _dedupe_sorted(bars)


def fetch_yahoo_minute_bars(
    code: str,
    start_date: str,
    end_date: str,
    *,
    market: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
    symbol: Optional[str] = None,
    interval: str = "1m",
) -> list[dict[str, Any]]:
    """Yahoo 非官方 chart API：1 分 K 只保留最近約 7 天、單次最多 7 天，超過就分段。
    市場別不確定時先試上市（.TW）再試上櫃（.TWO）。interval 可改 5m（指數有時只給 5 分 K）。"""
    call = fetcher or _default_fetcher
    code_key = str(code).strip().upper()
    if symbol:
        symbols = [symbol]
    else:
        symbols = _yahoo_symbols(code, market)
        # 上次哪個後綴成功就先試那個：stocks 表沒有市場別的代號才不用每次先吃一個 404。
        with _status_lock:
            known = _yahoo_suffix_cache.get(code_key)
        if known and known in symbols:
            symbols = [known] + [item for item in symbols if item != known]
    last_error: Optional[Exception] = None
    for sym in symbols:
        bars: list[dict[str, Any]] = []
        try:
            for chunk_start, chunk_end in _date_chunks(start_date, end_date, YAHOO_MAX_DAYS_PER_REQUEST):
                payload = call(YAHOO_CHART_URL + quote(sym, safe=""), {
                    "interval": interval, "period1": str(_tw_epoch(chunk_start)),
                    "period2": str(_tw_epoch(chunk_end, 23, 59, 59)), "includePrePost": "false",
                })
                bars.extend(parse_yahoo_chart(payload, start_date, end_date))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        if bars:
            bars = _dedupe_sorted(bars)
            if not symbol:
                with _status_lock:
                    _yahoo_suffix_cache[code_key] = sym
            _record("yahoo", symbol=sym, bars=bars)
            return bars
    if last_error is not None:
        _record("yahoo", symbol=symbols[-1], error=last_error)
        raise last_error
    _record("yahoo", symbol=symbols[-1], bars=[])
    return []


# ------------------------------------------------------------------ chain

def fetch_minute_bars_chain(
    code: str,
    start_date: str,
    end_date: str,
    *,
    market: Optional[str] = None,
    fetcher: Optional[Fetcher] = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """依序 FinMind → Yahoo，第一個拿到資料的來源勝出；都沒有回 ([], None)。"""
    for name in SOURCE_ORDER:
        try:
            if name == "finmind":
                bars = fetch_finmind_minute_bars(code, start_date, end_date, market=market, fetcher=fetcher)
            else:
                bars = fetch_yahoo_minute_bars(code, start_date, end_date, market=market, fetcher=fetcher)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[HistorySources] %s %s 失敗: %s", name, code, exc)
            continue
        if bars:
            return bars, name
    return [], None


def stock_market(code: str) -> Optional[str]:
    """stocks 表的市場別（TSE/OTC），Yahoo 代號後綴要用；查不到回 None（兩種都試）。"""
    try:
        from database import get_connection

        with get_connection() as connection:
            row = connection.execute(
                "SELECT market FROM stocks WHERE stock_code = ?", (str(code).strip().upper(),)
            ).fetchone()
        return str(row["market"]).upper() if row and row["market"] else None
    except Exception:  # noqa: BLE001
        return None


def history_sources_status() -> dict[str, Any]:
    with _status_lock:
        stats = {name: dict(entry) for name, entry in _status.items()}
        dataset_override = _finmind_dataset_override
        permitted = list(_finmind_permitted_datasets)
        volume_unit_detected = _finmind_volume_unit_detected
        volume_calibration = dict(_finmind_volume_calibration) if _finmind_volume_calibration else None
    return {
        "order": list(SOURCE_ORDER),
        "finmind": {
            "configured": bool(_finmind_token()),
            "dataset": dataset_override or FINMIND_MINUTE_DATASET, "configuredDataset": FINMIND_MINUTE_DATASET,
            "datasetAutoSelected": dataset_override is not None, "maxDaysPerCall": FINMIND_MAX_DAYS_PER_CALL,
            "permittedDatasetCount": len(permitted),
            "permittedStockDatasets": [name for name in permitted if name.startswith("TaiwanStock")],
            "volumeUnit": FINMIND_MINUTE_VOLUME_UNIT, "volumeUnitDetected": volume_unit_detected,
            "volumeCalibration": volume_calibration,
            "blockedForSeconds": round(_finmind_blocked_seconds()), **stats["finmind"],
        },
        "yahoo": {"otcIndexSymbol": OTC_INDEX_YAHOO_SYMBOL, **stats["yahoo"]},
    }


def _probe_one(runner: Callable[[], list[dict[str, Any]]]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        bars = runner()
        return {
            "ok": bool(bars), "bars": len(bars),
            "first": bars[0] if bars else None, "last": bars[-1] if bars else None,
            "elapsedMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "error": f"{type(exc).__name__}: {exc}"[:700],
            "elapsedMs": round((time.monotonic() - started) * 1000),
        }


def probe_history_sources(code: str, trade_date: str, *, market: Optional[str] = None, fetcher: Optional[Fetcher] = None) -> dict[str, Any]:
    """真的各打一次 FinMind 與 Yahoo（不碰永豐額度），回傳筆數與首尾 K 棒，用來驗證
    欄位、分鐘標籤與成交量單位是否正確。code 是 OTC_INDEX／TPEX／^TWOII 時改探櫃買指數：
    Yahoo 1 分 K、5 分 K 與 FinMind 櫃買分 K 各打一次。"""
    code_key = str(code).strip().upper()
    if code_key in (OTC_INDEX_CODE, "TPEX", "^TWOII"):
        out: dict[str, Any] = {"code": OTC_INDEX_CODE, "tradeDate": trade_date, "market": "INDEX", "yahooSymbol": OTC_INDEX_YAHOO_SYMBOL}
        for interval in YAHOO_INDEX_INTERVALS:
            out[f"yahoo{interval}"] = _probe_one(
                lambda interval=interval: fetch_yahoo_minute_bars(OTC_INDEX_CODE, trade_date, trade_date, fetcher=fetcher, interval=interval)
            )
        if _finmind_token():
            out["finmind"] = _probe_one(
                lambda: fetch_finmind_minute_bars(OTC_INDEX_CODE, trade_date, trade_date, fetcher=fetcher, ignore_block=True, block_on_error=False)
            )
        else:
            out["finmind"] = {"ok": False, "error": "FINMIND_TOKEN 未設定"}
        out["finmind"].update({"dataset": _finmind_dataset(), "dataId": FINMIND_OTC_INDEX_ID})
        return out

    out = {"code": code, "tradeDate": trade_date, "market": market}
    if _finmind_token():
        # probe 是人在看，斷路器擋住也照打，才看得到最新的錯誤內容。
        out["finmind"] = _probe_one(
            lambda: fetch_finmind_minute_bars(code, trade_date, trade_date, market=market, fetcher=fetcher, ignore_block=True)
        )
    else:
        out["finmind"] = {"ok": False, "error": "FINMIND_TOKEN 未設定"}
    out["finmind"]["dataset"] = _finmind_dataset()
    out["yahoo"] = _probe_one(lambda: fetch_yahoo_minute_bars(code, trade_date, trade_date, market=market, fetcher=fetcher))
    return out
