"""HanStock 設定模組。

從環境變數讀取所有必要設定。
注意：密鑰、密碼等敏感資訊只能透過環境變數（Railway Variables）提供，
絕對不可寫入程式碼或印在日誌中。
"""

import os

from dotenv import load_dotenv


# 載入專案資料夾內的 .env（本機開發用）
load_dotenv()

# ─── Shioaji 基本登入 ───
SHIOAJI_API_KEY = os.getenv("SHIOAJI_API_KEY")
SHIOAJI_SECRET_KEY = os.getenv("SHIOAJI_SECRET_KEY")

# ─── Shioaji 憑證（下單用，行情訂閱不一定需要） ───
SHIOAJI_CA_PATH = os.getenv("SHIOAJI_CA_PATH", "")
SHIOAJI_CA_PASSWD = os.getenv("SHIOAJI_CA_PASSWD", "")
SHIOAJI_PERSON_ID = os.getenv("SHIOAJI_PERSON_ID", "")

# ─── Shioaji 行情設定 ───
SHIOAJI_SIMULATION = os.getenv("SHIOAJI_SIMULATION", "false").lower() == "true"
SHIOAJI_FUTURES_CODE = os.getenv("SHIOAJI_FUTURES_CODE", "TXFR1")

# ─── 即時行情開關（設為 false 可停用自動啟動） ───
SHIOAJI_QUOTE_ENABLED = os.getenv("SHIOAJI_QUOTE_ENABLED", "true").lower() == "true"


def validate_settings() -> None:
    """確認必要的環境設定是否存在。"""
    if not SHIOAJI_API_KEY or not SHIOAJI_SECRET_KEY:
        raise RuntimeError(
            "找不到永豐 API Key 或 Secret Key，請檢查環境變數。"
        )
