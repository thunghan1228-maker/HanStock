from database import (
    get_connection,
    initialize_database,
    save_bars,
    save_stock,
)
from market_data import (
    convert_to_five_minute_bars,
    get_one_minute_bars,
)
from shioaji_client import shioaji_session


STOCK_CODE = "2330"
STOCK_NAME = "台積電"


initialize_database()

with shioaji_session() as api:
    one_minute_bars = get_one_minute_bars(api, STOCK_CODE)
    five_minute_bars = convert_to_five_minute_bars(
        one_minute_bars
    )

save_stock(
    stock_code=STOCK_CODE,
    stock_name=STOCK_NAME,
    market="TSE",
)

saved_1m = save_bars(
    table_name="bars_1m",
    stock_code=STOCK_CODE,
    bars=one_minute_bars,
)

saved_5m = save_bars(
    table_name="bars_5m",
    stock_code=STOCK_CODE,
    bars=five_minute_bars,
)

with get_connection() as connection:
    total_1m = connection.execute(
        """
        SELECT COUNT(*)
        FROM bars_1m
        WHERE stock_code = ?
        """,
        (STOCK_CODE,),
    ).fetchone()[0]

    total_5m = connection.execute(
        """
        SELECT COUNT(*)
        FROM bars_5m
        WHERE stock_code = ?
        """,
        (STOCK_CODE,),
    ).fetchone()[0]

print("＝＝＝＝ K線資料儲存完成 ＝＝＝＝")
print(f"本次處理一分鐘K線：{saved_1m} 筆")
print(f"本次處理五分鐘K線：{saved_5m} 筆")
print(f"資料庫一分鐘K線總數：{total_1m} 筆")
print(f"資料庫五分鐘K線總數：{total_5m} 筆")