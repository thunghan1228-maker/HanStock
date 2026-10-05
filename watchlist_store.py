"""自選股（2026-10-05 使用者）：清單存在後端，兩台電腦＋手機輸入同一個「同步碼」就看到同一份。

沒有帳號登入，同步碼就像密碼：資料表只存它的 SHA-256，資料庫裡看不到原本的同步碼；
同步碼放在 POST 內容裡，不放網址（網址會留在各種紀錄裡）。
每份清單有版本號：存檔要帶「讀到的版本」，別台電腦已經先存過（版本不同）就回 conflict 跟最新的一份，
前端換成最新的再做一次，不會互相蓋掉。

清單格式（存之前一律重新整理過，不認得的欄位丟掉）：
  {"groups": [{"id", "name", "items": [{"code", "note", "addedAt", "above", "below", "ma", "cost", "lots"}]}],
   "settings": {...}}
  above／below／ma＝到價提醒與跌破均線提醒（個股訊號追蹤用）、cost／lots＝持股成本與張數，先留著欄位。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from database import get_connection, initialize_database

TW_TZ = timezone(timedelta(hours=8))
KEY_RE = re.compile(r"^[\w-]{6,40}$")          # 英數字、中文、底線、減號，6～40 個字
CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
GROUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
SETTING_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
MAX_GROUPS = 20
MAX_ITEMS = 200
MAX_SETTINGS = 30
MAX_BYTES = 64 * 1024
MA_CHOICES = (0, 5, 10, 20)

_lock = threading.Lock()


class WatchlistError(ValueError):
    """同步碼或清單格式不對（回 400）。"""


def normalize_key(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if not KEY_RE.match(key):
        raise WatchlistError("同步碼要 6～40 個字（英文、數字、中文、底線、減號）")
    return key


def _key_hash(key: str) -> str:
    return hashlib.sha256(("tw-groups-watchlist:" + key).encode("utf-8")).hexdigest()


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS watchlists ("
        "key_hash TEXT PRIMARY KEY, data_json TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL)"
    )


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and 0 < number < 1e7 else None


def _item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    code = _text(raw.get("code"), 6).upper()
    if not CODE_RE.match(code):
        return None
    item: dict[str, Any] = {"code": code}
    note = _text(raw.get("note"), 200)
    if note:
        item["note"] = note
    added = _text(raw.get("addedAt"), 25)
    if added:
        item["addedAt"] = added
    for key in ("above", "below", "cost", "lots"):
        number = _positive(raw.get(key))
        if number is not None:
            item[key] = number
    try:
        ma = int(raw.get("ma") or 0)
    except (TypeError, ValueError):
        ma = 0
    if ma in MA_CHOICES and ma:
        item["ma"] = ma
    return item


def sanitize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("groups", []), list):
        raise WatchlistError("清單格式不對")
    groups: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(data.get("groups") or []):
        if len(groups) >= MAX_GROUPS:
            break
        if not isinstance(raw, dict):
            continue
        gid = _text(raw.get("id"), 24)
        if not GROUP_ID_RE.match(gid) or gid in used_ids:
            gid = f"g{index + 1}"
            while gid in used_ids:
                gid += "x"
        used_ids.add(gid)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in raw.get("items") or []:
            item = _item(raw_item)
            if item and item["code"] not in seen and len(items) < MAX_ITEMS:
                seen.add(item["code"])
                items.append(item)
        groups.append({"id": gid, "name": _text(raw.get("name"), 20) or "自選", "items": items})
    settings: dict[str, Any] = {}
    raw_settings = data.get("settings")
    if isinstance(raw_settings, dict):
        for key, value in raw_settings.items():
            if len(settings) >= MAX_SETTINGS or not SETTING_KEY_RE.match(str(key)):
                continue
            if isinstance(value, bool) or (isinstance(value, (int, float)) and value == value):
                settings[str(key)] = value
            elif isinstance(value, str):
                settings[str(key)] = value[:50]
    out = {"groups": groups, "settings": settings}
    if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise WatchlistError("清單太大了（最多 64KB）")
    return out


def _row(connection, key_hash: str):
    return connection.execute(
        "SELECT data_json, version, updated_at FROM watchlists WHERE key_hash = ?", (key_hash,)
    ).fetchone()


def _payload(row) -> dict[str, Any]:
    if not row:
        return {"data": {"groups": [], "settings": {}}, "version": 0, "updatedAt": None}
    return {"data": json.loads(row["data_json"]), "version": int(row["version"]), "updatedAt": row["updated_at"]}


def load(raw_key: Any) -> dict[str, Any]:
    key_hash = _key_hash(normalize_key(raw_key))
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return {"status": "ok", **_payload(_row(connection, key_hash))}


def save(raw_key: Any, data: Any, base_version: Any) -> dict[str, Any]:
    """base_version＝前端讀到的版本（第一次存是 0）；跟資料庫裡的不一樣就回 conflict＋最新的一份，不存。"""
    key_hash = _key_hash(normalize_key(raw_key))
    clean = sanitize(data)
    try:
        base = int(base_version)
    except (TypeError, ValueError) as exc:
        raise WatchlistError("baseVersion 要是數字") from exc
    now = datetime.now(TW_TZ).isoformat(timespec="seconds")
    body = json.dumps(clean, ensure_ascii=False)
    initialize_database()
    with _lock, get_connection() as connection:
        _schema(connection)
        if base == 0:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO watchlists (key_hash, data_json, version, updated_at) VALUES (?, ?, 1, ?)",
                (key_hash, body, now),
            )
        else:
            cursor = connection.execute(
                "UPDATE watchlists SET data_json = ?, version = version + 1, updated_at = ? WHERE key_hash = ? AND version = ?",
                (body, now, key_hash, base),
            )
        if cursor.rowcount != 1:
            return {"status": "conflict", **_payload(_row(connection, key_hash))}
        return {"status": "ok", "data": clean, "version": base + 1, "updatedAt": now}
