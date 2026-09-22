"""歷史分 K 備援來源鏈：永豐 kbars 額度用完或失敗時，改向 FinMind、再向 Yahoo 拿每分鐘價量。

只提供價量（open/high/low/close/volume），沒有內外盤與主力欄位，給 K 線圖歷史、櫃買指數
MA20、收盤後 5 分 K 訊號校正、主力累計翻多空重播用；主力副圖的逐筆回補仍只有永豐能做。
兩個來源的細節（FinMind 分 K 資料表欄位、Yahoo 非官方 chart API）在開發環境連不到外網
無法驗證，所以解析寫得寬鬆、任何來源拿不到就往下一個走，並由 /api/hub/history-sources
提供自檢（probe 會真的各打一次，回傳筆數與首尾 K 棒）。
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
FINMIND_MINUTE_DATASET = os.getenv("HANSTOCK_FINMIND_MINUTE_DATASET", "TaiwanStockPriceMinute")
# FinMind 日 K 的 Trading_Volume 是「股」；分 K 未驗證，預設也當股數處理，probe 看到數字
# 不對再用環境變數改成 lots。
FINMIND_MINUTE_VOLUME_UNIT = os.getenv("HANSTOCK_FINMIND_MINUTE_VOLUME_UNIT", "shares").strip().lower()
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_MAX_DAYS_PER_REQUEST = 7
OTC_INDEX_YAHOO_SYMBOL = os.getenv("HANSTOCK_OTC_INDEX_YAHOO_SYMBOL", "^TWOII")
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
        super().__init__(f"HTTP {code} {reason}: {body}".strip())
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
_yahoo_suffix_cache: dict[str, str] = {}


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
            body = exc.read().decode("utf-8", errors="replace")[:500]
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


def parse_finmind_minute_rows(rows: Any, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """把 FinMind 分 K 列轉成 bar-start 1 分 K。分鐘標籤是收棒還是起始時間官方沒說死：
    同一天若出現 09:00 就當起始時間，否則當收棒時間（跟 Shioaji kbars 一樣減一分鐘）。"""
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
            volume = _lots(row.get("volume", row.get("Trading_Volume", 0)), FINMIND_MINUTE_VOLUME_UNIT)
            bar = _make_bar(
                day_start_ms + start_minute * ONE_MIN_MS,
                row.get("open"), row.get("max", row.get("high")), row.get("min", row.get("low")), row.get("close"), volume,
            )
            if bar:
                bars.append(bar)
    return _dedupe_sorted(bars)


def fetch_finmind_minute_bars(
    code: str,
    start_date: str,
    end_date: str,
    *,
    fetcher: Optional[Fetcher] = None,
    ignore_block: bool = False,
) -> list[dict[str, Any]]:
    token = _finmind_token()
    if not token:
        return []
    if not ignore_block and _finmind_blocked_seconds() > 0:
        return []
    call = fetcher or _default_fetcher
    try:
        payload = call(FINMIND_DATA_URL, {
            "dataset": FINMIND_MINUTE_DATASET, "data_id": str(code).strip().upper(),
            "start_date": start_date, "end_date": end_date, "token": token,
        })
    except Exception as exc:
        _record("finmind", symbol=code, error=exc)
        _finmind_block(exc)
        raise
    if isinstance(payload, dict) and payload.get("status") not in (None, 200) and not payload.get("data"):
        error = SourceHttpError(int(payload.get("status") or 0), "FinMind", str(payload.get("msg"))[:300])
        _record("finmind", symbol=code, error=error)
        _finmind_block(error)
        raise error
    bars = parse_finmind_minute_rows(payload.get("data") if isinstance(payload, dict) else None, start_date, end_date)
    _record("finmind", symbol=code, bars=bars)
    return bars


# ------------------------------------------------------------------ Yahoo

def _yahoo_symbols(code: str, market: Optional[str]) -> list[str]:
    code = str(code).strip().upper()
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
) -> list[dict[str, Any]]:
    """Yahoo 非官方 chart API：1 分 K 只保留最近約 7 天、單次最多 7 天，超過就分段。
    市場別不確定時先試上市（.TW）再試上櫃（.TWO）。"""
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
                    "interval": "1m", "period1": str(_tw_epoch(chunk_start)),
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
                bars = fetch_finmind_minute_bars(code, start_date, end_date, fetcher=fetcher)
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
    return {
        "order": list(SOURCE_ORDER),
        "finmind": {
            "configured": bool(_finmind_token()), "dataset": FINMIND_MINUTE_DATASET,
            "volumeUnit": FINMIND_MINUTE_VOLUME_UNIT,
            "blockedForSeconds": round(_finmind_blocked_seconds()), **stats["finmind"],
        },
        "yahoo": {"otcIndexSymbol": OTC_INDEX_YAHOO_SYMBOL, **stats["yahoo"]},
    }


def probe_history_sources(code: str, trade_date: str, *, market: Optional[str] = None, fetcher: Optional[Fetcher] = None) -> dict[str, Any]:
    """真的各打一次 FinMind 與 Yahoo（不碰永豐額度），回傳筆數與首尾 K 棒，用來驗證
    欄位、分鐘標籤與成交量單位是否正確。"""
    out: dict[str, Any] = {"code": code, "tradeDate": trade_date, "market": market}
    for name in SOURCE_ORDER:
        started = time.monotonic()
        try:
            if name == "finmind":
                if not _finmind_token():
                    out[name] = {"ok": False, "error": "FINMIND_TOKEN 未設定"}
                    continue
                # probe 是人在看，斷路器擋住也照打，才看得到最新的錯誤內容。
                bars = fetch_finmind_minute_bars(code, trade_date, trade_date, fetcher=fetcher, ignore_block=True)
            else:
                bars = fetch_yahoo_minute_bars(code, trade_date, trade_date, market=market, fetcher=fetcher)
            out[name] = {
                "ok": bool(bars), "bars": len(bars),
                "first": bars[0] if bars else None, "last": bars[-1] if bars else None,
                "elapsedMs": round((time.monotonic() - started) * 1000),
            }
        except Exception as exc:  # noqa: BLE001
            out[name] = {
                "ok": False, "error": f"{type(exc).__name__}: {exc}"[:700],
                "elapsedMs": round((time.monotonic() - started) * 1000),
            }
    return out
