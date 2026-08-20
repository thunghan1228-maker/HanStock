"""Official institutional-flow sources shared by HanStock frontends."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


TPEX_INSTITUTIONAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
TPEX_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "tpex-institutional-latest.json"
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


def fetch_tpex_institutional_official(timeout: int = 30) -> dict[str, Any]:
    """Fetch the latest complete TPEx payload directly from the official API."""
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


def load_tpex_institutional_snapshot(path: str | Path = TPEX_SNAPSHOT_PATH) -> dict[str, Any]:
    """Load the latest snapshot produced by the GitHub Actions Taiwan-data runner."""
    snapshot_path = Path(path)
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"tpex_institutional_snapshot_unavailable_{type(exc).__name__}") from exc

    rows = payload.get("rows") if isinstance(payload, dict) else None
    data_date = str(payload.get("data_date") or "") if isinstance(payload, dict) else ""
    if (
        not isinstance(rows, list)
        or len(rows) < 600
        or len(data_date) != 10
        or any(not isinstance(row, dict) or row.get("date") != data_date for row in rows)
    ):
        raise RuntimeError(f"tpex_institutional_snapshot_invalid_{len(rows) if isinstance(rows, list) else 0}")
    return {
        "status": "ok",
        "data_date": data_date,
        "count": len(rows),
        "generated_at": payload.get("generated_at"),
        "source": payload.get("source") or "TPEx OpenAPI via GitHub Actions",
        "rows": rows,
    }


def fetch_tpex_institutional_latest(
    timeout: int = 30,
    snapshot_path: str | Path = TPEX_SNAPSHOT_PATH,
) -> dict[str, Any]:
    """Prefer the official API and fall back to the last verified GitHub snapshot."""
    try:
        return fetch_tpex_institutional_official(timeout=timeout)
    except RuntimeError as official_error:
        try:
            return load_tpex_institutional_snapshot(snapshot_path)
        except RuntimeError as snapshot_error:
            raise RuntimeError(f"{official_error}; {snapshot_error}") from official_error
