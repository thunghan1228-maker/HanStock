import os

from dotenv import load_dotenv


# 載入專案資料夾內的 .env
load_dotenv()

SHIOAJI_API_KEY = os.getenv("SHIOAJI_API_KEY")
SHIOAJI_SECRET_KEY = os.getenv("SHIOAJI_SECRET_KEY")


def validate_settings() -> None:
    """確認必要的環境設定是否存在。"""
    if not SHIOAJI_API_KEY or not SHIOAJI_SECRET_KEY:
        raise RuntimeError(
            "找不到永豐 API Key 或 Secret Key，請檢查 .env 檔案。"
        )
    