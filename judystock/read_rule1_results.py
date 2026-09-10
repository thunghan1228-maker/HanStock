"""Judy Stock 的 Rule1 結果讀取。

從 HanStock 精簡而來，只保留讀檔邏輯；資料目錄完全獨立於 HanStock，
兩邊互不影響。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paths import DATA_DIR

RESULT_PATH = DATA_DIR / "rule1_all_latest.json"
FALLBACK_RESULT_PATH = Path(__file__).resolve().parent / "seed_data" / "rule1_all_latest.json"


def load_rule1_results(result_path: Path = RESULT_PATH) -> dict[str, Any]:
    """讀取最新的 Rule1 全族群掃描結果；沒有真實資料時退回範例資料。"""
    if not result_path.exists():
        if result_path == RESULT_PATH and FALLBACK_RESULT_PATH.exists():
            result_path = FALLBACK_RESULT_PATH
        else:
            raise RuntimeError("找不到 Rule1 結果檔。")

    try:
        content = result_path.read_text(encoding="utf-8")
        results = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Rule1 結果檔無法讀取：{error}") from error

    required_keys = {"generated_at", "summary", "groups"}
    if not required_keys.issubset(results):
        raise RuntimeError("Rule1 結果檔格式不完整。")

    return results
