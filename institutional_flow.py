"""Official institutional-flow sources shared by HanStock frontends."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


TPEX_INSTITUTIONAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
_FOREIGN_KEY = "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"
_TRUST_KEY = "SecuritiesInvestmentTrustCompanies-Difference"
_DEALER_KEY = "Dealers-Difference"


def _number(value: Any) -> int:
    text = str(value or "0").strip().replace(",", "")
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _display_roc_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) != 7 or not text.isdigit():
        return None
    return f"{int(text[:3]) + 1911:04d}/{text[3:5]}/{text[5:7]}"


def normalize_tpex_institutional_payload(payload: Any) -> dict[str, Any]:
    """Normalize the latest TPEx OpenAPI payload into stable compact rows."""
    if not isinstance(payload, list):
        raise RuntimeError("tpex_institutional_payload_invalid")

    parsed: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        date = _display_roc_date(item.get("Date"))
        code = str(item.get("SecuritiesCompanyCode") or "").strip().upper()
        name = str(item.get("CompanyName") or "").strip()
        if not date or not code or not name:
            continue
        if not all(key in item for key in (_FOREIGN_KEY, _TRUST_KEY, _DEALER_KEY)):
            continue
        parsed.append(
            {
                "date": date,
                "code": code,
                "name": name,
                "foreign": _number(item.get(_FOREIGN_KEY)),
                "trust": _number(item.get(_TRUST_KEY)),
                "dealer": _number(item.get(_DEALER_KEY)),
                "hedge": 0,
            }
        )

    latest_date = max((row["date"] for row in parsed), default=None)
    rows = [row for row in parsed if row["date"] == latest_date]
    if not latest_date or len(rows) < 600:
        raise RuntimeError(f"tpex_institutional_payload_incomplete_{len(rows)}")
    return {"data_date": latest_date, "count": len(rows), "rows": rows}


def fetch_tpex_institutional_latest(timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        TPEX_INSTITUTIONAL_URL,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.tpex.org.tw/",
            "User-Agent": "HanStock-Hub/1.0 Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"tpex_institutional_fetch_failed_{type(exc).__name__}") from exc
    return normalize_tpex_institutional_payload(payload)
