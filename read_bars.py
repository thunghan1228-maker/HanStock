from datetime import datetime

from database import get_connection


STOCK_CODE = "2330"


with get_connection() as connection:
    stock = connection.execute(
        """
        SELECT stock_code, stock_name, market, updated_at
        FROM stocks
        WHERE stock_code = ?
        """,
        (STOCK_CODE,),
    ).fetchone()

    bars_5m = connection.execute(
        """
        SELECT bar_time, open, high, low, close, volume
        FROM bars_5m
        WHERE stock_code = ?
        ORDER BY bar_time DESC
        LIMIT 10
        """,
        (STOCK_CODE,),
    ).fetchall()


if stock is None:
    raise RuntimeError(f"資料庫找不到股票 {STOCK_CODE}。")

print("＝＝＝＝ 股票基本資料 ＝＝＝＝")
print(f"股票代號：{stock['stock_code']}")
print(f"股票名稱：{stock['stock_name']}")
print(f"市場：{stock['market']}")
print(f"更新時間：{stock['updated_at']}")

print()
print("＝＝＝＝ 資料庫最近10筆五分鐘K線 ＝＝＝＝")

for row in reversed(bars_5m):
    bar_time = datetime.fromisoformat(row["bar_time"])

    print(
        f"{bar_time:%Y-%m-%d %H:%M}｜"
        f"開 {row['open']}｜"
        f"高 {row['high']}｜"
        f"低 {row['low']}｜"
        f"收 {row['close']}｜"
        f"量 {row['volume']}"
    )