"""處置股（TWSE／TPEx 每日公布的處置有價證券）清單。

三個來源合併：
- Shioaji：api.punish()（永豐整理好的處置股欄狀資料，上市櫃都有；要登入後才查得到）
- TWSE：https://openapi.twse.com.tw/v1/announcement/punish
- TPEx：https://www.tpex.org.tw/openapi/v1/tpex_disposal_information（Railway 出去會被擋 403，
  所以櫃買那邊主要靠 Shioaji 的 punish）

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
_upcoming: dict[str, dict[str, Any]] = {}   # 已公告、還沒開始的處置（波段日報「明日起處置」用）
_status: dict[str, Any] = {"fetchedAt": None, "sources": {}, "count": 0}
_started = False
_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
_DATE_RE = re.compile(r"(\d{2,4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})")
_PERIOD_SPLIT_RE = re.compile(r"[～~至到]|(?<=\d{7})-(?=\d{7})|(?<=\d{8})-(?=\d{8})|\s+-\s+")


_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    # TPEx 的 OpenAPI 對非瀏覽器 User-Agent 回 403，用一般瀏覽器的字串。
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
}


def _default_fetcher(url: str) -> Any:
    headers = dict(_BROWSER_HEADERS)
    if "tpex.org.tw" in url:
        headers["Referer"] = "https://www.tpex.org.tw/"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=20) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8-sig"))


def _default_punish_fetcher() -> Any:
    """已登入的 Shioaji 才查；沒登入（備援專案、測試）回 None。"""
    try:
        from quote_service import get_quote_service

        service = get_quote_service()
    except Exception:  # noqa: BLE001
        return None
    api = getattr(service, "api", None)
    punish = getattr(api, "punish", None)
    if api is None or not callable(punish):
        return None
    try:
        return punish(timeout=30000)
    except TypeError:
        return punish()


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_date(value)


def _column(payload: Any, name: str) -> list[Any]:
    value = None
    if isinstance(payload, dict):
        value = payload.get(name)
    else:
        try:
            value = getattr(payload, name)
        except Exception:  # noqa: BLE001
            value = None
        if value is None and hasattr(payload, "get"):
            try:
                value = payload.get(name)
            except Exception:  # noqa: BLE001
                value = None
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _is_active(start: Optional[date], end: Optional[date], published: Optional[date], today: date) -> bool:
    if start and end:
        return start <= today <= end
    if end:
        return today <= end
    if published:
        return today <= published + timedelta(days=FALLBACK_ACTIVE_DAYS)
    return False


def _remember(result: dict[str, dict[str, Any]], entry: dict[str, Any]) -> None:
    existing = result.get(entry["code"])
    if existing is None or (entry["end"] or "") > (existing.get("end") or ""):
        result[entry["code"]] = entry


def extract_punish(payload: Any, *, today: date) -> dict[str, dict[str, Any]]:
    """Shioaji api.punish() 的欄狀資料（code／start_date／end_date／description／announced_date）
    轉成跟 extract_rows 一樣的 {代號: {...}}，只留今天仍在處置期間內的。"""
    result: dict[str, dict[str, Any]] = {}
    codes = _column(payload, "code")
    if not codes:
        return result
    starts, ends = _column(payload, "start_date"), _column(payload, "end_date")
    descriptions, announced = _column(payload, "description"), _column(payload, "announced_date")

    def _at(column: list[Any], index: int) -> Any:
        return column[index] if index < len(column) else None

    for index, code_raw in enumerate(codes):
        code = str(code_raw or "").strip().upper()
        if not _CODE_RE.match(code):
            continue
        start, end, published = _as_date(_at(starts, index)), _as_date(_at(ends, index)), _as_date(_at(announced, index))
        if not _is_active(start, end, published, today):
            continue
        _remember(result, {
            "code": code, "name": "", "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None, "reason": str(_at(descriptions, index) or "").strip()[:80],
            "source": "shioaji",
        })
    return result


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
        if not _is_active(start, end, published, today):
            continue
        reason = str(_pick(row, "Reason", "原因", "處置內容", "Content") or "").strip()[:80]
        _remember(result, {
            "code": code, "name": name, "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None, "reason": reason, "source": source,
        })
    return result


UPCOMING_MAX_DAYS = 14


def extract_upcoming(rows: Any, *, source: str, today: date) -> dict[str, dict[str, Any]]:
    """公告裡起始日在今天之後（14 天內）的處置：還沒開始，不算處置中，但波段日報要列「明日起處置」。"""
    result: dict[str, dict[str, Any]] = {}
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("aaData") or []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(_pick(row, "SecuritiesCompanyCode", "Code", "代號", "股票代碼") or "").strip().upper()
        if not _CODE_RE.match(code):
            continue
        start, end = parse_period(_pick(row, "Period", "期間", "起迄"))
        if start is None:
            start = parse_date(_pick(row, "StartDate", "Start", "起始", "開始"))
        if end is None:
            end = parse_date(_pick(row, "EndDate", "End", "迄", "結束"))
        if not start or start <= today or (start - today).days > UPCOMING_MAX_DAYS:
            continue
        name = str(_pick(row, "CompanyName", "Name", "名稱") or "").strip()
        reason = str(_pick(row, "Reason", "原因", "處置內容", "Content") or "").strip()[:80]
        _remember(result, {"code": code, "name": name, "start": start.isoformat(), "end": end.isoformat() if end else None,
                           "reason": reason, "source": source, "upcoming": True})
    return result


def refresh(
    *, fetcher: Optional[Fetcher] = None, today: Optional[date] = None, punish_fetcher: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    """三個來源各抓一次；任何一邊失敗就保留那一邊上次的結果。"""
    global _map, _upcoming
    call = fetcher or _default_fetcher
    upcoming: dict[str, dict[str, Any]] = {}
    punish_call = punish_fetcher or _default_punish_fetcher
    today = today or datetime.now(TW_TZ).date()
    merged: dict[str, dict[str, Any]] = {}
    sources: dict[str, Any] = {}
    with _lock:
        previous = dict(_map)

    def _still_active(source: str) -> dict[str, dict[str, Any]]:
        """上一次這個來源的清單裡，處置期間還沒過的（沒有迄日的也留著，等它被新清單取代）。"""
        return {
            code: item for code, item in previous.items()
            if item.get("source") == source and (not item.get("end") or item["end"] >= today.isoformat())
        }

    def _keep_previous(source: str, error: str) -> None:
        kept = _still_active(source)
        for item in kept.values():
            _remember(merged, item)
        sources[source] = {"ok": False, "active": len(kept), "rows": None, "fields": [], "error": error[:200]}

    def _fill_empty(source: str, rows: dict[str, dict[str, Any]], status: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """來源成功但回空清單（永豐 punish 盤後就是 0 筆）：上一次仍在處置期間內的先沿用，處置到期自然消失，
        不然上櫃處置股一到收盤就沒有「處置到幾號」。"""
        if rows:
            return rows
        kept = _still_active(source)
        if kept:
            status["active"] = len(kept)
            status["note"] = f"這次回空清單，沿用上次仍在期間內的 {len(kept)} 檔"
        return kept

    try:
        payload = punish_call()
        if payload is None:
            _keep_previous("shioaji", "未登入（punish 只有登入後查得到）")
        else:
            rows = extract_punish(payload, today=today)
            fields = list(payload.keys())[:12] if hasattr(payload, "keys") else []
            sources["shioaji"] = {"ok": True, "active": len(rows), "rows": len(_column(payload, "code")), "fields": fields, "error": None}
            for item in _fill_empty("shioaji", rows, sources["shioaji"]).values():
                _remember(merged, item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Disposition] shioaji punish 查詢失敗: %s", exc)
        _keep_previous("shioaji", f"{type(exc).__name__}: {exc}")

    for source, url in (("twse", TWSE_URL), ("tpex", TPEX_URL)):
        try:
            payload = call(url)
            rows = extract_rows(payload, source=source, today=today)
            upcoming.update(extract_upcoming(payload, source=source, today=today))
            sample = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None
            sources[source] = {
                "ok": True, "active": len(rows), "rows": len(payload) if isinstance(payload, list) else None,
                "fields": list(sample.keys())[:12] if sample else [], "error": None,
            }
            for item in _fill_empty(source, rows, sources[source]).values():
                _remember(merged, item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Disposition] %s 抓取失敗: %s", source, exc)
            _keep_previous(source, f"{type(exc).__name__}: {exc}")
    with _lock:
        _map = merged
        _upcoming = upcoming
        _status.update({
            "fetchedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "sources": sources, "count": len(merged),
            "codes": sorted(merged),
        })
        return dict(_status)


def get_disposition_map() -> dict[str, dict[str, Any]]:
    with _lock:
        return dict(_map)


def get_upcoming_map() -> dict[str, dict[str, Any]]:
    """已公告、還沒開始的處置（{代號: {code, name, start, end, reason, source, upcoming: True}}）。"""
    with _lock:
        return dict(_upcoming)


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
