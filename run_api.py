"""啟動 HanStock FastAPI 服務。"""

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("HANSTOCK_API_PORT", "8000")))
    host = os.getenv("HANSTOCK_API_HOST", "0.0.0.0")

    print("＝＝＝＝ HanStock API 啟動中 ＝＝＝＝")
    print(f"監聽：http://{host}:{port}")
    print("按 Ctrl+C 可停止服務。")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
    )
