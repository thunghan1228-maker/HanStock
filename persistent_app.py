"""HanStock 正式 app：在既有行情 app 上增加 Railway SQLite 持久化端點。"""

from __future__ import annotations

import hmac
import os
from datetime import datetime
from typing import Any

from fastapi import Header, HTTPException, Query
from pydantic import BaseModel, Field

from group_strength_collector import start_group_strength_collector
from group_strength_store import (
    group_strength_storage_status,
    load_group_strength_history,
    save_group_strength_snapshot,
)
from hanstock_app import app


class GroupStrengthSnapshotBody(BaseModel):
    tradeDate: str = Field(min_length=10, max_length=10)
    bucketTs: int = Field(gt=0)
    ranks: dict[str, int]


def _validate_trade_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    return value


def _expected_hub_secret() -> str:
    return (
        os.getenv("HANSTOCK_HUB_KEY", "").strip()
        or os.getenv("HANSTOCK_SYNC_TOKEN", "").strip()
    )


def _require_hub_auth(x_hub_key: str | None) -> None:
    expected = _expected_hub_secret()
    if not expected:
        raise HTTPException(status_code=503, detail="Hub 持久化寫入金鑰尚未設定")
    supplied = (x_hub_key or "").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Hub key 驗證失敗")


@app.on_event("startup")
def start_persistence_workers() -> None:
    # 由 Hub 自己讀公開的族群排名並寫入 SQLite，不需要 Vercel 持有寫入金鑰。
    start_group_strength_collector()


@app.get("/api/hub/persistence/status")
def get_persistence_status() -> dict[str, Any]:
    data = group_strength_storage_status()
    data["collectorEnabled"] = os.getenv(
        "HANSTOCK_GROUP_STRENGTH_COLLECTOR_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    return {"status": "ok", "data": data}


@app.get("/api/hub/group-strength/history")
def get_group_strength_history(
    trade_date: str = Query(..., min_length=10, max_length=10),
) -> dict[str, Any]:
    # 排名歷史本身就是公開網站資料；只開放唯讀 GET，寫入仍需金鑰。
    date = _validate_trade_date(trade_date)
    snapshots = load_group_strength_history(date)
    return {
        "status": "ok",
        "tradeDate": date,
        "count": len(snapshots),
        "snapshots": snapshots,
    }


@app.post("/api/hub/group-strength/history")
def post_group_strength_history(
    body: GroupStrengthSnapshotBody,
    x_hub_key: str | None = Header(default=None, alias="X-Hub-Key"),
) -> dict[str, Any]:
    _require_hub_auth(x_hub_key)
    date = _validate_trade_date(body.tradeDate)
    if not body.ranks:
        raise HTTPException(status_code=422, detail="ranks 不可為空")
    if len(body.ranks) > 200:
        raise HTTPException(status_code=422, detail="單次最多 200 個族群名次")
    count = save_group_strength_snapshot(date, body.bucketTs, body.ranks)
    return {
        "status": "ok",
        "tradeDate": date,
        "bucketTs": body.bucketTs,
        "saved": len(body.ranks),
        "snapshotCount": count,
    }
