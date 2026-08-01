from scan_rule1_group import scan_group
from stock_groups import STOCK_GROUPS, resolve_group_names


def show_help() -> None:
    """顯示操作說明。"""
    print()
    print("＝＝＝＝ HanStock 操作說明 ＝＝＝＝")
    print("輸入族群名稱，例如：記憶體")
    print("輸入族群內股票代號，例如：2344")
    print("輸入 groups：查看目前所有族群")
    print("輸入 help：查看操作說明")
    print("輸入 q：結束程式")
    print()


def show_groups() -> None:
    """顯示目前已建立的族群。"""
    print()
    print("＝＝＝＝ 目前股票族群 ＝＝＝＝")

    for group_name, stocks in STOCK_GROUPS.items():
        print(f"{group_name}：{len(stocks)} 檔")

    print()


def main() -> None:
    print("＝＝＝＝ HanStock 台灣股票選股系統 ＝＝＝＝")
    show_help()

    while True:
        keyword = input("請輸入股票代號或族群名稱：").strip()

        if not keyword:
            continue

        if keyword.lower() in {"q", "quit", "exit"}:
            print("HanStock 已結束。")
            break

        if keyword.lower() == "help":
            show_help()
            continue

        if keyword.lower() == "groups":
            show_groups()
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