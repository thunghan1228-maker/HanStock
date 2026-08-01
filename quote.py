import os

import shioaji as sj
from dotenv import load_dotenv


# 讀取 .env 裡的永豐 API 金鑰
load_dotenv()

api_key = os.getenv("SHIOAJI_API_KEY")
secret_key = os.getenv("SHIOAJI_SECRET_KEY")

if not api_key or not secret_key:
    raise RuntimeError("找不到 API Key 或 Secret Key，請檢查 .env。")

api = sj.Shioaji()

try:
    # 登入永豐 Shioaji
    api.login(
        api_key=api_key,
        secret_key=secret_key,
    )

    print("永豐 API 登入成功！")

    # 取得台積電 2330 的商品合約
    contract = api.Contracts.Stocks["2330"]

    if contract is None:
        raise RuntimeError("找不到股票代號 2330。")

    # 查詢一次市場快照
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
    api.logout()
    print("已安全登出。")