import unittest
from datetime import datetime, timedelta, timezone

from group_rank_checkpoints import compute_group_rank_checkpoints

TW = timezone(timedelta(hours=8))


class GroupRankCheckpointTests(unittest.TestCase):
    def test_ranks_each_half_hour_from_stored_5m_closes(self):
        groups = {"甲": ["1111", "2222"], "乙": ["3333"], "丙": ["4444"]}
        prev = {"1111": 100.0, "2222": 50.0, "3333": 10.0, "4444": 20.0}
        bars = {
            # 09:00 開盤那根：甲 +1%／+2%、乙 +5%、丙 -1%
            "1111": [("09:00", 101.0), ("09:25", 110.0), ("09:55", 100.0)],
            "2222": [("09:00", 51.0), ("09:25", 55.0), ("09:55", 50.0)],
            "3333": [("09:00", 10.5), ("09:25", 10.2), ("09:55", 10.3)],
            "4444": [("09:00", 19.8), ("09:25", 20.0), ("09:55", 21.0)],
        }
        out = compute_group_rank_checkpoints(
            "2026-10-16", now=datetime(2026, 10, 16, 10, 2, tzinfo=TW), groups=groups, prev_closes=prev,
            closes_loader=lambda date, codes: bars,
        )
        self.assertEqual(out["times"], ["09:05", "09:30", "10:00"])   # 10:30 還沒到
        self.assertEqual(out["groups"]["乙"]["ranks"], [1, 2, 2])
        self.assertEqual(out["groups"]["甲"]["ranks"], [2, 1, 3])
        self.assertEqual(out["groups"]["丙"]["ranks"], [3, 3, 1])
        self.assertAlmostEqual(out["groups"]["甲"]["avgs"][1], 10.0)

    def test_checkpoint_uses_last_bar_that_started_before_it(self):
        groups = {"甲": ["1111"], "乙": ["2222"]}
        prev = {"1111": 100.0, "2222": 100.0}
        bars = {"1111": [("09:00", 101.0), ("09:30", 150.0)], "2222": [("09:00", 102.0), ("09:25", 103.0)]}
        out = compute_group_rank_checkpoints(
            "2026-10-16", now=datetime(2026, 10, 16, 9, 40, tzinfo=TW), groups=groups, prev_closes=prev,
            closes_loader=lambda date, codes: bars,
        )
        # 09:30 的點只看 09:25 以前開始的 K 棒：甲還是 101（09:30 那根不算）
        self.assertEqual(out["groups"]["甲"]["avgs"], [1.0, 1.0])
        self.assertEqual(out["groups"]["乙"]["ranks"], [1, 1])

    def test_no_bars_yet_gives_empty_times(self):
        out = compute_group_rank_checkpoints(
            "2026-10-16", now=datetime(2026, 10, 16, 9, 2, tzinfo=TW), groups={"甲": ["1111"]}, prev_closes={"1111": 10.0},
            closes_loader=lambda date, codes: {},
        )
        self.assertEqual(out["times"], [])
        self.assertEqual(out["groups"]["甲"]["ranks"], [])


if __name__ == "__main__":
    unittest.main()
