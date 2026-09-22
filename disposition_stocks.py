"""處置股（TWSE／TPEx 每日公布的處置有價證券）清單。

兩邊都走官方 OpenAPI：
- TWSE：https://openapi.twse.com.tw/v1/announcement/punish
- TPEx：https://www.tpex.org.tw/openapi/v1/tpex_disposal_information

兩邊欄位名稱不一樣、也可能改版，所以解析採「找欄位」：代號欄（鍵名含 Code／代號）、名稱欄
（Name／名稱）、處置期間欄（Period／期間／起迄，或分開的 Start／End、起／迄），日期同時
支援民國（1140922、114/09/22）與西元。抓不到就沿用上一次的清單，錯誤與原始欄位名稱都留在
status 裡，正式環境看一眼就對得出來（開發環境連不到外網，無法實測）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.request import Request, urlopen

logger = logging.getLogger("hanstock.disposition")
TW_TZ = timezone(timedelta(hours=8))
TWSE_URL = os.getenv("HANSTOCK_TWSE_DISPOSITION_URL", "https://openapi.twse.com.tw/v1/announcement/punish")
TPEX_URL = os.getenv("HANSTOCK_TPEX_DISPOSITION_URL", "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information")
REFRESH_SECONDS = max(300, int(os.getenv("HANSTOCK_DISPOSITION_REFRESH_SECONDS", "1800")))
# 期間欄位解析不出來時，公布日起這麼多天內都當作處置中（處置通常 10 個交易日）。
FALLBACK_ACTIVE_DAYS = 14
Fetcher = Callable[[str], Any]

_lock = threading.Lock()
_map: dict[str, dict[str, Any]] = {}
_status: dict[str, Any] = {"fetchedAt": None, "sources": {}, "count": 0}
_started = False
_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
_DATE_RE = re.compile(r"(\d{2,4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})")
_PERIOD_SPLIT_RE = re.compile(r"[～~至到]|(?<=\d{7})-(?=\d{7})|(?<=\d{8})-(?=\d{8})|\s+-\s+")


def _default_fetcher(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"})
    with urlopen(request, timeout=20) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8-sig"))


def parse_date(text: Any) -> Optional[date]:
    """民國（1140922、114/09/22、114年9月22日）與西元（20260922、2026-09-22）都收。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    try:
        if len(digits) == 7:  # 1140922 / 114/09/22
            year, month, day = int(digits[:3]), int(digits[3:5]), int(digits[5:7])
        elif len(digits) == 8:  # 20260922 / 2026-09-22
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        else:
            match = _DATE_RE.search(raw)
            if not match:
                return None
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 1911:
            year += 1911
        return date(year, month, day)
    except ValueError:
        return None


def parse_period(text: Any) -> tuple[Optional[date], Optional[date]]:
    raw = str(text or "").strip()
    if not raw:
        return None, None
    parts = [part for part in _PERIOD_SPLIT_RE.split(raw) if part.strip()]
    dates = [parse_date(part) for part in parts]
    dates = [item for item in dates if item]
    if not dates:
        return None, None
    return dates[0], dates[-1]


def _pick(row: dict[str, Any], *needles: str) -> Any:
    for key, value in row.items():
        lowered = str(key).lower()
        if any(needle.lower() in lowered for needle in needles):
            return value
    return None


def extract_rows(rows: Any, *, source: str, today: date) -> dict[str, dict[str, Any]]:
    """把一份公告列表轉成 {代號: {...}}，只留今天仍在處置期間內的。"""
    result: dict[str, dict[str, Any]] = {}
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("aaData") or []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        code_raw = _pick(row, "SecuritiesCompanyCode", "Code", "代號", "股票代碼")
        code = str(code_raw or "").strip().upper()
        if not _CODE_RE.match(code):
            continue
        name = str(_pick(row, "CompanyName", "Name", "名稱") or "").strip()
        start, end = parse_period(_pick(row, "Period", "期間", "起迄"))
        if start is None:
            start = parse_date(_pick(row, "StartDate", "Start", "起始", "開始"))
        if end is None:
            end = parse_date(_pick(row, "EndDate", "End", "迄", "結束"))
        published = parse_date(_pick(row, "Date", "日期", "公布"))
        if start and end:
            active = start <= today <= end
        elif end:
            active = today <= end
        elif published:
            active = today <= published + timedelta(days=FALLBACK_ACTIVE_DAYS)
        else:
            active = False
        if not active:
            continue
        reason = str(_pick(row, "Reason", "原因", "處置內容", "Content") or "").strip()[:80]
        existing = result.get(code)
        entry = {
            "code": code, "name": name, "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None, "reason": reason, "source": source,
        }
        if existing is None or (entry["end"] or "") > (existing.get("end") or ""):
            result[code] = entry
    return result


def refresh(*, fetcher: Optional[Fetcher] = None, today: Optional[date] = None) -> dict[str, Any]:
    """兩邊各抓一次；任何一邊失敗就保留那一邊上次的結果。"""
    global _map
    call = fetcher or _default_fetcher
    today = today or datetime.now(TW_TZ).date()
    merged: dict[str, dict[str, Any]] = {}
    sources: dict[str, Any] = {}
    with _lock:
        previous = dict(_map)
    for source, url in (("twse", TWSE_URL), ("tpex", TPEX_URL)):
        try:
            payload = call(url)
            rows = extract_rows(payload, source=source, today=today)
            sample = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None
            sources[source] = {
                "ok": True, "active": len(rows), "rows": len(payload) if isinstance(payload, list) else None,
                "fields": list(sample.keys())[:12] if sample else [], "error": None,
            }
            merged.update(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Disposition] %s 抓取失敗: %s", source, exc)
            kept = {code: item for code, item in previous.items() if item.get("source") == source}
            merged.update(kept)
            sources[source] = {"ok": False, "active": len(kept), "rows": None, "fields": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
    with _lock:
        _map = merged
        _status.update({
            "fetchedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "sources": sources, "count": len(merged),
            "codes": sorted(merged),
        })
        return dict(_status)


def get_disposition_map() -> dict[str, dict[str, Any]]:
    with _lock:
        return dict(_map)


def is_disposition(code: str) -> bool:
    with _lock:
        return str(code).strip().upper() in _map


def disposition_status() -> dict[str, Any]:
    with _lock:
        return dict(_status)


def _loop() -> None:
    while True:
        try:
            refresh()
        except Exception:  # noqa: BLE001
            logger.exception("[Disposition] 更新失敗")
        time.sleep(REFRESH_SECONDS)


def start_disposition_collector() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        if os.getenv("HANSTOCK_DISPOSITION_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return False
        _started = True
    threading.Thread(target=_loop, name="hanstock-disposition", daemon=True).start()
    return True
