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
    volume = max(0, int(volume_value or 0))
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
    return parse_market_payload(payload, trade_date, "TSE")


def fetch_tpex_day(
    trade_date: date, *, fetcher: Callable[..., dict[str, Any]] = fetch_json
) -> list[dict[str, Any]]:
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
        try:
            day_rows.extend(fetch_tpex_day(trade_date))
        except Exception as error:  # noqa: BLE001
            source_failures.append(
                {"date": trade_date.isoformat(), "source": "TPEx", "error": str(error)}
            )
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
