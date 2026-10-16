"""每季財報：綜合損益表彙總報表鏡像（今年累計）→ 單季 EPS、營益率、近 4 季 EPS、每季公布期限。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
import financials as module

# 富喬的例子：單季 25Q3 0.54、25Q4 0.83、26Q1 0.79、26Q2 1.45 → 近 4 季 3.61；營益率 26Q1 21.4% → 26Q2 25.7%
CUM = {
    "2025Q2": {"1815": ["富喬", "sii", 100.0, 10.0, 1.00]},
    "2025Q3": {"1815": ["富喬", "sii", 160.0, 20.0, 1.54]},
    "2025Q4": {"1815": ["富喬", "sii", 230.0, 32.0, 2.37]},
    "2026Q1": {"1815": ["富喬", "sii", 80.0, 17.12, 0.79], "2881": ["富邦金", "sii", None, None, 3.0]},
    "2026Q2": {"1815": ["富喬", "sii", 180.0, 42.82, 2.24], "2881": ["富邦金", "sii", None, None, 6.1]},
}


def payload(season: str) -> dict:
    return {"season": season, "updated": "x", "fields": ["name", "market", "revenue", "opIncome", "eps"], "rows": CUM[season]}


class FinancialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.update({"key": None, "singles": None})

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.update({"key": None, "singles": None})

    def load_all(self) -> None:
        for season in CUM:
            got, rows = module.parse_season(payload(season))
            self.assertEqual(got, season)
            module.save_season(season, rows, "t")

    def test_parse_rejects_bad_payload(self) -> None:
        self.assertEqual(module.parse_season({"season": "2026Q5", "rows": {}}), (None, []))
        self.assertEqual(module.parse_season([]), (None, []))

    def test_singles_eps4_and_margin(self) -> None:
        self.load_all()
        singles = module.all_singles()["1815"]
        self.assertEqual([(s["label"], s["eps"]) for s in singles], [("26Q2", 1.45), ("26Q1", 0.79), ("25Q4", 0.83), ("25Q3", 0.54)])   # 25Q2 少了 Q1 累計，算不出單季
        self.assertEqual([s["margin"] for s in singles[:2]], [25.7, 21.4])
        self.assertEqual(module.eps4_of(singles), {"eps4": 3.61, "latest": "2026Q2", "label": "26Q2"})
        self.assertIsNone(module.eps4_of(singles, "2026Q1"))                  # 26Q1 以前只有 3 季
        self.assertEqual(module.eps4_steps(singles), [("2026-08-14", 3.61)])   # Q2 公布期限 8/14 起換
        self.assertEqual(module.published_on("2025Q4"), "2026-03-31")
        bank = module.all_singles()["2881"]
        self.assertEqual([(s["label"], s["eps"], s["margin"]) for s in bank], [("26Q2", 3.1, None), ("26Q1", 3.0, None)])   # 金融業沒有營業收入，不算營益率
        self.assertEqual(module.eps4_map(), {"1815": {"eps4": 3.61, "latest": "2026Q2", "label": "26Q2"}})

    def test_gap_is_not_four_quarters(self) -> None:
        rows = [{"season": s, "label": s[2:], "eps": 1.0} for s in ("2026Q2", "2026Q1", "2025Q3", "2025Q2")]
        self.assertIsNone(module.eps4_of(rows))

    def test_collect_from_mirror(self) -> None:
        def fake(url: str):
            if "fin-index.json" in url:
                return {"updated": "u1", "seasons": ["2026Q2", "2026Q1"]}
            for season in CUM:
                if f"fin-{season}.json" in url:
                    return payload(season)
            raise AssertionError(url)

        out = module.collect(now=datetime(2026, 10, 16, 20, 0, tzinfo=module.TW_TZ), fetcher=fake)
        self.assertEqual(out["seasons"], {"2026Q2": 2, "2026Q1": 2})
        self.assertEqual(out["errors"], [])
        self.assertEqual(module.collector_status()["indexUpdated"], "u1")


if __name__ == "__main__":
    unittest.main()
