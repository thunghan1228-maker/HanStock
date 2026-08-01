import sys

from rule1 import evaluate_rule1, load_daily_bars
from stock_groups import (
    get_group,
    resolve_group_names,
)


def scan_group(group_name: str) -> None:
    """掃描單一族群的 Rule1 結果。"""
    stocks = get_group(group_name)

    results = []
    failed_results = []

    print()
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
            result["passed_count"] = sum(
                result["conditions"].values()
            )

            results.append(result)

            if result["passed"]:
                print("  ✅ 符合 Rule1")
            else:
                print(
                    f"  ❌ 不符合 "
                    f"（符合 {result['passed_count']}/4 項）"
                )

        except Exception as error:
            failed_results.append(
                (stock_code, stock_name, str(error))
            )
            print(f"  ⚠️ 無法判斷：{error}")

    results.sort(
        key=lambda item: (
            item["passed_count"],
            item["change_rate"],
        ),
        reverse=True,
    )

    passed_results = [
        result
        for result in results
        if result["passed"]
    ]

    not_passed_results = [
        result
        for result in results
        if not result["passed"]
    ]

    print()
    print(f"＝＝＝＝ 「{group_name}」Rule1 掃描結果 ＝＝＝＝")
    print(f"符合：{len(passed_results)} 檔")
    print(f"不符合：{len(not_passed_results)} 檔")
    print(f"無法判斷：{len(failed_results)} 檔")

    print()
    print("＝＝＝＝ 符合 Rule1 ＝＝＝＝")

    if passed_results:
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

    print()
    print("＝＝＝＝ 最接近符合的股票 ＝＝＝＝")

    for result in not_passed_results:
        failed_conditions = [
            condition_name
            for condition_name, passed
            in result["conditions"].items()
            if not passed
        ]

        print(
            f"{result['stock_code']} "
            f"{result['stock_name']}｜"
            f"符合 {result['passed_count']}/4｜"
            f"漲跌幅 {result['change_rate']:+.2f}%｜"
            f"價差 {result['price_change']:+.2f}"
        )

        print(
            "  尚未符合："
            + "、".join(failed_conditions)
        )

    if failed_results:
        print()
        print("＝＝＝＝ 無法判斷 ＝＝＝＝")

        for stock_code, stock_name, error in failed_results:
            print(f"{stock_code} {stock_name}：{error}")


def main() -> None:
    if len(sys.argv) < 2:
        print("請輸入族群名稱或族群內股票代號。")
        print("例如：py scan_rule1_group.py 記憶體")
        print("例如：py scan_rule1_group.py 2344")
        raise SystemExit(1)

    keyword = sys.argv[1].strip()
    group_names = resolve_group_names(keyword)

    if keyword not in group_names:
        print(
            f"輸入「{keyword}」，"
            f"自動辨識族群：{'、'.join(group_names)}"
        )

    for group_name in group_names:
        scan_group(group_name)


if __name__ == "__main__":
    main()