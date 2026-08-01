import json
from pathlib import Path
from typing import Any


from paths import DATA_DIR

RESULT_PATH = DATA_DIR / "rule1_all_latest.json"
FALLBACK_RESULT_PATH = Path(__file__).resolve().parent / "seed_data" / "rule1_all_latest.json"


def load_rule1_results(
    result_path: Path = RESULT_PATH,
) -> dict[str, Any]:
    """讀取最新的 Rule1 全族群掃描結果。"""
    if not result_path.exists():
        if result_path == RESULT_PATH and FALLBACK_RESULT_PATH.exists():
            result_path = FALLBACK_RESULT_PATH
        else:
            raise RuntimeError(
                "找不到 Rule1 結果檔，請先在 HanStock 輸入 r1all。"
            )

    try:
        content = result_path.read_text(encoding="utf-8")
        results = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Rule1 結果檔無法讀取：{error}"
        ) from error

    required_keys = {"generated_at", "summary", "groups"}

    if not required_keys.issubset(results):
        raise RuntimeError("Rule1 結果檔格式不完整，請重新執行 r1all。")

    return results


def print_rule1_results(results: dict[str, Any]) -> None:
    """把 Rule1 JSON 結果以終端機格式顯示。"""
    summary = results["summary"]

    print()
    print("＝＝＝＝ Rule1 最新掃描結果 ＝＝＝＝")
    print(f"產生時間：{results['generated_at']}")
    print(f"族群總數：{summary['total_groups']}")
    print(f"有符合股票的族群：{summary['groups_with_results']}")
    print(f"符合條件紀錄：{summary['total_passed_records']}")
    print(f"無法判斷紀錄：{summary['unavailable_records']}")

    print()
    print("＝＝＝＝ 符合 Rule1 的股票 ＝＝＝＝")

    found_any = False

    for group in results["groups"]:
        passed_stocks = group.get("passed_stocks", [])

        if not passed_stocks:
            continue

        found_any = True
        print()
        print(f"[{group['group_name']}]")

        for stock in passed_stocks:
            price_change = float(stock["price_change"])

            if price_change > 0:
                arrow = "🔺"
            elif price_change < 0:
                arrow = "🔽"
            else:
                arrow = "➖"

            print(
                f"{stock['stock_code']} "
                f"{stock['stock_name']}｜"
                f"漲跌幅 {float(stock['change_rate']):+.2f}%｜"
                f"價差 {price_change:+.2f}｜"
                f"{arrow}"
            )

    if not found_any:
        print("目前沒有符合 Rule1 的股票。")


def show_latest_rule1_results() -> None:
    """讀取並顯示最新 Rule1 掃描結果。"""
    print_rule1_results(load_rule1_results())


def main() -> None:
    try:
        show_latest_rule1_results()
    except RuntimeError as error:
        print(f"讀取失敗：{error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
