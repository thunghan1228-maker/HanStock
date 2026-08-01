import os
from datetime import datetime, timezone

import shioaji as sj
from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv("SHIOAJI_API_KEY")
secret_key = os.getenv("SHIOAJI_SECRET_KEY")

if not api_key or not secret_key:
    raise RuntimeError("找不到 API Key 或 Secret Key，請檢查 .env。")

api = sj.Shioaji()

try:
    api.login(
        api_key=api_key,
        secret_key=secret_key,
    )

    print("永豐 API 登入成功！")

    contract = api.Contracts.Stocks["2330"]
    kbars = api.kbars(contract=contract)

    if not kbars.ts:
        raise RuntimeError("沒有取得K線資料。")

    print("＝＝＝＝ 2330 最近10筆一分鐘K線 ＝＝＝＝")

    start_index = max(0, len(kbars.ts) - 10)

    for i in range(start_index, len(kbars.ts)):
        dt = datetime.fromtimestamp(
    kbars.ts[i] / 1_000_000_000,
    tz=timezone.utc,
)

        print(
            f"{dt:%Y-%m-%d %H:%M}｜"
            f"開 {kbars.Open[i]}｜"
            f"高 {kbars.High[i]}｜"
            f"低 {kbars.Low[i]}｜"
            f"收 {kbars.Close[i]}｜"
            f"量 {kbars.Volume[i]}"
        )

except Exception as error:
    print(f"K線讀取失敗：{error}")

finally:
    api.logout()
    print("已安全登出。")