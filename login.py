import os

import shioaji as sj
from dotenv import load_dotenv


# 讀取同一個資料夾裡的 .env
load_dotenv()

api_key = os.getenv("SHIOAJI_API_KEY")
secret_key = os.getenv("SHIOAJI_SECRET_KEY")

# 確認金鑰存在，但不會把金鑰印在畫面上
if not api_key or not secret_key:
    raise RuntimeError("找不到 API Key 或 Secret Key，請檢查 .env 檔案。")

api = sj.Shioaji()

try:
    accounts = api.login(
        api_key=api_key,
        secret_key=secret_key,
    )

    print("永豐 Shioaji API 登入成功！")
    print(f"讀取到的帳戶數量：{len(accounts)}")

finally:
    api.logout()
    print("已安全登出。")