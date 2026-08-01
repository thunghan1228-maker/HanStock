import sys

from stock_groups import (
    get_group,
    resolve_group_names,
)


def main() -> None:
    if len(sys.argv) < 2:
        print("請輸入族群名稱或股票代號。")
        print("例如：py identify_group.py 2344")
        print("例如：py identify_group.py 記憶體")
        raise SystemExit(1)

    keyword = sys.argv[1].strip()
    group_names = resolve_group_names(keyword)

    for group_name in group_names:
        stocks = get_group(group_name)

        print(f"＝＝＝＝ {group_name}族群 ＝＝＝＝")
        print(f"共 {len(stocks)} 檔")
        print()

        for stock_code, stock_name in stocks:
            print(f"{stock_code} {stock_name}")


if __name__ == "__main__":
    main()