"""今日功課的盤中紀錄：每 5 分鐘一份刀劍空名單（網站前端算好送來），回放與「刀功課廣播」用。

刀劍空(32) 的規則在 tw-groups 前端（race333Model），後端沒有一份一樣的；所以由開著網站的瀏覽器在每個 5 分鐘時段
送一份上來，先送到的算數（同一時段不覆蓋），誰的網站開著都能幫大家記到。為了不讓人亂塞資料：
只收今天、只收「現在這個時段」（前後 10 分鐘內），欄位逐一檢查、檔數有上限。保留 30 天。
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

import database
from otc_index import TW_TZ

KEEP_DAYS = 30
MAX_ROWS = 300
SLOT_WINDOW_MINUTES = 10
ZONES = {"upper", "middle", "lower", "harvest"}
CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
SLOT_RE = re.compile(r"^(0[9]|1[0-3]):[0-5][05]$")
_table_lock = threading.Lock()
_table_ready_path: Optional[str] = None


def _ensure_table() -> None:
    global _table_ready_path
    path = str(database.DATABASE_PATH.resolve())
    if _table_ready_path == path:
        return
    with _table_lock:
        if _table_ready_path == path:
            return
        with database.get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS homework_snapshots (
                    trade_date TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (trade_date, slot)
                )
                """
            )
        _table_ready_path = path


def valid_slot(slot: str) -> bool:
    if not SLOT_RE.match(slot or ""):
        return False
    return "09:05" <= slot <= "13:30"


def _num(value: Any, digits: int = 2) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) > 1e13:
        return None
    return round(number, digits)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_row(raw: Any, *, zone_required: bool) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    code = _text(raw.get("code"), 6).upper()
    if not CODE_RE.match(code):
        return None
    row: dict[str, Any] = {"code": code, "name": _text(raw.get("name"), 16)}
    zone = _text(raw.get("zone"), 10)
    if zone_required:
        if zone not in ZONES:
            return None
        row["zone"] = zone
    row["g"] = _text(raw.get("g"), 16)
    rank = _num(raw.get("gr"), 0)
    row["gr"] = int(rank) if rank is not None and 0 < rank < 200 else None
    row["mk"] = _text(raw.get("mk"), 4)
    for key, digits in (("pct", 2), ("price", 2), ("ma", 0), ("big", 1), ("avg", 0), ("hi", 2)):
        row[key] = _num(raw.get(key), digits)
    return row


def clean_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("格式不對")
    rows = [row for row in (_clean_row(r, zone_required=True) for r in (raw.get("rows") or [])[:MAX_ROWS]) if row]
    k12 = [row for row in (_clean_row(r, zone_required=False) for r in (raw.get("k12") or [])[:MAX_ROWS]) if row]
    total = _num(raw.get("total"), 0)
    return {
        "rows": rows,
        "k12": k12,
        "total": int(total) if total and 0 < total < 200 else None,
        "otcLabel": _text(raw.get("otcLabel"), 8),
        "otcPct": _num(raw.get("otcPct")),
        "quoteTime": _text(raw.get("quoteTime"), 8),
    }


def save_snapshot(raw: Any, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """前端送來的一份紀錄；不是今天或不是現在這個時段的不收，同一時段已經有的不覆蓋。"""
    now = now or datetime.now(TW_TZ)
    if not isinstance(raw, dict):
        raise ValueError("格式不對")
    date, slot = _text(raw.get("date"), 10), _text(raw.get("slot"), 5)
    if date != now.strftime("%Y-%m-%d"):
        raise ValueError("只收今天的紀錄")
    if not valid_slot(slot):
        raise ValueError("時段要是 09:05～13:30 的 5 分鐘整點")
    slot_time = now.replace(hour=int(slot[:2]), minute=int(slot[3:]), second=0, microsecond=0)
    if not (slot_time - timedelta(minutes=1) <= now <= slot_time + timedelta(minutes=SLOT_WINDOW_MINUTES)):
        raise ValueError("只收現在這個時段的紀錄")
    payload = clean_payload(raw)
    _ensure_table()
    with database.get_connection() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO homework_snapshots (trade_date, slot, payload, created_at) VALUES (?, ?, ?, ?)",
            (date, slot, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now.isoformat(timespec="seconds")),
        )
        saved = cursor.rowcount > 0
        if saved:
            cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            connection.execute("DELETE FROM homework_snapshots WHERE trade_date < ?", (cutoff,))
    return {"status": "ok", "saved": saved, "date": date, "slot": slot}


def load_snapshots(date: Optional[str] = None) -> dict[str, Any]:
    """某一天全部時段；沒給日期＝今天，今天還沒有就給最近有紀錄的那天。"""
    _ensure_table()
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    with database.get_connection() as connection:
        dates = [row["trade_date"] for row in connection.execute(
            "SELECT DISTINCT trade_date FROM homework_snapshots ORDER BY trade_date DESC LIMIT 20"
        ).fetchall()]
        target = date or (today if today in dates else (dates[0] if dates else today))
        rows = connection.execute(
            "SELECT slot, payload FROM homework_snapshots WHERE trade_date = ? ORDER BY slot", (target,)
        ).fetchall()
    return {
        "status": "ok",
        "date": target,
        "dates": dates,
        "slots": {row["slot"]: json.loads(row["payload"]) for row in rows},
    }
