import sys
from datetime import date, timedelta

from database import (
    initialize_database,
    save_bars,
    save_stock,
)
from market_data import (
    convert_to_daily_bars,
    get_one_minute_bars,
)
from shioaji_client import shioaji_session
from stock_groups import get_group


def main() -> None:
    if len(sys.argv) < 2:
        print("請輸入股票族群名稱。")
        print("使用方式：py save_group_daily_bars.py 記憶體")
        raise SystemExit(1)

    group_name = sys.argv[1].strip()
    stocks = get_group(group_name)

    end_date = date.today()
    start_date = end_date - timedelta(days=29)

    initialize_database()

    success_count = 0
    failed_stocks: list[tuple[str, str, str]] = []

    print(f"＝＝＝＝ 開始下載「{group_name}」族群日K ＝＝＝＝")
    print(f"股票數量：{len(stocks)} 檔")
    print(f"查詢期間：{start_date} ～ {end_date}")
    print()

    # 整個族群共用同一次登入
    with shioaji_session() as api:
        for index, (stock_code, stock_name) in enumerate(
            stocks,
            start=1,
        ):
            print(
                f"[{index}/{len(stocks)}] "
                f"處理 {stock_code} {stock_name}..."
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
                    market="TSE",
                )

                saved_count = save_bars(
                    table_name="bars_1d",
                    stock_code=stock_code,
                    bars=daily_bars,
                )

                success_count += 1

                print(
                    f"  ✅ 完成：日K {saved_count} 筆"
                )

            except Exception as error:
                failed_stocks.append(
                    (
                        stock_code,
                        stock_name,
                        str(error),
                    )
                )

                print(f"  ❌ 失敗：{error}")

    print()
    print("＝＝＝＝ 族群日K下載結果 ＝＝＝＝")
    print(f"成功：{success_count} 檔")
    print(f"失敗：{len(failed_stocks)} 檔")

    if failed_stocks:
        print()
        print("失敗清單：")

        for stock_code, stock_name, error in failed_stocks:
            print(
                f"- {stock_code} {stock_name}：{error}"
            )


if __name__ == "__main__":
    main()