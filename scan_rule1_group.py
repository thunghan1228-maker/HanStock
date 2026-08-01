import sys

from rule1 import evaluate_rule1, load_daily_bars
from stock_groups import get_group


def main() -> None:
    if len(sys.argv) < 2:
        print("請輸入股票族群名稱。")
        print("使用方式：py scan_rule1_group.py 記憶體")
        raise SystemExit(1)

    group_name = sys.argv[1].strip()
    stocks = get_group(group_name)

    passed_results = []
    not_passed_results = []
    failed_results = []

    print(f"＝＝＝＝ 掃描「{group_name}」族群 Rule1 ＝＝＝＝")
    print(f"股票數量：{len(stocks)} 檔")
    print()

    for index, (stock_code, stock_name) in enumerate(
        stocks,
        start=1,
    ):
        print(
            f"[{index}/{len(stocks)}] "
            f"判斷 {stock_code} {stock_name}..."
        )

        try:
            daily_bars = load_daily_bars(stock_code)

            if not daily_bars:
                raise RuntimeError("資料庫沒有日K資料。")

            result = evaluate_rule1(
                stock_code=stock_code,
                daily_bars=daily_bars,
            )

            result["stock_name"] = stock_name

            if result["passed"]:
                passed_results.append(result)
                print("  ✅ 符合 Rule1")
            else:
                not_passed_results.append(result)
                passed_count = sum(
                    result["conditions"].values()
                )
                print(
                    f"  ❌ 不符合 "
                    f"（符合 {passed_count}/4 項）"
                )

        except Exception as error:
            failed_results.append(
                (stock_code, stock_name, str(error))
            )
            print(f"  ⚠️ 無法判斷：{error}")

    passed_results.sort(
        key=lambda item: item["change_rate"],
        reverse=True,
    )

    print()
    print(
        f"＝＝＝＝ 「{group_name}」Rule1 掃描結果 ＝＝＝＝"
    )
    print(f"符合：{len(passed_results)} 檔")
    print(f"不符合：{len(not_passed_results)} 檔")
    print(f"無法判斷：{len(failed_results)} 檔")
    print()

    if passed_results:
        print("符合 Rule1 的股票：")

        for result in passed_results:
            print(
                f"{result['stock_code']} "
                f"{result['stock_name']}｜"
                f"漲跌幅 {result['change_rate']:+.2f}%｜"
                f"價差 {result['price_change']:+.2f}｜"
                f"收盤 {result['today_close']:.2f}"
            )
    else:
        print(f"「{group_name}」目前沒有符合 Rule1 的股票。")

    if failed_results:
        print()
        print("無法判斷的股票：")

        for stock_code, stock_name, error in failed_results:
            print(f"{stock_code} {stock_name}：{error}")


if __name__ == "__main__":
    main()