from read_rule1_results import show_latest_rule1_results
from scan_rule1_all import scan_all_groups
from scan_rule1_group import scan_group
from stock_groups import STOCK_GROUPS, resolve_group_names


def show_help() -> None:
    """顯示操作說明。"""
    print()
    print("＝＝＝＝ HanStock 操作說明 ＝＝＝＝")
    print("輸入族群名稱，例如：記憶體")
    print("輸入族群內股票代號，例如：2344")
    print("輸入 groups：查看目前所有族群")
    print("輸入 r1all：掃描全部族群 Rule1 並保存結果")
    print("輸入 latest：直接查看最新 Rule1 掃描結果")
    print("輸入 help：查看操作說明")
    print("輸入 q：結束程式")
    print()


def show_groups() -> None:
    """顯示目前已建立的族群。"""
    print()
    print("＝＝＝＝ 目前股票族群 ＝＝＝＝")

    for group_name, stocks in STOCK_GROUPS.items():
        print(f"{group_name}：{len(stocks)} 檔")

    print(f"分類總數：{len(STOCK_GROUPS)}")
    print()


def main() -> None:
    print("＝＝＝＝ HanStock 台灣股票選股系統 ＝＝＝＝")
    show_help()

    while True:
        try:
            keyword = input("請輸入股票代號或族群名稱：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHanStock 已結束。")
            break

        if not keyword:
            continue

        command = keyword.lower()

        if command in {"q", "quit", "exit"}:
            print("HanStock 已結束。")
            break

        if command == "help":
            show_help()
            continue

        if command == "groups":
            show_groups()
            continue

        if command == "r1all":
            scan_all_groups()
            print()
            continue

        if command in {"latest", "result", "results"}:
            try:
                show_latest_rule1_results()
            except RuntimeError as error:
                print(f"操作失敗：{error}")
            print()
            continue

        try:
            group_names = resolve_group_names(keyword)

            if keyword not in group_names:
                print(
                    f"輸入「{keyword}」，"
                    f"自動辨識族群：{'、'.join(group_names)}"
                )

            for group_name in group_names:
                scan_group(group_name)

        except Exception as error:
            print(f"操作失敗：{error}")

        print()


if __name__ == "__main__":
    main()
