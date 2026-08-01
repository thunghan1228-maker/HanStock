"""HanStock 路徑設定。"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("HANSTOCK_DATA_DIR", str(PROJECT_DIR / "data"))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
