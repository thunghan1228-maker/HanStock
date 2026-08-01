from market_data import get_snapshot
from shioaji_client import shioaji_session


with shioaji_session() as api:
    snapshot = get_snapshot(api, "2330")

    print("＝＝＝＝ 2330 台積電行情 ＝＝＝＝")
    print(f"股票代號：{snapshot.code}")
    print(f"開盤價：{snapshot.open}")
    print(f"最高價：{snapshot.high}")
    print(f"最低價：{snapshot.low}")
    print(f"成交價：{snapshot.close}")
    print(f"漲跌價：{snapshot.change_price}")
    print(f"漲跌幅：{snapshot.change_rate}%")
    print(f"總成交量：{snapshot.total_volume}")
    print(f"最佳買價：{snapshot.buy_price}")
    print(f"最佳賣價：{snapshot.sell_price}")