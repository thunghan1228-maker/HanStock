from rule1 import evaluate_rule1, load_daily_bars
from stock_groups import STOCK_GROUPS


def scan_all_groups() -> None:
    total_groups = len(STOCK_GROUPS)
    total_passed = 0
    failed_results = []

    print("＝＝＝＝ HanStock 全族群 Rule1 掃描 ＝＝＝＝")
    print(f"族群數量：{total_groups}")
    print()

    for group_index, (group_name, stocks) in enumerate(
        STOCK_GROUPS.items(),
        start=1,
    ):
        passed_results = []
        unavailable_results = []

        print(
            f"[{group_index}/{total_groups}] "
            f"掃描「{group_name}」族群..."
        )

        for stock_code, stock_name in stocks:
            try:
                daily_bars = load_daily_bars(stock_code)

                if not daily_bars:
                    raise RuntimeError("資料庫沒有日K資料。")

                result = evaluate_rule1(
                    stock_code=stock_code,
                    daily_bars=daily_bars,
                )

                if result["passed"]:
                    result["stock_name"] = stock_name
                    passed_results.append(result)

            except Exception as error:
                unavailable_results.append(
                    (stock_code, stock_name, str(error))
                )

        passed_results.sort(
            key=lambda item: item["change_rate"],
            reverse=True,
        )

        print(f"  符合：{len(passed_results)} 檔")

        if passed_results:
            total_passed += len(passed_results)

            for result in passed_results:
                print(
                    f"  {result['stock_code']} "
                    f"{result['stock_name']}｜"
                    f"漲跌幅 {result['change_rate']:+.2f}%｜"
                    f"價差 {result['price_change']:+.2f}"
                )
        else:
            print("  無符合條件股票")

        if unavailable_results:
            failed_results.extend(
                (
                    group_name,
                    stock_code,
                    stock_name,
                    error,
                )
                for stock_code, stock_name, error
                in unavailable_results
            )

        print()

    print("＝＝＝＝ 全族群 Rule1 掃描完成 ＝＝＝＝")
    print(f"族群數量：{total_groups}")
    print(f"符合條件紀錄：{total_passed} 筆")
    print(f"無法判斷紀錄：{len(failed_results)} 筆")

    if failed_results:
        print()
        print("＝＝＝＝ 無法判斷清單 ＝＝＝＝")

        for (
            group_name,
            stock_code,
            stock_name,
            error,
        ) in failed_results:
            print(
                f"{group_name}｜"
                f"{stock_code} {stock_name}｜"
                f"{error}"
            )


if __name__ == "__main__":
    scan_all_groups()