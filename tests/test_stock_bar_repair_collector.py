from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import stock_bar_repair_collector as collector
from history_quota import QUOTA_RESERVED, HistoryQuotaGate


def _service(remaining_bytes: int):
    api = SimpleNamespace(usage=lambda: {"connections": 1, "bytes": 1, "limit_bytes": 500_000_000, "remaining_bytes": remaining_bytes})
    return SimpleNamespace(api=api, state=SimpleNamespace(logged_in=True))


class BackfillQuotaGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repair = patch.object(collector, "repair_recent_stock_bars_once", return_value={"checkedCount": 0, "failedCount": 0})
        self.repair.start()
        self.process = patch.object(collector, "process_main_force_backfill_job", return_value=None)
        self.process_mock = self.process.start()
        self.gate = patch.object(collector, "history_quota", HistoryQuotaGate())
        self.gate.start()
        self.reserve = patch.object(collector, "BACKFILL_RESERVE_BYTES", 150_000_000)
        self.reserve.start()

    def tearDown(self) -> None:
        for p in (self.repair, self.process, self.gate, self.reserve):
            p.stop()
        collector._pause_reason = None

    def test_backfill_pauses_when_remaining_quota_is_below_reserve(self) -> None:
        # 2026-09-22 實際發生：全族群主力回補早盤就把 Shioaji 500MB 歷史額度燒光，
        # 櫃買指數/個股K線歷史整天拿不到資料。低於保留值就不能再領工作。
        collector.collect_once(service=_service(remaining_bytes=80_000_000))

        self.process_mock.assert_not_called()
        self.assertIn(QUOTA_RESERVED, collector.backfill_pause_reason() or "")

    def test_backfill_runs_when_remaining_quota_is_above_reserve(self) -> None:
        collector.collect_once(service=_service(remaining_bytes=400_000_000))

        self.process_mock.assert_called()
        self.assertIsNone(collector.backfill_pause_reason())


if __name__ == "__main__":
    unittest.main()
