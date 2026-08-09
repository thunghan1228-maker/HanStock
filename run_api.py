"""啟動 HanStock FastAPI 服務（含 Shioaji 即時行情）。"""

import os
import logging

import uvicorn

# 股票期貨若同月份同時存在標準與調整型 R1，優先標準契約；
# 模組載入即安裝 policy，之後 hanstock_app 取用同一個 stock_futures_service module。
import stock_futures_standard_policy  # noqa: F401,E402


# 設定根日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hanstock.startup")


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("HANSTOCK_API_PORT", "8000")))
    host = os.getenv("HANSTOCK_API_HOST", "0.0.0.0")

    logger.info("＝＝＝＝ HanStock API 啟動中 ＝＝＝＝")
    logger.info("監聽：http://%s:%d", host, port)
    logger.info("SHIOAJI_QUOTE_ENABLED=%s", os.getenv("SHIOAJI_QUOTE_ENABLED", "true"))
    logger.info("SHIOAJI_FUTURES_CODE=%s", os.getenv("SHIOAJI_FUTURES_CODE", "TXFR1"))
    logger.info("SHIOAJI_SIMULATION=%s", os.getenv("SHIOAJI_SIMULATION", "false"))

    # 確認關鍵環境變數是否存在（不印出值）
    has_api_key = bool(os.getenv("SHIOAJI_API_KEY"))
    has_secret = bool(os.getenv("SHIOAJI_SECRET_KEY"))
    has_ca = bool(os.getenv("SHIOAJI_CA_PATH"))
    logger.info(
        "環境變數檢查: API_KEY=%s, SECRET_KEY=%s, CA_PATH=%s",
        "已設定" if has_api_key else "未設定",
        "已設定" if has_secret else "未設定",
        "已設定" if has_ca else "未設定",
    )

    # hanstock_app 會先掛上 Shioaji 1.7 櫃買指數 Quote runtime，
    # 再載入原 api_server:app；股票/期貨既有流程不變。
    uvicorn.run(
        "hanstock_app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
