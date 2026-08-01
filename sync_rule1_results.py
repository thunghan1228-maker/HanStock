"""把本機最新 Rule1 JSON 安全同步到雲端 HanStock API。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from read_rule1_results import RESULT_PATH, load_rule1_results


def sync_results() -> None:
    api_base_url = os.getenv("HANSTOCK_CLOUD_API_URL", "").rstrip("/")
    sync_token = os.getenv("HANSTOCK_SYNC_TOKEN", "")

    if not api_base_url:
        raise RuntimeError("請設定 HANSTOCK_CLOUD_API_URL。")
    if not sync_token:
        raise RuntimeError("請設定 HANSTOCK_SYNC_TOKEN。")

    results = load_rule1_results(RESULT_PATH)
    payload = json.dumps(results, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        f"{api_base_url}/api/rule1/sync",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-HanStock-Sync-Token": sync_token,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"同步失敗（HTTP {error.code}）：{detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"無法連線到雲端 API：{error.reason}") from error

    print("Rule1 結果同步成功！")
    print(response_body)


if __name__ == "__main__":
    try:
        sync_results()
    except RuntimeError as error:
        print(f"同步失敗：{error}")
        raise SystemExit(1) from error
