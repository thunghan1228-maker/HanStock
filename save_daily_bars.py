import sys
from datetime import date, timedelta

from database import (
    get_connection,
    initialize_database,
    save_bars,
    save_stock,
)
from market_data import (
    convert_to_daily_bars,
    get_one_minute_bars,
)
from shioaji_client import shioaji_session


def main() -> None:
    if len(sys.argv) < 3:
        print("請輸入股票代號與股票名稱。")
        print("使用方式：py save_daily_bars.py 2344 華邦電")
        raise SystemExit(1)

    stock_code = sys.argv[1].strip()
    stock_name = sys.argv[2].strip()

    end_date = date.today()
    start_date = end_date - timedelta(days=29)

    initialize_database()

    print(f"股票：{stock_code} {stock_name}")
    print(f"查詢期間：{start_date} ～ {end_date}")

    with shioaji_session() as api:
        one_minute_bars = get_one_minute_bars(
            api=api,
            stock_code=stock_code,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )

    daily_bars = convert_to_daily_bars(one_minute_bars)

    save_stock(
        stock_code=stock_code,
        stock_name=stock_name,
        market="TSE",
    )

    saved_daily = save_bars(
        table_name="bars_1d",
        stock_code=stock_code,
        bars=daily_bars,
    )

    with get_connection() as connection:
        total_daily = connection.execute(
            """
            SELECT COUNT(*)
            FROM bars_1d
            WHERE stock_code = ?
            """,
            (stock_code,),
        ).fetchone()[0]

    print("＝＝＝＝ 日K資料儲存完成 ＝＝＝＝")
    print(f"取得一分鐘K線：{len(one_minute_bars)} 筆")
    print(f"本次處理日K線：{saved_daily} 筆")
    print(f"資料庫日K線總數：{total_daily} 筆")

    print()
    print("＝＝＝＝ 最近10筆日K線 ＝＝＝＝")

    for bar in daily_bars[-10:]:
        print(
            f"{bar['time']:%Y-%m-%d}｜"
            f"開 {bar['open']}｜"
            f"高 {bar['high']}｜"
            f"低 {bar['low']}｜"
            f"收 {bar['close']}｜"
            f"量 {bar['volume']}"
        )


if __name__ == "__main__":
    main()