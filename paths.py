"""HanStock 路徑設定。"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# 優先使用明確設定；若 Railway 已掛 Volume 到 /data 但漏設 HANSTOCK_DATA_DIR，
# 自動偵測既有 /data 掛載點，避免 SQLite 落到 /app/data 的 ephemeral filesystem。
_configured_data_dir = os.getenv("HANSTOCK_DATA_DIR", "").strip()
_railway_volume_dir = Path("/data")
if _configured_data_dir:
    DATA_DIR = Path(_configured_data_dir).expanduser().resolve()
    DATA_DIR_SOURCE = "env"
elif _railway_volume_dir.exists() and _railway_volume_dir.is_dir() and os.access(_railway_volume_dir, os.W_OK):
    DATA_DIR = _railway_volume_dir.resolve()
    DATA_DIR_SOURCE = "railway-volume-auto"
else:
    DATA_DIR = (PROJECT_DIR / "data").resolve()
    DATA_DIR_SOURCE = "project-default"

DATA_DIR.mkdir(parents=True, exist_ok=True)
