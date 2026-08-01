from shioaji_client import shioaji_session


with shioaji_session() as api:
    contract = api.Contracts.Stocks["2330"]

    if contract is None:
        raise RuntimeError("找不到股票代號 2330。")

    print("共用連線模組測試成功！")
    print(f"測試股票代號：{contract.code}")