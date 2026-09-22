from __future__ import annotations

import unittest

from history_quota import QUOTA_EXHAUSTED, HistoryQuotaGate


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


if __name__ == "__main__":
    unittest.main()
