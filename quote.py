import shioaji as sj

from config import (
    SHIOAJI_API_KEY,
    SHIOAJI_SECRET_KEY,
    validate_settings,
)


validate_settings()

api = sj.Shioaji()
logged_in = False

try:
    api.login(
        api_key=SHIOAJI_API_KEY,
        secret_key=SHIOAJI_SECRET_KEY,
    )
    logged_in = True

    print("永豐 API 登入成功！")

    contract = api.Contracts.Stocks["2330"]

    if contract is None:
        raise RuntimeError("找不到股票代號 2330。")

    snapshots = api.snapshots([contract])

    if not snapshots:
        raise RuntimeError("沒有取得 2330 的行情資料。")

    snapshot = snapshots[0]

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

except Exception as error:
    print(f"行情讀取失敗：{error}")

finally:
    if logged_in:
        api.logout()
        print("已安全登出。")