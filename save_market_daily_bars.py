"""Download daily bars for all TWSE/TPEx stocks and ETFs.

Official exchange after-hours data is the default. Shioaji remains available as an
explicit fallback for diagnostics, but is no longer required for historical daily bars.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from database import initialize_database, save_bars, save_stock
from market_data import convert_to_daily_bars, get_one_minute_bars
from official_daily_bars import download_official_daily_bars
from shioaji_client import shioaji_session


def eligible_contracts(api):
    contracts = list(api.Contracts.Stocks.TSE) + list(api.Contracts.Stocks.OTC)
    selected = {}
    for contract in contracts:
        code = str(getattr(contract, "code", "") or "").strip().upper()
        is_common_stock = len(code) == 4 and code.isdigit()
        is_etf = code.startswith("00") and 5 <= len(code) <= 6
        if not (is_common_stock or is_etf):
            continue
        selected.setdefault(code, contract)
    return [selected[code] for code in sorted(selected)]


def download_shioaji_daily_bars(
    days: int = 140, delay: float = 0.15, limit: int | None = None
):
    """Legacy per-symbol fallback. Do not use this for normal full-market backfills."""
    initialize_database()
    end_date = date.today()
    start_date = end_date - timedelta(days=max(60, days))
    success = 0
    failures = []
    with shioaji_session() as api:
        contracts = eligible_contracts(api)
        if limit is not None:
            contracts = contracts[:limit]
        print(f"Shioaji 母體：{len(contracts)} 檔；期間：{start_date}～{end_date}", flush=True)
        for index, contract in enumerate(contracts, start=1):
            code = str(contract.code)
            name = str(contract.name)
            try:
                bars_1m = get_one_minute_bars(
                    api=api,
                    stock_code=code,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                )
                bars_1d = convert_to_daily_bars(bars_1m)
                save_stock(code, name, market=str(getattr(contract, "exchange", "TW")))
                save_bars("bars_1d", code, bars_1d)
                success += 1
                print(f"[{index}/{len(contracts)}] {code} {name}: {len(bars_1d)} 日", flush=True)
            except Exception as error:  # noqa: BLE001
                failures.append({"stock_code": code, "stock_name": name, "error": str(error)})
                print(f"[{index}/{len(contracts)}] {code} {name}: 失敗 {error}", flush=True)
            time.sleep(max(0.0, delay))
    return {"requested": success + len(failures), "success": success, "failures": failures}


def download_market_daily_bars(
    days: int = 140,
    delay: float = 0.35,
    limit: int | None = None,
    *,
    source: str = "official",
    run_triangle_scan: bool = True,
):
    """Compatibility entrypoint; official TWSE/TPEx data is now the default."""
    if source == "shioaji":
        return download_shioaji_daily_bars(days=days, delay=delay, limit=limit)
    if source != "official":
        raise ValueError(f"不支援的日 K 來源：{source}")
    if limit is not None:
        raise ValueError("官方來源按交易日整批下載，不支援 --limit；測試請用較短 --days。")
    return download_official_daily_bars(
        days=days,
        delay=delay,
        run_triangle_scan=run_triangle_scan,
    )


def main():
    parser = argparse.ArgumentParser(description="HanStock 全市場日 K 下載")
    parser.add_argument("--days", type=int, default=140)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--source", choices=("official", "shioaji"), default="official")
    parser.add_argument("--skip-triangle-scan", action="store_true")
    args = parser.parse_args()
    result = download_market_daily_bars(
        days=args.days,
        delay=args.delay,
        limit=args.limit,
        source=args.source,
        run_triangle_scan=not args.skip_triangle_scan,
    )
    print(result, flush=True)


if __name__ == "__main__":
    main()
