import json
from datetime import datetime
from pathlib import Path

from rule1 import evaluate_rule1, load_daily_bars
from stock_groups import STOCK_GROUPS


DATA_DIR = Path(__file__).parent / "data"
RESULT_PATH = DATA_DIR / "rule1_all_latest.json"


def scan_all_groups() -> dict:
    """掃描全部族群的 Rule1，顯示並保存結果。"""
    total_groups = len(STOCK_GROUPS)
    total_passed_records = 0
    groups_with_results = 0
    unavailable_results = []
    group_results = []

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
                    passed_results.append(
                        {
                            "stock_code": stock_code,
                            "stock_name": stock_name,
                            "today_close": result["today_close"],
                            "yesterday_close": result["yesterday_close"],
                            "price_change": result["price_change"],
                            "change_rate": result["change_rate"],
                            "ma5_today": result["ma5_today"],
                            "ma5_yesterday": result["ma5_yesterday"],
                        }
                    )

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

        group_results.append(
            {
                "group_name": group_name,
                "stock_count": len(stocks),
                "passed_count": len(passed_results),
                "passed_stocks": passed_results,
            }
        )

        print(
            f"＝＝＝＝ [{group_index}/{total_groups}] "
            f"{group_name} ＝＝＝＝"
        )

        if passed_results:
            groups_with_results += 1
            total_passed_records += len(passed_results)

            for result in passed_results:
                if result["price_change"] > 0:
                    change_arrow = "🔺"
                elif result["price_change"] < 0:
                    change_arrow = "🔽"
                else:
                    change_arrow = "➖"

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

    generated_at = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    output = {
        "strategy": "Rule1",
        "generated_at": generated_at,
        "summary": {
            "total_groups": total_groups,
            "groups_with_results": groups_with_results,
            "total_passed_records": total_passed_records,
            "unavailable_records": len(unavailable_results),
        },
        "groups": group_results,
        "unavailable_results": unavailable_results,
    }

    DATA_DIR.mkdir(exist_ok=True)

    RESULT_PATH.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("＝＝＝＝ 全族群 Rule1 掃描完成 ＝＝＝＝")
    print(f"族群總數：{total_groups}")
    print(f"有符合股票的族群：{groups_with_results}")
    print(f"符合條件紀錄：{total_passed_records}")
    print(f"無法判斷紀錄：{len(unavailable_results)}")
    print(f"結果已保存：{RESULT_PATH}")

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

    return output


if __name__ == "__main__":
    scan_all_groups()