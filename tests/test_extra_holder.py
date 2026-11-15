"""族群表以外股票的收盤後大戶力（extra_holder）：要補哪一天、公式跟大戶力排行一致、背景佇列只算一次、族群股不算、端點。"""

from __future__ import annotations

import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import database
import extra_holder
import persistent_app
from otc_index import TW_TZ


@pytest.fixture(autouse=True)
def temp_db():
    with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DATABASE_PATH", Path(tmp) / "t.db"):
        extra_holder._table_ready_path = None
        extra_holder._queue.clear()
        extra_holder._queued.clear()
        yield
        deadline = time.time() + 5
        while extra_holder._worker is not None and time.time() < deadline:
            time.sleep(0.02)
        extra_holder._table_ready_path = None


def fake_service():
    return SimpleNamespace(api=SimpleNamespace(), state=SimpleNamespace(logged_in=True))


METRICS = {   # 兩分鐘：大戶買 3,000 萬、賣 1,000 萬 → 淨 +2,000 萬；當日成交額 2 億
    1: {"big_buy_amount": 20_000_000, "big_sell_amount": 4_000_000, "total_amount": 120_000_000},
    2: {"big_buy_amount": 10_000_000, "big_sell_amount": 6_000_000, "total_amount": 80_000_000},
}


def test_target_date_after_close_is_today_otherwise_previous():
    with patch.object(extra_holder, "_is_trading_day", return_value=True), \
            patch("main_force_store.latest_trade_date_with_data", return_value="2026-11-13"):
        assert extra_holder.target_date(datetime(2026, 11, 16, 13, 40, tzinfo=TW_TZ)) == "2026-11-16"
        assert extra_holder.target_date(datetime(2026, 11, 16, 10, 0, tzinfo=TW_TZ)) == "2026-11-13"   # 盤中：今天的逐筆還沒收完
    with patch.object(extra_holder, "_is_trading_day", return_value=False), \
            patch("main_force_store.latest_trade_date_with_data", return_value="2026-11-13"):
        assert extra_holder.target_date(datetime(2026, 11, 15, 20, 0, tzinfo=TW_TZ)) == "2026-11-13"   # 週末


def test_compute_one_uses_teacher_formula():
    with patch("stock_bar_bootstrap._resolve_stock_contract", return_value=object()), \
            patch("stock_bar_bootstrap._fetch_historical_ticks", return_value=[]), \
            patch("stock_bar_bootstrap._historical_tick_metrics", return_value=METRICS), \
            patch("big_holder.avg_turnover", return_value=400_000_000):
        out = extra_holder.compute_one(fake_service(), "3374", "2026-11-13")
    assert out["net"] == 20_000_000 and out["total"] == 200_000_000
    assert out["pct"] == 5.0                                  # 2,000 萬 ÷ 近 5 日均成交值 4 億
    with patch("stock_bar_bootstrap._resolve_stock_contract", return_value=None):
        assert extra_holder.compute_one(fake_service(), "9999", "2026-11-13") == {"error": "找不到股票合約"}


def test_payload_queues_missing_once_skips_universe_and_returns_saved():
    calls = []

    def fake_compute(service, code, day):
        calls.append(code)
        return {"net": 1.0, "avg": 10.0, "total": 5.0, "pct": 12.3} if code == "3374" else {"error": "那天沒有逐筆成交"}

    now = datetime(2026, 11, 15, 20, 0, tzinfo=TW_TZ)
    with patch.object(extra_holder, "target_date", return_value="2026-11-13"), patch.object(extra_holder, "compute_one", side_effect=fake_compute), \
            patch.object(extra_holder, "PAUSE_SECONDS", 0):
        first = extra_holder.payload(["3374", "2330", "6925"], now=now, universe={"2330"}, service_getter=fake_service, quota_check=lambda api: None)
        assert first["pending"] == 2 and first["items"] == {}
        deadline = time.time() + 5
        while extra_holder._worker is not None and time.time() < deadline:
            time.sleep(0.02)
        again = extra_holder.payload(["3374", "2330", "6925"], now=now, universe={"2330"}, service_getter=fake_service, quota_check=lambda api: None)
    assert sorted(calls) == ["3374", "6925"]                  # 族群股 2330 不算；算過的不再算
    assert again["pending"] == 0
    assert again["items"]["3374"]["pct"] == 12.3
    assert again["failed"] == {"6925": "那天沒有逐筆成交"}


def test_quota_or_login_blocker_pauses_without_saving():
    with patch.object(extra_holder, "target_date", return_value="2026-11-13"), patch.object(extra_holder, "compute_one") as compute:
        extra_holder.payload(["3374"], universe=set(), service_getter=fake_service, quota_check=lambda api: "history_quota_reserved")
        deadline = time.time() + 5
        while extra_holder._worker is not None and time.time() < deadline:
            time.sleep(0.02)
        compute.assert_not_called()
        assert extra_holder.load("2026-11-13", ["3374"]) == {}   # 沒記成錯誤，下次有人來問會再排
        assert extra_holder._status["state"] == "paused"


def test_too_many_codes_rejected_and_endpoint():
    with pytest.raises(ValueError):
        extra_holder.payload([str(1000 + i) for i in range(extra_holder.MAX_CODES + 1)], universe=set())
    with patch.object(extra_holder, "target_date", return_value=None):
        res = TestClient(persistent_app.app).get("/api/hub/extra-holder?codes=3374")
    assert res.status_code == 200 and res.json()["items"] == {}
