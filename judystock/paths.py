"""Judy Stock 路徑設定（跟 HanStock 完全分開的資料目錄）。"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

_configured_data_dir = os.getenv("JUDYSTOCK_DATA_DIR", "").strip()
if _configured_data_dir:
    DATA_DIR = Path(_configured_data_dir).expanduser().resolve()
    DATA_DIR_SOURCE = "env"
else:
    DATA_DIR = (PROJECT_DIR / "data").resolve()
    DATA_DIR_SOURCE = "project-default"

DATA_DIR.mkdir(parents=True, exist_ok=True)
