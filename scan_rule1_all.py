from rule1 import evaluate_rule1, load_daily_bars
from stock_groups import STOCK_GROUPS


def scan_all_groups() -> None:
    """掃描全部族群的 Rule1 結果。"""
    total_groups = len(STOCK_GROUPS)
    total_passed_records = 0
    groups_with_results = 0
    unavailable_results = []

    print()
    print("＝＝＝＝ HanStock 全族群 Rule1 掃描 ＝＝＝＝")
    print(f"族群數量：{total_groups}")
    print()

    for group_index, (group_name, stocks) in enumerate(
        STOCK_GROUPS.items(),
        start=1,
    ):
        passed_results = []

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
                    {
                        "group_name": group_name,
                        "stock_code": stock_code,
                        "stock_name": stock_name,
                        "error": str(error),
                    }
                )

        passed_results.sort(
            key=lambda item: (
                item["change_rate"],
                item["price_change"],
            ),
            reverse=True,
        )

        print(
            f"＝＝＝＝ [{group_index}/{total_groups}] "
            f"{group_name} ＝＝＝＝"
        )

        if passed_results:
            groups_with_results += 1
            total_passed_records += len(passed_results)

            for result in passed_results:
                change_arrow = (
                    "🔺"
                    if result["price_change"] > 0
                    else "🔽"
                    if result["price_change"] < 0
                    else "➖"
                )

                print(
                    f"{result['stock_code']} "
                    f"{result['stock_name']}｜"
                    f"漲跌幅 {result['change_rate']:+.2f}%｜"
                    f"價差 {result['price_change']:+.2f}｜"
                    f"{change_arrow}"
                )
        else:
            print("無符合條件")

        print()

    print("＝＝＝＝ 全族群 Rule1 掃描完成 ＝＝＝＝")
    print(f"族群總數：{total_groups}")
    print(f"有符合股票的族群：{groups_with_results}")
    print(f"符合條件紀錄：{total_passed_records}")
    print(f"無法判斷紀錄：{len(unavailable_results)}")

    if unavailable_results:
        print()
        print("＝＝＝＝ 無法判斷清單 ＝＝＝＝")

        for item in unavailable_results:
            print(
                f"{item['group_name']}｜"
                f"{item['stock_code']} "
                f"{item['stock_name']}｜"
                f"{item['error']}"
            )


if __name__ == "__main__":
    scan_all_groups()