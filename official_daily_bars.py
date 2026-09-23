"""Backfill HanStock daily bars from official TWSE and TPEx after-hours data."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from database import get_connection, initialize_database

TWSE_DAILY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TPEX_LEGACY_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/"
    "daily_close_quotes/stk_quote_result.php"
)
# 上面兩個TPEx端點經Railway log證實持續回401/403（至少2026-07-21起，見
# HanStock issue追蹤），是這個repo原本唯一的TPEx資料來源，等於OTC股票
# 日K完全沒有新資料。這是新找到的正式OpenAPI替代來源：只回傳「最新一個
# 交易日」的快照，沒有日期參數可以查歷史，所以只能修正「今天/最新」的
# 收集，不能回補7-9月已經缺的歷史缺口。欄位是英文命名，且是研究得來、
# 沒有機會對照真實回應核對確切拼法，所以用關鍵字比對（見_tpex_openapi_
# field）容忍命名差異，解析不到資料時會印出實際欄位名稱方便之後校正。
TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
UTC = timezone.utc

FIELD_ALIASES = {
    "code": {"證券代號", "股票代號", "代號"},
    "name": {"證券名稱", "股票名稱", "名稱"},
    "open": {"開盤價", "開盤"},
    "high": {"最高價", "最高"},
    "low": {"最低價", "最低"},
    "close": {"收盤價", "收盤"},
    "volume": {"成交股數", "成交量", "成交數量"},
}


def _normalise_label(value: Any) -> str:
    return "".join(str(value or "").replace("\u3000", " ").split())


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("＋", "+").replace("－", "-")
    if text in {"", "--", "---", "----", "-", "N/A", "null", "None"}:
        return None
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def _eligible_code(code: str) -> bool:
    code = code.strip().upper()
    return (len(code) == 4 and code.isdigit()) or (
        code.startswith("00") and 5 <= len(code) <= 6
    )


def _table_candidates(payload: dict[str, Any]) -> Iterable[tuple[list[Any], list[Any]]]:
    for table in payload.get("tables", []) or []:
        if isinstance(table, dict):
            fields = table.get("fields") or table.get("columns")
            rows = table.get("data") or table.get("rows")
            if isinstance(fields, list) and isinstance(rows, list):
                yield fields, rows

    for index in range(1, 30):
        fields = payload.get(f"fields{index}")
        rows = payload.get(f"data{index}")
        if isinstance(fields, list) and isinstance(rows, list):
            yield fields, rows

    fields = payload.get("fields")
    rows = payload.get("data")
    if isinstance(fields, list) and isinstance(rows, list):
        yield fields, rows


def _field_indexes(fields: list[Any]) -> dict[str, int] | None:
    normalised = [_normalise_label(field) for field in fields]
    indexes: dict[str, int] = {}
    for key, aliases in FIELD_ALIASES.items():
        for index, label in enumerate(normalised):
            if label in aliases:
                indexes[key] = index
                break
    return indexes if set(indexes) == set(FIELD_ALIASES) else None


def _row_to_bar(
    row: list[Any], indexes: dict[str, int], trade_date: date, market: str
) -> dict[str, Any] | None:
    try:
        code = str(row[indexes["code"]]).strip().upper()
        name = str(row[indexes["name"]]).strip()
    except (IndexError, TypeError):
        return None
    if not _eligible_code(code):
        return None

    prices = {
        key: _number(row[indexes[key]])
        for key in ("open", "high", "low", "close")
    }
    if any(value is None or value <= 0 for value in prices.values()):
        return None
    volume_value = _number(row[indexes["volume"]])
    # 官方欄位「成交股數」單位是股，換算成跟Hub其他資料表(bars_1m/bars_5m)
    # 一致的「張」(1張=1000股)，否則日K成交量會比即時資料大1000倍。
    volume = max(0, int((volume_value or 0) / 1000))
    bar_time = datetime.combine(trade_date, datetime_time.min, tzinfo=UTC)
    return {
        "stock_code": code,
        "stock_name": name,
        "market": market,
        "time": bar_time,
        "open": float(prices["open"]),
        "high": float(prices["high"]),
        "low": float(prices["low"]),
        "close": float(prices["close"]),
        "volume": volume,
    }


def parse_market_payload(
    payload: dict[str, Any], trade_date: date, market: str
) -> list[dict[str, Any]]:
    """Parse modern and legacy official payloads into HanStock daily bars."""
    selected: dict[str, dict[str, Any]] = {}
    for fields, rows in _table_candidates(payload):
        indexes = _field_indexes(fields)
        if indexes is None:
            continue
        for row in rows:
            if not isinstance(row, list):
                continue
            bar = _row_to_bar(row, indexes, trade_date, market)
            if bar:
                selected[bar["stock_code"]] = bar

    # Older TPEx JSON uses aaData without a fields array.
    if not selected and isinstance(payload.get("aaData"), list):
        legacy_fields = [
            "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
            "成交股數", "成交金額", "成交筆數",
        ]
        indexes = _field_indexes(legacy_fields)
        if indexes:
            for row in payload["aaData"]:
                if isinstance(row, list):
                    bar = _row_to_bar(row, indexes, trade_date, market)
                    if bar:
                        selected[bar["stock_code"]] = bar
    return [selected[code] for code in sorted(selected)]


def fetch_json(
    url: str,
    params: dict[str, str],
    *,
    timeout: float = 45.0,
    retries: int = 2,
) -> dict[str, Any]:
    query = urlencode(params)
    request = Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "HanStock/1.0 (+https://hanstock.xyz)",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"官方盤後資料取得失敗：{url}: {last_error}") from last_error


def _roc_date(value: date) -> str:
    return f"{value.year - 1911:03d}/{value.month:02d}/{value.day:02d}"


def fetch_twse_day(
    trade_date: date, *, fetcher: Callable[..., dict[str, Any]] = fetch_json
) -> list[dict[str, Any]]:
    payload = fetcher(
        TWSE_DAILY_URL,
        {
            "date": trade_date.strftime("%Y%m%d"),
            "type": "ALLBUT0999",
            "response": "json",
        },
    )
    rows = parse_market_payload(payload, trade_date, "TSE")
    if not rows:
        # 空結果可能是真的休市，也可能是證交所回應格式悄悄變了、解析不到欄位。
        # 印出 stat／欄位摘要，讓休市跟「解析失敗」在 log 上分得出來，不用每次都猜。
        stat = payload.get("stat") if isinstance(payload, dict) else None
        has_tables = bool(payload.get("tables")) if isinstance(payload, dict) else False
        print(
            f"  · TWSE {trade_date} 空結果診斷：stat={stat!r} tables存在={has_tables}",
            flush=True,
        )
    return rows


def _tpex_openapi_field(entry: dict[str, Any], *substrings: str) -> Any:
    """櫃買OpenAPI用英文欄位名，且拼法是研究得來、沒對照過真實回應；用
    關鍵字比對容忍命名差異（例如Close或ClosingPrice都算close）。"""
    wanted = [s.lower() for s in substrings]
    for key, value in entry.items():
        lowered = str(key).lower()
        if all(s in lowered for s in wanted):
            return value
    return None


def _tpex_openapi_roc_date(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if len(text) < 5:
        return None
    try:
        roc_year = int(text[:-4])
        month = int(text[-4:-2])
        day = int(text[-2:])
        return date(roc_year + 1911, month, day)
    except (ValueError, IndexError):
        return None


def fetch_tpex_openapi_snapshot(
    fetcher: Callable[..., Any] = fetch_json,
) -> tuple[date | None, list[dict[str, Any]]]:
    """櫃買中心正式OpenAPI快照；只回傳「最新一個交易日」的資料，沒有
    日期參數可以查歷史，所以只能拿來確認今天/最新交易日，不能回補更早
    的缺口。回傳（這批資料實際代表的交易日, 個股列表）；呼叫端要自己
    核對這個日期是不是真的是要的那一天，不能假設一定對得上。"""
    try:
        payload = fetcher(TPEX_OPENAPI_URL, {})
    except Exception:  # noqa: BLE001
        return None, []
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None, []
    trade_date = _tpex_openapi_roc_date(_tpex_openapi_field(payload[0], "date"))
    if trade_date is None:
        print(f"  · 櫃買OpenAPI空結果診斷：無法解析交易日期，第一筆鍵={list(payload[0].keys())}", flush=True)
        return None, []
    selected: dict[str, dict[str, Any]] = {}
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        code = str(_tpex_openapi_field(entry, "code") or "").strip().upper()
        if not _eligible_code(code):
            continue
        name = str(_tpex_openapi_field(entry, "name") or "").strip()
        # "close"不是"closing"的子字串（分岔在第5個字母c-l-o-s-[e]對c-l-o-s-[i]ng），
        # 用共同前綴"clos"才能同時比對到Close跟ClosingPrice兩種可能拼法。
        prices = {
            key: _number(_tpex_openapi_field(entry, "clos" if key == "close" else key))
            for key in ("open", "high", "low", "close")
        }
        if any(value is None or value <= 0 for value in prices.values()):
            continue
        # 同樣是股數，換算成「張」跟其他來源單位一致。
        volume = max(0, int((_number(_tpex_openapi_field(entry, "shares")) or 0) / 1000))
        selected[code] = {
            "stock_code": code,
            "stock_name": name or code,
            "market": "OTC",
            "time": datetime.combine(trade_date, datetime_time.min, tzinfo=UTC),
            "open": float(prices["open"]),
            "high": float(prices["high"]),
            "low": float(prices["low"]),
            "close": float(prices["close"]),
            "volume": volume,
        }
    if not selected:
        print(
            f"  · 櫃買OpenAPI空結果診斷：解析到交易日{trade_date}但沒有任何有效個股列，"
            f"第一筆鍵={list(payload[0].keys())}",
            flush=True,
        )
    return trade_date, [selected[code] for code in sorted(selected)]


def fetch_tpex_day(
    trade_date: date, *, fetcher: Callable[..., dict[str, Any]] = fetch_json
) -> list[dict[str, Any]]:
    snapshot_date, snapshot_rows = fetch_tpex_openapi_snapshot(fetcher)
    if snapshot_date == trade_date and snapshot_rows:
        return snapshot_rows
    try:
        payload = fetcher(
            TPEX_DAILY_URL,
            {"date": trade_date.strftime("%Y/%m/%d"), "id": "", "response": "json"},
        )
        rows = parse_market_payload(payload, trade_date, "OTC")
        if rows:
            return rows
    except Exception:  # noqa: BLE001
        pass
    payload = fetcher(
        TPEX_LEGACY_URL,
        {"l": "zh-tw", "o": "json", "d": _roc_date(trade_date), "s": "0,asc,0"},
    )
    return parse_market_payload(payload, trade_date, "OTC")


def fetch_official_day(
    trade_date: date,
    *,
    twse_loader: Callable[[date], list[dict[str, Any]]] = fetch_twse_day,
    tpex_loader: Callable[[date], list[dict[str, Any]]] = fetch_tpex_day,
) -> list[dict[str, Any]]:
    """Use TWSE as the shared trading-day gate before accepting TPEx rows.

    TPEx's historical page can return the latest quote set while echoing a requested
    market-closed date. TWSE returns no rows on the same holidays, so a date is only
    persisted when TWSE confirms that the common Taiwan equity market was open.
    """
    twse_rows = twse_loader(trade_date)
    if not twse_rows:
        return []
    return twse_rows + tpex_loader(trade_date)


def _calendar_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def _save_day(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    stock_rows = {
        row["stock_code"]: (
            row["stock_code"], row["stock_name"], row["market"], updated_at
        )
        for row in rows
    }
    bar_rows = [
        (
            row["stock_code"], row["time"].isoformat(), row["open"], row["high"],
            row["low"], row["close"], row["volume"],
        )
        for row in rows
    ]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO stocks (stock_code, stock_name, market, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                market = excluded.market,
                updated_at = excluded.updated_at
            """,
            stock_rows.values(),
        )
        changes_before_bars = connection.total_changes
        connection.executemany(
            """
            INSERT INTO bars_1d
                (stock_code, bar_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, bar_time) DO NOTHING
            """,
            bar_rows,
        )
        return connection.total_changes - changes_before_bars


def _otc_bars_exist(trade_date: date, minimum: int = 100) -> bool:
    """這天已經有夠多上櫃日K（官方或 FinMind 先前補過）就不用再打 FinMind。"""
    try:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS n FROM bars_1d b JOIN stocks s ON s.stock_code = b.stock_code
                WHERE s.market = 'OTC' AND substr(b.bar_time, 1, 10) = ?
                """,
                (trade_date.isoformat(),),
            ).fetchone()
        return int(row["n"] if row else 0) >= minimum
    except Exception:  # noqa: BLE001
        return False


def _finmind_otc_day(trade_date: date) -> tuple[list[dict[str, Any]], str]:
    """櫃買來源拿不到時的備援：FinMind TaiwanStockPrice 全市場當日收盤，只挑資料庫已知是上櫃的代號。
    回（列, 說明）；沒有 token 或這天沒資料回空列。"""
    try:
        from otc_gap_backfill import _known_otc_codes, _known_stock_names, _row_to_otc_bar, fetch_finmind_price_day

        with get_connection() as connection:
            known = _known_otc_codes(connection)
            names = _known_stock_names(connection)
        raw = fetch_finmind_price_day(trade_date)
        if not raw:
            return [], "FinMind 沒有 token 或這天沒有資料"
        rows = [bar for entry in raw if (bar := _row_to_otc_bar(entry, trade_date, known, names))]
        return rows, f"FinMind 補上櫃 {len(rows)} 檔" if rows else "FinMind 有資料但沒有對到已知的上櫃代號"
    except Exception as error:  # noqa: BLE001
        return [], f"FinMind 失敗：{type(error).__name__}: {error}"


def download_official_daily_bars(
    days: int = 140,
    delay: float = 0.35,
    *,
    end_date: date | None = None,
    run_triangle_scan: bool = True,
) -> dict[str, Any]:
    """Fetch each market once per date; preserve existing bars and fill only gaps."""
    initialize_database()
    end = end_date or date.today()
    start = end - timedelta(days=max(60, days))
    dates = list(_calendar_days(start, end))
    inserted = 0
    source_failures: list[dict[str, str]] = []
    for index, trade_date in enumerate(dates, start=1):
        try:
            twse_rows = fetch_twse_day(trade_date)
        except Exception as error:  # noqa: BLE001
            source_failures.append(
                {"date": trade_date.isoformat(), "source": "TWSE", "error": str(error)}
            )
            print(f"[{index}/{len(dates)}] {trade_date}: 證交所取得失敗，整日暫不寫入", flush=True)
            time.sleep(max(0.0, delay))
            continue
        if not twse_rows:
            print(f"[{index}/{len(dates)}] {trade_date}: 休市或尚未公布，整日略過", flush=True)
            time.sleep(max(0.0, delay))
            continue

        day_rows = list(twse_rows)
        tpex_rows: list[dict[str, Any]] = []
        tpex_error: str | None = None
        try:
            tpex_rows = fetch_tpex_day(trade_date)
        except Exception as error:  # noqa: BLE001
            tpex_error = str(error)
        if not tpex_rows and not _otc_bars_exist(trade_date):
            # 櫃買中心從 Railway 出去被擋（2026-09-22 起 403／連線重置）：這天上櫃的日K還沒有，
            # 改用 FinMind 補；只寫資料庫已知是上櫃的代號。
            tpex_rows, finmind_note = _finmind_otc_day(trade_date)
            source_failures.append({
                "date": trade_date.isoformat(), "source": "TPEx", "error": tpex_error or "櫃買回空清單",
                "finmind": finmind_note,
            })
        elif tpex_error:
            source_failures.append({"date": trade_date.isoformat(), "source": "TPEx", "error": tpex_error})
        day_rows.extend(tpex_rows)
        day_inserted = _save_day(day_rows)
        inserted += day_inserted
        print(
            f"[{index}/{len(dates)}] {trade_date}: 官方 {len(day_rows)} 筆，新增 {day_inserted} 筆",
            flush=True,
        )
        time.sleep(max(0.0, delay))

    with get_connection() as connection:
        stock_count = int(
            connection.execute("SELECT COUNT(DISTINCT stock_code) FROM bars_1d").fetchone()[0]
        )
        bar_count = int(connection.execute("SELECT COUNT(*) FROM bars_1d").fetchone()[0])

    scan_summary = None
    if run_triangle_scan and inserted:
        from triangle_screener import scan_all_triangles

        scan_summary = scan_all_triangles()["summary"]
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "requested_dates": len(dates),
        "inserted_bars": inserted,
        "stock_count": stock_count,
        "bar_count": bar_count,
        "source_failures": source_failures,
        "triangle_scan": scan_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="HanStock 證交所＋櫃買中心日 K 補下載")
    parser.add_argument("--days", type=int, default=140)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--skip-triangle-scan", action="store_true")
    args = parser.parse_args()
    result = download_official_daily_bars(
        days=args.days,
        delay=args.delay,
        run_triangle_scan=not args.skip_triangle_scan,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
