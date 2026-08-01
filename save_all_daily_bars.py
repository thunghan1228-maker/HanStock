import argparse
import time
from datetime import date, timedelta
from pathlib import Path

from database import (
    DATA_DIR,
    initialize_database,
    save_bars,
    save_stock,
)
from market_data import (
    convert_to_daily_bars,
    get_one_minute_bars,
)
from shioaji_client import shioaji_session
from stock_groups import STOCK_GROUPS


def collect_unique_stocks():
    """彙整所有族群，依股票代號去除重複項目。"""
    unique_stocks: dict[str, str] = {}
    memberships: dict[str, list[str]] = {}
    name_conflicts: dict[str, set[str]] = {}

    total_records = 0

    for group_name, stocks in STOCK_GROUPS.items():
        for stock_code, stock_name in stocks:
            total_records += 1

            memberships.setdefault(stock_code, []).append(group_name)

            if stock_code not in unique_stocks:
                unique_stocks[stock_code] = stock_name
            elif unique_stocks[stock_code] != stock_name:
                name_conflicts.setdefault(
                    stock_code,
                    {unique_stocks[stock_code]},
                ).add(stock_name)

    sorted_stocks = sorted(
        unique_stocks.items(),
        key=lambda item: item[0],
    )

    return (
        sorted_stocks,
        memberships,
        name_conflicts,
        total_records,
    )


def show_audit() -> None:
    """顯示族群與股票數量，不連接永豐 API。"""
    (
        unique_stocks,
        memberships,
        name_conflicts,
        total_records,
    ) = collect_unique_stocks()

    duplicated_codes = [
        stock_code
        for stock_code, groups in memberships.items()
        if len(groups) > 1
    ]

    print("＝＝＝＝ HanStock 全族群資料稽核 ＝＝＝＝")
    print(f"分類數量：{len(STOCK_GROUPS)}")
    print(f"族群成分股紀錄：{total_records}")
    print(f"去除重複後股票數量：{len(unique_stocks)}")
    print(f"同時屬於多個族群：{len(duplicated_codes)}")
    print(f"股票名稱有差異：{len(name_conflicts)}")

    if name_conflicts:
        print()
        print("＝＝＝＝ 股票名稱差異 ＝＝＝＝")

        for stock_code, names in sorted(name_conflicts.items()):
            print(
                f"{stock_code}："
                + "／".join(sorted(names))
            )


def download_all_daily_bars(
    limit: int | None,
    delay_seconds: float,
) -> None:
    """下載去除重複後的全部股票日K資料。"""
    (
        unique_stocks,
        _memberships,
        _name_conflicts,
        _total_records,
    ) = collect_unique_stocks()

    if limit is not None:
        unique_stocks = unique_stocks[:limit]

    end_date = date.today()
    start_date = end_date - timedelta(days=29)

    initialize_database()
    DATA_DIR.mkdir(exist_ok=True)

    failed_stocks: list[tuple[str, str, str]] = []
    success_count = 0

    print("＝＝＝＝ 開始下載全部股票日K ＝＝＝＝")
    print(f"本次股票數量：{len(unique_stocks)}")
    print(f"查詢期間：{start_date} ～ {end_date}")
    print()

    with shioaji_session() as api:
        for index, (stock_code, stock_name) in enumerate(
            unique_stocks,
            start=1,
        ):
            print(
                f"[{index}/{len(unique_stocks)}] "
                f"{stock_code} {stock_name}"
            )

            try:
                one_minute_bars = get_one_minute_bars(
                    api=api,
                    stock_code=stock_code,
                    start=start_date.isoformat(),
                    end=end_date.isoformat(),
                )

                daily_bars = convert_to_daily_bars(
                    one_minute_bars
                )

                save_stock(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    market="TW",
                )

                saved_count = save_bars(
                    table_name="bars_1d",
                    stock_code=stock_code,
                    bars=daily_bars,
                )

                success_count += 1
                print(f"  ✅ 日K {saved_count} 筆")

            except Exception as error:
                failed_stocks.append(
                    (
                        stock_code,
                        stock_name,
                        str(error),
                    )
                )
                print(f"  ❌ {error}")

            time.sleep(delay_seconds)

    failure_path = Path(DATA_DIR) / "daily_download_failures.txt"

    if failed_stocks:
        failure_lines = [
            f"{code}\t{name}\t{error}"
            for code, name, error in failed_stocks
        ]

        failure_path.write_text(
            "\n".join(failure_lines),
            encoding="utf-8",
        )
    elif failure_path.exists():
        failure_path.unlink()

    print()
    print("＝＝＝＝ 全部股票日K下載結果 ＝＝＝＝")
    print(f"成功：{success_count} 檔")
    print(f"失敗：{len(failed_stocks)} 檔")

    if failed_stocks:
        print(f"失敗紀錄：{failure_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HanStock 全族群日K下載工具"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只統計族群和股票數量，不下載資料",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只下載前幾檔，適合先做小量測試",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="每檔之間等待秒數，預設0.5秒",
    )

    arguments = parser.parse_args()

    if arguments.dry_run:
        show_audit()
        return

    download_all_daily_bars(
        limit=arguments.limit,
        delay_seconds=arguments.delay,
    )


if __name__ == "__main__":
    main()