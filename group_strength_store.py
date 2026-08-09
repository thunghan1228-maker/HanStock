"""族群強弱 5 分鐘排名的 Railway SQLite 持久化層。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from database import DATABASE_PATH, get_connection
from paths import DATA_DIR

MAX_SNAPSHOTS_PER_DAY = 84


def _ensure_table() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS group_strength_snapshots (
                trade_date TEXT NOT NULL,
                bucket_ts INTEGER NOT NULL,
                ranks_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (trade_date, bucket_ts)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_group_strength_trade_date_ts
            ON group_strength_snapshots (trade_date, bucket_ts)
            """
        )


def load_group_strength_history(trade_date: str) -> list[dict[str, Any]]:
    _ensure_table()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT bucket_ts, ranks_json
            FROM group_strength_snapshots
            WHERE trade_date = ?
            ORDER BY bucket_ts ASC
            LIMIT ?
            """,
            (trade_date, MAX_SNAPSHOTS_PER_DAY),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            ranks = json.loads(row["ranks_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(ranks, dict) or not ranks:
            continue
        clean: dict[str, int] = {}
        for group, rank in ranks.items():
            if isinstance(group, str) and isinstance(rank, int) and rank > 0:
                clean[group] = rank
        if clean:
            result.append({"bucketTs": int(row["bucket_ts"]), "ranks": clean})
    return result


def save_group_strength_snapshot(
    trade_date: str,
    bucket_ts: int,
    ranks: dict[str, int],
) -> int:
    clean = {
        str(group): int(rank)
        for group, rank in ranks.items()
        if str(group).strip() and isinstance(rank, int) and 0 < int(rank) <= 500
    }
    if not clean:
        return 0

    _ensure_table()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO group_strength_snapshots (
                trade_date, bucket_ts, ranks_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(trade_date, bucket_ts) DO UPDATE SET
                ranks_json = excluded.ranks_json,
                updated_at = excluded.updated_at
            """,
            (trade_date, int(bucket_ts), payload, now),
        )
        # 每個交易日只保留最近 84 個 5 分鐘快照。
        connection.execute(
            """
            DELETE FROM group_strength_snapshots
            WHERE trade_date = ?
              AND bucket_ts NOT IN (
                SELECT bucket_ts
                FROM group_strength_snapshots
                WHERE trade_date = ?
                ORDER BY bucket_ts DESC
                LIMIT ?
              )
            """,
            (trade_date, trade_date, MAX_SNAPSHOTS_PER_DAY),
        )
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM group_strength_snapshots WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()["n"]
    return int(count)


def group_strength_storage_status() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_table()
    with get_connection() as connection:
        total = int(
            connection.execute("SELECT COUNT(*) AS n FROM group_strength_snapshots").fetchone()["n"]
        )

    data_dir_env = os.getenv("HANSTOCK_DATA_DIR", "").strip()
    hub_key_set = bool(os.getenv("HANSTOCK_HUB_KEY", "").strip())
    sync_token_set = bool(os.getenv("HANSTOCK_SYNC_TOKEN", "").strip())
    writable = os.access(DATA_DIR, os.W_OK)
    return {
        "dataDir": str(DATA_DIR),
        "dataDirEnvSet": bool(data_dir_env),
        "recommendedRailwayVolumePath": "/data",
        "databasePath": str(DATABASE_PATH),
        "databaseExists": DATABASE_PATH.exists(),
        "databaseSizeBytes": DATABASE_PATH.stat().st_size if DATABASE_PATH.exists() else 0,
        "writable": writable,
        "hubKeyConfigured": hub_key_set,
        "syncTokenConfigured": sync_token_set,
        "authConfigured": hub_key_set or sync_token_set,
        "snapshotCount": total,
        # HANSTOCK_DATA_DIR 有明確設定時，代表部署端刻意指定持久化目錄；
        # Railway 正式建議值為 /data 並掛 Volume。
        "persistentCandidate": bool(data_dir_env) and writable,
    }
