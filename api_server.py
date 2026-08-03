"""HanStock 網站 API（v3.3 預覽整合版）。

保留既有 Rule1、69 個族群與同步 API，同時加入新版 UI 所需的相容端點、
PWA 靜態檔案、API no-store 標頭與可選的 canonical 網址轉址。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from read_rule1_results import RESULT_PATH, load_rule1_results
from stock_groups import STOCK_GROUPS, resolve_group_names


API_VERSION = "1.1.0-preview"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = Path(os.getenv("HANSTOCK_DATA_DIR", str(ROOT_DIR / "data"))).expanduser()
REALTIME_RESULT_PATH = DATA_DIR / "realtime_latest.json"
REALTIME_STALE_SECONDS = int(os.getenv("HANSTOCK_REALTIME_STALE_SECONDS", "30"))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "HANSTOCK_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://hanstock.xyz,https://www.hanstock.xyz",
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _meta(source: str, **extra: Any) -> dict[str, Any]:
    return {
        "source": source,
        "is_mock": False,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **extra,
    }


def _flatten_passed_stocks(results: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for group in results.get("groups", []):
        group_name = group.get("group_name", "")
        for stock in group.get("passed_stocks", []):
            flattened.append({"group_name": group_name, **stock})
    return flattened


def _latest_results_or_404() -> dict[str, Any]:
    try:
        return load_rule1_results()
    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _latest_results_optional() -> dict[str, Any] | None:
    try:
        return load_rule1_results()
    except RuntimeError:
        return None



def _coerce_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def _normalize_realtime_quote(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("code") or item.get("stock_code") or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="即時行情缺少股票代號。")
    normalized = {
        "code": code,
        "name": str(item.get("name") or item.get("stock_name") or "").strip(),
        "price": _coerce_number(item.get("price") if item.get("price") is not None else item.get("close")),
        "change_rate": _coerce_number(item.get("change_rate")),
        "price_change": _coerce_number(item.get("price_change") if item.get("price_change") is not None else item.get("change_price")),
        "volume": _coerce_number(item.get("volume") if item.get("volume") is not None else item.get("total_volume")),
        "bid": _coerce_number(item.get("bid") if item.get("bid") is not None else item.get("buy_price")),
        "ask": _coerce_number(item.get("ask") if item.get("ask") is not None else item.get("sell_price")),
        "open": _coerce_number(item.get("open")),
        "high": _coerce_number(item.get("high")),
        "low": _coerce_number(item.get("low")),
        "previous_close": _coerce_number(item.get("previous_close") if item.get("previous_close") is not None else item.get("yesterday_close")),
        "updated_at": item.get("updated_at"),
    }
    if normalized["price"] is None:
        raise HTTPException(status_code=422, detail=f"{code} 缺少成交價。")
    return normalized


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_realtime_results() -> dict[str, Any] | None:
    if not REALTIME_RESULT_PATH.exists():
        return None
    try:
        payload = json.loads(REALTIME_RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _save_realtime_results(payload: dict[str, Any]) -> None:
    REALTIME_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = REALTIME_RESULT_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(REALTIME_RESULT_PATH)


def _realtime_age_seconds(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    timestamp = _parse_iso_datetime(payload.get("updated_at") or payload.get("received_at"))
    if timestamp is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds())


def _latest_realtime_quote(code: str) -> dict[str, Any] | None:
    payload = _load_realtime_results() or {}
    for quote in payload.get("quotes", []):
        if str(quote.get("code")) == str(code):
            return quote
    return None


def _validate_realtime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    quotes = payload.get("quotes")
    if not isinstance(quotes, list) or not quotes:
        raise HTTPException(status_code=422, detail="即時行情 quotes 必須是非空陣列。")
    if len(quotes) > 1000:
        raise HTTPException(status_code=413, detail="單次最多同步 1000 檔行情。")
    normalized_quotes = [_normalize_realtime_quote(item) for item in quotes if isinstance(item, dict)]
    if not normalized_quotes:
        raise HTTPException(status_code=422, detail="沒有可用的即時行情。")
    updated_at = payload.get("updated_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "source": str(payload.get("source") or "shioaji-local"),
        "updated_at": updated_at,
        "received_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "quotes": normalized_quotes,
    }


def _validate_sync_payload(payload: dict[str, Any]) -> None:
    required_keys = {"generated_at", "summary", "groups"}
    if not required_keys.issubset(payload):
        raise HTTPException(status_code=422, detail="Rule1 JSON 格式不完整。")


def _save_synced_results(payload: dict[str, Any]) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = RESULT_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(RESULT_PATH)


def _memberships_for_stock(code: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for group_name, stocks in STOCK_GROUPS.items():
        for stock_code, stock_name in stocks:
            if str(stock_code) == str(code):
                matches.append(
                    {
                        "name": group_name,
                        "stock_name": stock_name,
                        "stock_count": len(stocks),
                    }
                )
                break
    return matches


def _latest_stock_snapshot(code: str) -> dict[str, Any] | None:
    results = _latest_results_optional()
    if not results:
        return None
    for stock in _flatten_passed_stocks(results):
        if str(stock.get("stock_code")) == str(code):
            return stock
    return None


app = FastAPI(
    title="HanStock API",
    description="HanStock 台灣股票族群與 Rule1 選股結果 API",
    version=API_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def canonical_and_cache_headers(request: Request, call_next):
    canonical_host = os.getenv("HANSTOCK_CANONICAL_HOST", "hanstock.xyz").strip().lower()
    redirect_enabled = _env_bool("HANSTOCK_CANONICAL_REDIRECT", False)
    force_https = _env_bool("HANSTOCK_FORCE_HTTPS", False)
    trust_proxy = _env_bool("HANSTOCK_TRUST_PROXY_HEADERS", True)

    host = request.url.netloc.lower()
    scheme = request.url.scheme.lower()
    if trust_proxy:
        host = request.headers.get("x-forwarded-host", host).split(",", 1)[0].strip().lower()
        scheme = request.headers.get("x-forwarded-proto", scheme).split(",", 1)[0].strip().lower()

    if redirect_enabled and host in {canonical_host, f"www.{canonical_host}"}:
        wrong_host = host != canonical_host
        wrong_scheme = force_https and scheme != "https"
        if wrong_host or wrong_scheme:
            target_scheme = "https" if force_https else scheme
            target = f"{target_scheme}://{canonical_host}{request.url.path}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=308)

    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    dashboard_path = WEB_DIR / "index.html"
    if not dashboard_path.exists():
        return "<h1>HanStock API</h1><p>找不到 web/index.html。</p>"
    return dashboard_path.read_text(encoding="utf-8")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    _path = WEB_DIR / "manifest.webmanifest" if (WEB_DIR / "manifest.webmanifest").exists() else WEB_DIR / "web" / "manifest.webmanifest"
    return FileResponse(_path, media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    _path = WEB_DIR / "sw.js" if (WEB_DIR / "sw.js").exists() else WEB_DIR / "web" / "sw.js"
    response = FileResponse(_path, media_type="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "HanStock API",
        "version": API_VERSION,
        "environment": os.getenv("HANSTOCK_ENV", "production"),
        "data_mode": "live-rule1-without-realtime",
        "canonical_host": os.getenv("HANSTOCK_CANONICAL_HOST", "hanstock.xyz"),
        "rule1_result_exists": RESULT_PATH.exists(),
        "group_count": len(STOCK_GROUPS),
    }


@app.get("/api/meta/config")
def public_config() -> dict[str, Any]:
    return {
        "api_base_url": "",
        "environment": os.getenv("HANSTOCK_ENV", "production"),
        "data_mode": "live-rule1-without-realtime",
        "mock_fallback_allowed": False,
    }


@app.get("/api/realtime/latest")
def realtime_latest() -> dict[str, Any]:
    realtime = _load_realtime_results() or {}
    quotes = realtime.get("quotes", [])
    rule1_results = _latest_results_optional() or {}
    rule1_summary = rule1_results.get("summary", {})
    up = sum(1 for item in quotes if (item.get("change_rate") or 0) > 0)
    down = sum(1 for item in quotes if (item.get("change_rate") or 0) < 0)
    age_seconds = _realtime_age_seconds(realtime)
    is_stale = age_seconds is None or age_seconds > REALTIME_STALE_SECONDS
    return {
        "updated_at": realtime.get("updated_at"),
        "received_at": realtime.get("received_at"),
        "summary": {
            "up": up,
            "down": down,
            "flat": max(0, len(quotes) - up - down),
            "rule1_passed": rule1_summary.get("total_passed_records", 0),
            "group_count": len(STOCK_GROUPS),
        },
        "quotes": quotes,
        "notice": (
            "即時行情已超過更新時限，請檢查本機同步程式。"
            if quotes and is_stale
            else ("即時 Shioaji 行情尚未同步。" if not quotes else "")
        ),
        "_meta": _meta(
            realtime.get("source", "hanstock-api"),
            realtime_available=bool(quotes),
            is_stale=is_stale,
            age_seconds=round(age_seconds, 1) if age_seconds is not None else None,
        ),
    }


@app.get("/api/groups")
def list_groups(include_stocks: bool = Query(default=False)) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for group_name, stocks in STOCK_GROUPS.items():
        item: dict[str, Any] = {
            "group_name": group_name,
            "stock_count": len(stocks),
        }
        if include_stocks:
            item["stocks"] = [
                {"stock_code": code, "stock_name": name}
                for code, name in stocks
            ]
        groups.append(item)
        items.append(
            {
                "name": group_name,
                "count": len(stocks),
                "change_rate": None,
                "up": 0,
                "down": 0,
                "flat": len(stocks),
                "market_data_available": False,
            }
        )
    return {
        "group_count": len(groups),
        "groups": groups,
        "count": len(groups),
        "items": items,
        "_meta": _meta("stock_groups.py", market_data_available=False),
    }


@app.get("/api/groups/{keyword}")
def get_groups_by_keyword(keyword: str) -> dict[str, Any]:
    try:
        group_names = resolve_group_names(keyword)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    groups = []
    for group_name in group_names:
        stocks = STOCK_GROUPS[group_name]
        groups.append(
            {
                "group_name": group_name,
                "stock_count": len(stocks),
                "stocks": [
                    {"stock_code": code, "stock_name": name}
                    for code, name in stocks
                ],
            }
        )
    return {
        "keyword": keyword,
        "matched_groups": groups,
        "_meta": _meta("stock_groups.py"),
    }


@app.get("/api/stocks/{code}")
def stock_detail(code: str) -> dict[str, Any]:
    memberships = _memberships_for_stock(code)
    if not memberships:
        raise HTTPException(status_code=404, detail="找不到股票代號或所屬族群。")

    snapshot = _latest_stock_snapshot(code) or {}
    realtime = _latest_realtime_quote(code) or {}
    name = realtime.get("name") or snapshot.get("stock_name") or memberships[0].get("stock_name") or ""
    price = realtime.get("price") if realtime else snapshot.get("today_close")
    return {
        "code": str(code),
        "name": name,
        "groups": [item["name"] for item in memberships],
        "group_memberships": memberships,
        "price": price,
        "change_rate": realtime.get("change_rate") if realtime else snapshot.get("change_rate"),
        "price_change": realtime.get("price_change") if realtime else snapshot.get("price_change"),
        "open": realtime.get("open"),
        "high": realtime.get("high"),
        "low": realtime.get("low"),
        "previous_close": realtime.get("previous_close") if realtime else snapshot.get("yesterday_close"),
        "volume": realtime.get("volume"),
        "bid": realtime.get("bid"),
        "ask": realtime.get("ask"),
        "updated_at": realtime.get("updated_at") if realtime else (_latest_results_optional() or {}).get("generated_at"),
        "signals": ([{"rule": "Rule1", "status": "passed"}] if snapshot else []),
        "notice": "" if realtime else "目前沒有此股票的即時行情；價格可能來自最新 Rule1 結果。",
        "_meta": _meta(
            "shioaji-realtime+stock_groups.py" if realtime else "stock_groups.py+rule1-latest",
            realtime_available=bool(realtime),
        ),
    }


@app.get("/api/stocks/{code}/groups")
def stock_groups(code: str) -> dict[str, Any]:
    memberships = _memberships_for_stock(code)
    if not memberships:
        raise HTTPException(status_code=404, detail="找不到股票代號或所屬族群。")
    return {"code": str(code), "groups": memberships, "_meta": _meta("stock_groups.py")}


@app.get("/api/rule1/latest")
@app.get("/api/rules/rule1/latest", include_in_schema=False)
def latest_rule1(passed_only: bool = Query(default=False)) -> dict[str, Any]:
    results = _latest_results_or_404()
    if not passed_only:
        payload = dict(results)
        payload["_meta"] = _meta("rule1_all_latest.json")
        return payload
    return {
        "strategy": results.get("strategy"),
        "generated_at": results.get("generated_at"),
        "summary": results.get("summary", {}),
        "passed_stocks": _flatten_passed_stocks(results),
        "_meta": _meta("rule1_all_latest.json"),
    }


@app.get("/api/rule1/passed")
def passed_rule1() -> dict[str, Any]:
    results = _latest_results_or_404()
    stocks = _flatten_passed_stocks(results)
    return {
        "strategy": results.get("strategy"),
        "generated_at": results.get("generated_at"),
        "count": len(stocks),
        "stocks": stocks,
        "_meta": _meta("rule1_all_latest.json"),
    }


@app.get("/api/rule2/latest")
@app.get("/api/rules/rule2/latest", include_in_schema=False)
def rule2_latest() -> dict[str, Any]:
    return {
        "rule": {
            "id": "rule2",
            "name": "Rule2",
            "timeframe": "5m",
            "version": "draft",
            "enabled": False,
        },
        "generated_at": None,
        "summary": {"scanned": 0, "signals": 0, "long": 0, "short": 0},
        "signals": [],
        "notice": "Rule2 詳細條件尚未由使用者正式確認，因此掃描與訊號保持停用。",
        "_meta": _meta("disabled-rule"),
    }


@app.post("/api/rules/rule2/scan")
def rule2_scan_disabled() -> dict[str, Any]:
    raise HTTPException(status_code=409, detail="Rule2 條件尚未確認，禁止執行正式掃描。")


@app.get("/api/admin/status")
def admin_status() -> dict[str, Any]:
    results = _latest_results_optional()
    generated_at = results.get("generated_at") if results else None
    passed = (results or {}).get("summary", {}).get("total_passed_records", 0)
    realtime = _load_realtime_results() or {}
    quote_count = len(realtime.get("quotes", []))
    age_seconds = _realtime_age_seconds(realtime)
    realtime_ok = quote_count > 0 and age_seconds is not None and age_seconds <= REALTIME_STALE_SECONDS
    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "services": [
            {"key": "api", "title": "HanStock API", "status": "ok", "value": "正常", "detail": f"版本 {API_VERSION}", "icon": "🌐"},
            {"key": "groups", "title": "股票族群", "status": "ok", "value": f"{len(STOCK_GROUPS)} 族群", "detail": "沿用正式 stock_groups.py", "icon": "📁"},
            {"key": "rule1", "title": "Rule1 最新掃描", "status": "ok" if results else "warn", "value": generated_at or "尚無結果", "detail": f"符合 {passed} 筆", "icon": "🔍"},
            {"key": "realtime", "title": "雲端即時行情", "status": "ok" if realtime_ok else "warn", "value": f"{quote_count} 檔" if quote_count else "尚未同步", "detail": (f"最近更新 {round(age_seconds, 1)} 秒前" if age_seconds is not None else "等待本機 Shioaji 同步"), "icon": "📈"},
            {"key": "rule2", "title": "Rule2 五分鐘K", "status": "warn", "value": "停用", "detail": "等待正式條件確認", "icon": "⏱"},
        ],
        "logs": [],
        "_meta": _meta("hanstock-api"),
    }


@app.post("/api/realtime/sync")
def sync_realtime(
    payload: dict[str, Any] = Body(...),
    x_hanstock_sync_token: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_token = os.getenv("HANSTOCK_SYNC_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="伺服器尚未設定同步金鑰。")
    if x_hanstock_sync_token != expected_token:
        raise HTTPException(status_code=401, detail="同步金鑰不正確。")
    normalized = _validate_realtime_payload(payload)
    _save_realtime_results(normalized)
    return {
        "status": "ok",
        "message": "即時行情同步成功",
        "updated_at": normalized.get("updated_at"),
        "quote_count": len(normalized.get("quotes", [])),
    }


@app.post("/api/rule1/sync")
def sync_rule1(
    payload: dict[str, Any] = Body(...),
    x_hanstock_sync_token: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_token = os.getenv("HANSTOCK_SYNC_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=503, detail="伺服器尚未設定同步金鑰。")
    if x_hanstock_sync_token != expected_token:
        raise HTTPException(status_code=401, detail="同步金鑰不正確。")
    _validate_sync_payload(payload)
    _save_synced_results(payload)
    return {
        "status": "ok",
        "message": "Rule1 結果同步成功",
        "generated_at": payload.get("generated_at"),
        "total_groups": payload.get("summary", {}).get("total_groups"),
        "total_passed_records": payload.get("summary", {}).get("total_passed_records"),
    }


# 靜態目錄必須放在所有 API 路由之後，避免攔截 /api/*。
# 相容兩種目錄結構：web/ui（正常）或 web/web/ui（套用更新時產生的巢狀）。
_ui_dir = WEB_DIR / "ui" if (WEB_DIR / "ui").exists() else WEB_DIR / "web" / "ui" if (WEB_DIR / "web" / "ui").exists() else None
_icons_dir = WEB_DIR / "icons" if (WEB_DIR / "icons").exists() else WEB_DIR / "web" / "icons" if (WEB_DIR / "web" / "icons").exists() else None
if _ui_dir:
    app.mount("/ui", StaticFiles(directory=_ui_dir), name="ui")
if _icons_dir:
    app.mount("/icons", StaticFiles(directory=_icons_dir), name="icons")
