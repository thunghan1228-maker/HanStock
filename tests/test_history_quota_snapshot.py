from __future__ import annotations

import unittest

from history_quota import QUOTA_EXHAUSTED, QUOTA_RESERVED, HistoryQuotaGate


class HistoryQuotaSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_usage_numbers_and_block_state(self) -> None:
        class FakeApi:
            def usage(self):
                return {"connections": 1, "bytes": 10, "limit_bytes": 100, "remaining_bytes": 90}

        snapshot = HistoryQuotaGate().snapshot(FakeApi())

        self.assertFalse(snapshot["blocked"])
        self.assertIsNone(snapshot["error"])
        self.assertEqual(snapshot["usage"]["remaining_bytes"], 90)
        self.assertEqual(snapshot["usage"]["limit_bytes"], 100)
        self.assertIsNotNone(snapshot["probedAt"])

    def test_snapshot_flags_exhausted_quota(self) -> None:
        class ExhaustedApi:
            def usage(self):
                return {"connections": 1, "bytes": 100, "limit_bytes": 100, "remaining_bytes": 0}

        snapshot = HistoryQuotaGate().snapshot(ExhaustedApi())

        self.assertTrue(snapshot["blocked"])
        self.assertEqual(snapshot["error"], QUOTA_EXHAUSTED)
        self.assertEqual(snapshot["usage"]["remaining_bytes"], 0)

    def test_snapshot_without_usage_capability_is_empty_but_not_blocked(self) -> None:
        snapshot = HistoryQuotaGate().snapshot(object())

        self.assertFalse(snapshot["blocked"])
        self.assertIsNone(snapshot["usage"])
        self.assertIsNone(snapshot["probedAt"])

    def test_check_background_pauses_below_reserve_but_foreground_check_still_passes(self) -> None:
        # 2026-09-22 實際發生：全族群主力回補早盤就把 500MB 燒光，櫃買指數/個股
        # K 線歷史整天拿不到。背景回補要在剩餘額度低於保留值時就停，前景需求
        # (開圖、櫃買指數 bootstrap)則可以繼續用到 0。
        class LowApi:
            def usage(self):
                return {"connections": 1, "bytes": 420_000_000, "limit_bytes": 500_000_000, "remaining_bytes": 80_000_000}

        gate = HistoryQuotaGate()
        background = gate.check_background(LowApi(), reserve_bytes=150_000_000)

        self.assertIn(QUOTA_RESERVED, background)
        self.assertIsNone(gate.check(LowApi()))

    def test_check_background_allows_when_remaining_is_above_reserve(self) -> None:
        class HealthyApi:
            def usage(self):
                return {"connections": 1, "bytes": 100_000_000, "limit_bytes": 500_000_000, "remaining_bytes": 400_000_000}

        self.assertIsNone(HistoryQuotaGate().check_background(HealthyApi(), reserve_bytes=150_000_000))
        self.assertIsNone(HistoryQuotaGate().check_background(object(), reserve_bytes=150_000_000))

    def test_check_background_reports_exhaustion_first(self) -> None:
        class ExhaustedApi:
            def usage(self):
                return {"connections": 1, "bytes": 528_000_000, "limit_bytes": 524_288_000, "remaining_bytes": -4_000_000}

        self.assertEqual(HistoryQuotaGate().check_background(ExhaustedApi(), reserve_bytes=150_000_000), QUOTA_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
