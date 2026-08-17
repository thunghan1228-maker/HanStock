"""日線三角收斂盤中預覽：以前一日完整日 K 搭配今日即時暫定日 K。"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from database import get_connection
from intraday_signal_store import save_intraday_signals
from triangle_screener import MAX_BARS, evaluate_triangle

logger = logging.getLogger("hanstock.triangle_intraday")
TAIPEI = ZoneInfo("Asia/Taipei")
TARGET_STATUSES = ("放量突破", "突破待量", "接近突破")
STATUS_ORDER = {status: index for index, status in enumerate(TARGET_STATUSES)}
SIGNAL_KIND_BY_STATUS = {
    "接近突破": "triangleNearBreakout",
    "突破待量": "triangleBreakoutPendingVolume",
    "放量突破": "triangleVolumeBreakout",
}
SIGNAL_LABEL_BY_STATUS = {
    status: f"日線三角收斂｜{status}" for status in TARGET_STATUSES
}
_cache_lock = threading.RLock()
_candidate_cache_date = ""
_candidate_cache: list[dict[str, Any]] = []
_runtime: dict[str, Any] = {
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "candidate_count": 0,
    "quote_count": 0,
    "matched_count": 0,
    "inserted_signal_count": 0,
}


def _result_path() -> Path:
    from paths import DATA_DIR

    return DATA_DIR / "triangle_intraday_latest.json"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "---", "null", "None"}:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _book_price(value: Any) -> float | None:
    first = str(value or "").split("_")[0]
    return _number(first)


def _exchange_channel(code: str, market: str) -> str:
    normalized = str(market or "").strip().upper()
    exchange = "otc" if normalized in {"OTC", "TPEX", "上櫃", "櫃買"} else "tse"
    return f"{exchange}_{code}.tw"


def _load_daily_universe() -> list[dict[str, Any]]:
    """一次載入每檔最近 90 根，避免全市場逐檔開啟 SQLite 連線。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH recent AS (
                SELECT stock_code, bar_time, open, high, low, close, volume,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code ORDER BY bar_time DESC
                       ) AS row_number
                FROM bars_1d
            )
            SELECT s.stock_code, s.stock_name, s.market,
                   r.bar_time, r.open, r.high, r.low, r.close, r.volume
            FROM stocks AS s
            JOIN recent AS r ON r.stock_code = s.stock_code
            WHERE r.row_number <= ?
            ORDER BY s.stock_code, r.bar_time
            """,
            (MAX_BARS,),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row["stock_code"])
        entry = grouped.setdefault(code, {
            "stock_code": code,
            "stock_name": str(row["stock_name"]),
            "market": str(row["market"] or ""),
            "bars": [],
        })
        entry["bars"].append({
            "bar_time": str(row["bar_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"] or 0),
        })
    return list(grouped.values())


def _base_candidates(trade_date: str) -> list[dict[str, Any]]:
    global _candidate_cache_date, _candidate_cache
    with _cache_lock:
        if _candidate_cache_date == trade_date and _candidate_cache:
            return _candidate_cache

    candidates: list[dict[str, Any]] = []
    for item in _load_daily_universe():
        try:
            base = evaluate_triangle(item["stock_code"], item["bars"])
        except Exception:  # noqa: BLE001
            continue
        if base.get("passed"):
            candidates.append(item)

    with _cache_lock:
        _candidate_cache_date = trade_date
        _candidate_cache = candidates
    return candidates


def _fetch_json(url: str, timeout: float = 18.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Language": "zh-TW,zh;q=0.9",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
            "User-Agent": "Mozilla/5.0 HanStock-IntradayTriangle/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.load(response)


def _fetch_mis_batch(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    params = urllib.parse.urlencode({
        "ex_ch": "|".join(_exchange_channel(item["stock_code"], item["market"]) for item in candidates),
        "json": "1",
        "delay": "0",
        "_": str(int(time.time() * 1000)),
    })
    payload = _fetch_json(f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?{params}")
    result: dict[str, dict[str, Any]] = {}
    for row in payload.get("msgArray", []) if isinstance(payload, dict) else []:
        code = str(row.get("c") or row.get("ch") or "").split(".")[0].strip().upper()
        if not code:
            continue
        bid = _book_price(row.get("b"))
        ask = _book_price(row.get("a"))
        fallback = (bid + ask) / 2 if bid and ask else bid or ask or _number(row.get("y"))
        result[code] = {
            "open": _number(row.get("o")),
            "high": _number(row.get("h")),
            "low": _number(row.get("l")),
            "close": _number(row.get("z")) or _number(row.get("pz")) or fallback,
            # MIS v 為張數；官方日 K 成交量為股數。
            "volume": int((_number(row.get("v")) or 0) * 1000),
            "quote_ts": int(_number(row.get("tlong")) or 0),
        }
    return result


def _fetch_live_price_batch(codes: list[str]) -> dict[str, float]:
    input_value = urllib.parse.quote(json.dumps({"json": {"tickers": codes}}, separators=(",", ":")))
    payload = _fetch_json(
        f"https://www.hanstock.xyz/api/trpc/stocks.liveQuotes?input={input_value}",
        timeout=20.0,
    )
    rows = (((payload or {}).get("result") or {}).get("data") or {}).get("json", {}).get("rows", [])
    return {
        str(row.get("code") or "").strip().upper(): float(row["price"])
        for row in rows
        if str(row.get("code") or "").strip() and _number(row.get("price")) is not None
    }


def fetch_live_daily_quotes(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    batches = [candidates[index:index + 50] for index in range(0, len(candidates), 50)]
    quotes: dict[str, dict[str, Any]] = {}
    prices: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(batches) * 2))) as executor:
        jobs = {}
        for batch in batches:
            jobs[executor.submit(_fetch_mis_batch, batch)] = "mis"
            jobs[executor.submit(_fetch_live_price_batch, [item["stock_code"] for item in batch])] = "price"
        for future in as_completed(jobs):
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("盤中三角行情批次失敗 (%s): %s", jobs[future], exc)
                continue
            if jobs[future] == "mis":
                quotes.update(payload)
            else:
                prices.update(payload)

    for code, price in prices.items():
        if code in quotes:
            quotes[code]["close"] = price
    return quotes


def evaluate_intraday_candidate(
    candidate: dict[str, Any],
    quote: dict[str, Any],
    trade_date: str,
) -> dict[str, Any] | None:
    close = _number(quote.get("close"))
    open_price = _number(quote.get("open")) or close
    high = _number(quote.get("high")) or close
    low = _number(quote.get("low")) or close
    if close is None or open_price is None or high is None or low is None:
        return None
    high = max(high, open_price, close)
    low = min(low, open_price, close)
    bars = list(candidate.get("bars") or [])
    if bars and str(bars[-1].get("bar_time") or "")[:10] == trade_date:
        bars = bars[:-1]
    bars.append({
        "bar_time": trade_date,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": max(0, int(quote.get("volume") or 0)),
    })
    result = evaluate_triangle(str(candidate["stock_code"]), bars)
    if not result.get("passed") or result.get("status") not in TARGET_STATUSES:
        return None
    return {
        "stock_name": str(candidate.get("stock_name") or candidate["stock_code"]),
        "market": str(candidate.get("market") or ""),
        "preview": True,
        **result,
    }


def _signal_for_row(row: dict[str, Any], trade_date: str, bucket_ts: int) -> dict[str, Any]:
    status = str(row["status"])
    return {
        "tradeDate": trade_date,
        "ticker": str(row["stock_code"]),
        "name": str(row["stock_name"]),
        "groupName": "日線三角收斂",
        "kind": SIGNAL_KIND_BY_STATUS[status],
        "label": SIGNAL_LABEL_BY_STATUS[status],
        "barTs": bucket_ts,
        "price": float(row["close"]),
        "note": (
            f"距上緣 {float(row['distance_to_upper_pct']):+.2f}%｜"
            f"20 日量比 {float(row['volume_ratio_20d']):.2f} 倍｜"
            f"收斂分數 {float(row['score']):.1f}"
        ),
    }


def scan_intraday_triangles(
    now: datetime | None = None,
    *,
    candidate_loader: Callable[[str], list[dict[str, Any]]] = _base_candidates,
    quote_loader: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] = fetch_live_daily_quotes,
) -> dict[str, Any]:
    current = now.astimezone(TAIPEI) if now is not None else datetime.now(TAIPEI)
    trade_date = current.strftime("%Y-%m-%d")
    bucket_ts = int(current.timestamp() // 300 * 300 * 1000)
    candidates = candidate_loader(trade_date)
    quotes = quote_loader(candidates)
    if candidates and not quotes:
        raise RuntimeError("盤中即時日 K 行情暫時無法取得，保留上一版名單")

    rows: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    for candidate in candidates:
        code = str(candidate["stock_code"])
        quote = quotes.get(code)
        if not quote:
            unavailable.append({"stock_code": code, "reason": "盤中行情缺漏"})
            continue
        try:
            result = evaluate_intraday_candidate(candidate, quote, trade_date)
            if result:
                rows.append(result)
        except Exception as exc:  # noqa: BLE001
            unavailable.append({"stock_code": code, "reason": str(exc)})

    rows.sort(key=lambda row: (STATUS_ORDER.get(str(row["status"]), 9), -float(row["score"])))
    inserted = save_intraday_signals([_signal_for_row(row, trade_date, bucket_ts) for row in rows])
    output = {
        "strategy": "日線三角收斂｜盤中預覽",
        "mode": "intraday_preview",
        "trade_date": trade_date,
        "generated_at": current.isoformat(timespec="seconds"),
        "bucket_ts": bucket_ts,
        "summary": {
            "candidate_count": len(candidates),
            "quote_count": len(quotes),
            "matched_count": len(rows),
            "unavailable_count": len(unavailable),
            "inserted_signal_count": len(inserted),
        },
        "rows": rows,
        "unavailable": unavailable[:100],
    }
    path = _result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    with _cache_lock:
        _runtime.update({
            "last_run_at": output["generated_at"],
            "last_success_at": output["generated_at"],
            "last_error": None,
            **output["summary"],
        })
    return output


def load_intraday_triangle_results() -> dict[str, Any]:
    path = _result_path()
    if not path.exists():
        raise RuntimeError("尚無盤中三角收斂預覽，開盤後將每 5 分鐘自動更新。")
    return json.loads(path.read_text(encoding="utf-8"))


def intraday_triangle_status() -> dict[str, Any]:
    with _cache_lock:
        return dict(_runtime)


def record_intraday_triangle_error(error: Exception, now: datetime | None = None) -> None:
    current = now.astimezone(TAIPEI) if now is not None else datetime.now(TAIPEI)
    with _cache_lock:
        _runtime["last_run_at"] = current.isoformat(timespec="seconds")
        _runtime["last_error"] = str(error)
