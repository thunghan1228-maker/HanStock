from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import database
import homework_snapshots
from homework_snapshots import load_snapshots, save_snapshot
from otc_index import TW_TZ

NOW = datetime(2026, 10, 19, 9, 31, 20, tzinfo=TW_TZ)


@pytest.fixture(autouse=True)
def temp_db():
    with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DATABASE_PATH", Path(tmp) / "t.db"):
        homework_snapshots._table_ready_path = None
        yield
        homework_snapshots._table_ready_path = None


def _payload(**extra):
    return {
        "date": "2026-10-19", "slot": "09:30", "total": 66, "otcLabel": "強多", "otcPct": 0.92, "quoteTime": "09:31:05",
        "rows": [
            {"code": "2478", "name": "大毅", "zone": "lower", "g": "被動元件", "gr": 41, "mk": "🔪", "pct": 0.7, "price": 150.5, "ma": 11, "big": -23.2, "avg": 1851023933, "hi": 4.2},
            {"code": "bad!", "zone": "lower"},
            {"code": "1303", "name": "南亞", "zone": "nowhere"},
        ],
        "k12": [{"code": "2484", "name": "希華", "g": "石英", "gr": 58, "pct": 0.0}],
        **extra,
    }


def test_saves_current_slot_once_and_cleans_rows():
    first = save_snapshot(_payload(), now=NOW)
    again = save_snapshot(_payload(rows=[]), now=NOW)
    assert first["saved"] is True and again["saved"] is False
    data = load_snapshots("2026-10-19")
    snap = data["slots"]["09:30"]
    assert [r["code"] for r in snap["rows"]] == ["2478"]
    assert snap["rows"][0]["big"] == -23.2 and snap["rows"][0]["gr"] == 41
    assert snap["k12"][0]["code"] == "2484"
    assert data["dates"] == ["2026-10-19"]


def test_rejects_other_days_and_slots_far_from_now():
    with pytest.raises(ValueError):
        save_snapshot(_payload(date="2026-10-18"), now=NOW)
    with pytest.raises(ValueError):
        save_snapshot(_payload(slot="10:00"), now=NOW)   # 還沒到
    with pytest.raises(ValueError):
        save_snapshot(_payload(slot="09:15"), now=NOW)   # 太久以前
    with pytest.raises(ValueError):
        save_snapshot(_payload(slot="09:32"), now=NOW)   # 不是 5 分鐘整點
    with pytest.raises(ValueError):
        save_snapshot(_payload(slot="08:55"), now=NOW.replace(hour=8, minute=56))


def test_load_without_date_falls_back_to_latest_day():
    save_snapshot(_payload(), now=NOW)
    with patch("homework_snapshots.datetime") as fake:
        fake.now.return_value = datetime(2026, 10, 20, 8, 0, tzinfo=TW_TZ)
        data = load_snapshots()
    assert data["date"] == "2026-10-19"
    assert list(data["slots"]) == ["09:30"]
