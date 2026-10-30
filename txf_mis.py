"""期交所 MIS（mis.taifex.com.tw）臺指期報價代打。

2026-10-30 09:13 使用者截圖：首頁「台指期近月」一直讀取中。tw-groups 的 Cloudflare Worker 直接打期交所 MIS 從日盤開盤後一直被回 HTTP 520
（同一時間從別的主機打都正常），所以 Worker 打不通時改走這裡：後端（Railway）代打 getQuoteList，原樣回 MIS 的 JSON，Worker 用同一套解析。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any

MIS_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
CACHE_SECONDS = 4          # 首頁每 20 秒問一次；多人同時開也只打期交所一次
TIMEOUT_SECONDS = 8

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def quote_list(market: str) -> dict[str, Any]:
    """market＝'0' 日盤（TXF…-F）、'1' 夜盤（TXF…-M）；其他一律當日盤。回期交所原樣的 JSON。"""
    market = "1" if str(market).strip() == "1" else "0"
    now = time.time()
    with _lock:
        hit = _cache.get(market)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    payload = {"MarketType": market, "SymbolType": "F", "KindID": "1", "CID": "TXF", "ExpireMonth": "", "RowSize": "全部", "PageNo": "", "SortColumn": "", "AscDesc": "A"}
    request = urllib.request.Request(
        MIS_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "origin": "https://mis.taifex.com.tw", "referer": "https://mis.taifex.com.tw/futures/", "user-agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))
    with _lock:
        _cache[market] = (time.time(), data)
    return data
