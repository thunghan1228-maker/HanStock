"""盤中行情看門狗：候選名單還沒好（要靠 Tick 算族群排名）但開盤後一筆 Tick 都沒有，也要重連（2026-11-19 整個早上沒重連的死結）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import intraday_large_order_collector as module
import stock_futures_service


class FakeMonitor:
    def __init__(self) -> None:
        self.status: dict = {}

    def update_status(self, **kw) -> None:
        self.status.update(kw)


class FakeService:
    def __init__(self, age, subs=445) -> None:
        self.age, self.subs = age, subs

    def get_stock_health(self) -> dict:
        return {"stock_quote_age_seconds": self.age, "active_subscription_count": self.subs, "cached_quote_count": 0}


@pytest.fixture(autouse=True)
def reset_state():
    module._prepared_at = None
    module._last_recovery_at = float("-inf")
    yield
    module._prepared_at = None
    module._last_recovery_at = float("-inf")


def run(service, status, *, expected=True, session=True, elapsed=True):
    """跑一次看門狗；elapsed＝假裝已經等過 STALE_SECONDS（第一次進來先記時間，第二次才判斷）。"""
    monitor = FakeMonitor()
    calls = []
    quote = SimpleNamespace(trigger_stale_stock_recovery=lambda svc, reason: calls.append(reason) or True)
    with patch.object(module, "get_intraday_large_order_monitor", return_value=monitor), \
            patch.object(module, "_ticks_expected", return_value=expected), \
            patch.object(module, "_market_session_open", return_value=session), \
            patch.object(stock_futures_service, "get_stock_futures_quote_service", return_value=quote):
        module._recover_if_stock_ticks_stale(service, status)
        if elapsed and module._prepared_at is not None:
            module._prepared_at -= module.STALE_SECONDS + 1
            module._recover_if_stock_ticks_stale(service, status)
    return monitor.status, calls


def test_no_ticks_while_candidates_wait_triggers_recovery():
    status, calls = run(FakeService(age=None), {"prepared": False, "reason": "waiting_group_snapshot"})
    assert len(calls) == 1 and "無資料" in calls[0]
    assert status["watchdogState"] == "recovering"


def test_ticks_flowing_while_candidates_wait_does_not_recover():
    status, calls = run(FakeService(age=3.0), {"prepared": False})
    assert calls == []
    assert status["watchdogState"] == "waiting_candidates"


def test_before_ticks_expected_or_no_subscription_keeps_waiting():
    status, calls = run(FakeService(age=None), {"prepared": False}, expected=False)      # 09:02 以前
    assert calls == [] and status["watchdogState"] == "waiting_candidates"
    status, calls = run(FakeService(age=None, subs=0), {"prepared": False})              # 還沒訂閱任何股票
    assert calls == [] and status["watchdogState"] == "waiting_candidates"


def test_prepared_path_unchanged():
    status, calls = run(FakeService(age=200.0), {"prepared": True})
    assert len(calls) == 1
    status, calls = run(FakeService(age=10.0), {"prepared": True})
    assert calls == [] and status["watchdogState"] == "healthy"


def test_grace_period_before_first_recovery():
    status, calls = run(FakeService(age=None), {"prepared": False}, elapsed=False)       # 剛開始等，還沒滿 90 秒
    assert calls == [] and status["watchdogState"] == "stale_waiting_candidates"
