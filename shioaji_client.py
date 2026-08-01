from contextlib import contextmanager

import shioaji as sj

from config import (
    SHIOAJI_API_KEY,
    SHIOAJI_SECRET_KEY,
    validate_settings,
)


@contextmanager
def shioaji_session():
    """建立 Shioaji 連線，使用完畢後自動安全登出。"""
    validate_settings()

    api = sj.Shioaji()
    logged_in = False

    try:
        api.login(
            api_key=SHIOAJI_API_KEY,
            secret_key=SHIOAJI_SECRET_KEY,
        )
        logged_in = True

        print("永豐 Shioaji API 登入成功！")
        yield api

    finally:
        if logged_in:
            api.logout()
            print("已安全登出。")