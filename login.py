import shioaji as sj

from config import (
    SHIOAJI_API_KEY,
    SHIOAJI_SECRET_KEY,
    validate_settings,
)


# 確認 .env 裡的金鑰存在
validate_settings()

api = sj.Shioaji()
logged_in = False

try:
    accounts = api.login(
        api_key=SHIOAJI_API_KEY,
        secret_key=SHIOAJI_SECRET_KEY,
    )
    logged_in = True

    print("永豐 Shioaji API 登入成功！")
    print(f"讀取到的帳戶數量：{len(accounts)}")

finally:
    if logged_in:
        api.logout()
        print("已安全登出。")