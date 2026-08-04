"""HanStock 環境設定。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

SHIOAJI_API_KEY = os.getenv("SHIOAJI_API_KEY")
SHIOAJI_SECRET_KEY = os.getenv("SHIOAJI_SECRET_KEY")
SHIOAJI_QUOTE_ENABLED = os.getenv("SHIOAJI_QUOTE_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def validate_settings() -> None:
    """確認必要的環境設定是否存在。"""
    if not SHIOAJI_API_KEY or not SHIOAJI_SECRET_KEY:
        raise RuntimeError("找不到永豐 API Key 或 Secret Key，請檢查 .env 檔案。")
