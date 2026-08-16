"""下載上市、上櫃普通股票及 ETF 的日 K，排除權證與 ETN。"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from database import initialize_database, save_bars, save_stock
from market_data import convert_to_daily_bars, get_one_minute_bars
from shioaji_client import shioaji_session


def eligible_contracts(api):
    contracts = list(api.Contracts.Stocks.TSE) + list(api.Contracts.Stocks.OTC)
    selected = {}
    for contract in contracts:
        code = str(getattr(contract, "code", "") or "").strip().upper()
        name = str(getattr(contract, "name", "") or "").strip()
        is_common_stock = len(code) == 4 and code.isdigit()
        is_etf = code.startswith("00") and 5 <= len(code) <= 6
        if not (is_common_stock or is_etf):
            continue
        selected.setdefault(code, contract)
    return [selected[code] for code in sorted(selected)]


def download_market_daily_bars(days: int = 140, delay: float = 0.15, limit: int | None = None):
    initialize_database()
    end_date = date.today()
    start_date = end_date - timedelta(days=max(60, days))
    success = 0
    failures = []
    with shioaji_session() as api:
        contracts = eligible_contracts(api)
        if limit is not None:
            contracts = contracts[:limit]
        print(f"全市場母體：{len(contracts)} 檔；期間：{start_date}～{end_date}", flush=True)
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
    print(f"完成：成功 {success}，失敗 {len(failures)}", flush=True)
    return {"requested": success + len(failures), "success": success, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description="HanStock 全市場日 K 下載")
    parser.add_argument("--days", type=int, default=140)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    download_market_daily_bars(days=args.days, delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    main()
