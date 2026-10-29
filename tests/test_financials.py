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


# 季報 EPS 榜：毛利（gross）、淨利（net）也進單季；Q3 公布期間只有幾家
FIELDS7 = ["name", "market", "revenue", "opIncome", "eps", "gross", "net"]
CUM7 = {
    "2025Q2": {"1815": ["富喬", "sii", 100.0, 10.0, 1.00, 30.0, 8.0], "2002": ["乙公司", "sii", 100.0, 10.0, 1.0, 40.0, 8.0], "3000": ["丙公司", "otc", 100.0, -2.0, -0.2, 10.0, -2.0]},
    "2025Q3": {"1815": ["富喬", "sii", 160.0, 20.0, 1.54, 50.0, 13.0], "2002": ["乙公司", "sii", 160.0, 20.0, 1.6, 64.0, 13.0], "3000": ["丙公司", "otc", 160.0, -5.0, -0.5, 16.0, -5.0]},
    "2026Q1": {"1815": ["富喬", "sii", 80.0, 17.12, 0.79, 24.0, 12.0], "2002": ["乙公司", "sii", 80.0, 8.0, 0.8, 32.0, 6.0], "3000": ["丙公司", "otc", 80.0, -1.0, -0.1, 8.0, -1.0], "4000": ["丁公司", "sii", 50.0, 5.0, 0.5, 15.0, 4.0]},
    "2026Q2": {"1815": ["富喬", "sii", 180.0, 42.82, 2.24, 56.0, 30.0], "2002": ["乙公司", "sii", 180.0, 18.0, 1.7, 72.0, 14.0], "3000": ["丙公司", "otc", 160.0, 4.0, 0.3, 20.0, 3.0],
               "4000": ["丁公司", "sii", 100.0, 10.0, 1.0, 30.0, 8.0]},
    "2026Q3": {"1815": ["富喬", "sii", 300.0, 90.0, 4.0, 100.0, 64.0], "2002": ["乙公司", "sii", 290.0, 20.0, 1.2, 100.0, 12.0], "3000": ["丙公司", "otc", 280.0, 12.0, 0.7, 36.0, 9.0]},
}
SEEN = {"1815": "2026-10-28", "2002": "2026-10-27", "3000": "2026-10-27"}      # 2002、3000 是第一次存檔（baseline）那批


def payload7(season: str) -> dict:
    out = {"season": season, "updated": "x", "fields": FIELDS7, "rows": CUM7[season]}
    if season == "2026Q3":
        out.update({"baseline": "2026-10-27", "seen": SEEN})
    return out


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

    def load7(self) -> None:
        for season in CUM7:
            got, rows = module.parse_season(payload7(season))
            module.save_season(season, rows, "t")

    def test_parse_seen_and_known(self) -> None:
        _, rows = module.parse_season(payload7("2026Q3"))
        by = {r["code"]: r for r in rows}
        self.assertEqual((by["1815"]["firstSeen"], by["1815"]["known"]), ("2026-10-28", True))
        self.assertEqual((by["2002"]["firstSeen"], by["2002"]["known"]), ("2026-10-27", False))     # 跟 baseline 同一天：公布日不確定
        self.assertEqual(by["1815"]["gross"], 100.0)

    def test_singles_margins(self) -> None:
        self.load7()
        s = {x["season"]: x for x in module.all_singles()["1815"]}
        self.assertEqual((s["2026Q3"]["eps"], s["2026Q3"]["gm"], s["2026Q3"]["margin"], s["2026Q3"]["nm"]), (1.76, 36.7, 39.3, 28.3))
        self.assertEqual((s["2026Q2"]["gm"], s["2026Q2"]["margin"], s["2026Q2"]["nm"]), (32.0, 25.7, 18.0))
        self.assertEqual((s["2026Q1"]["gm"], s["2026Q1"]["nm"]), (30.0, 15.0))                       # Q1 就是累計本身

    def test_eps_board(self) -> None:
        self.load7()
        with database.get_connection() as c:     # 富喬 10/28 公布：隔日 10/29 收 105、10/28 收 100 → +5%
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [("1815", "2026-10-27", 99, 101, 98, 99, 1000), ("1815", "2026-10-28", 100, 101, 99, 100, 1000), ("1815", "2026-10-29", 104, 106, 103, 105, 2000)])
        out = module.eps_board()
        self.assertEqual((out["season"], out["label"], out["published"], out["latestDay"]), ("2026Q3", "2026 Q3", 3, "2026-10-28"))     # 丁公司還沒公布 Q3
        by = {r[0]: dict(zip(out["fields"], r)) for r in out["rows"]}
        a, b, c = by["1815"], by["2002"], by["3000"]
        self.assertEqual((a["eps"], a["last"], a["yoy"], a["tier"]), (1.76, 0.54, 225.9, "t100"))
        self.assertEqual((a["gm"], a["gmPrev"], a["gmDelta"], a["rocket"], a["turtle"], a["triple"]), (36.7, 32.0, 4.7, True, False, True))
        self.assertEqual((a["announce"], a["known"], a["next"], a["close"]), ("2026-10-28", True, 5.0, 105.0))
        self.assertEqual((b["eps"], b["tier"], b["turtle"], b["known"], b["next"]), (-0.5, "tneg", True, False, None))                 # 本季虧損、毛利率少 3 個百分點
        self.assertEqual((c["eps"], c["last"], c["yoy"], c["tier"]), (0.4, -0.3, None, "t30"))                                        # 去年虧今年賺＝轉盈
        self.assertEqual([r[0] for r in out["rows"]], ["1815", "2002", "3000"][:1] + [r[0] for r in out["rows"]][1:])               # 年增高的在前
        self.assertEqual(out["rows"][0][0], "1815")
        self.assertEqual(module.eps_board("2026Q2")["published"], 4)
        with self.assertRaises(LookupError):
            module.eps_board("2025Q1")

    def test_eps_board_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        self.load7()
        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/eps-board")
        self.assertEqual((r.status_code, r.json()["season"], r.json()["published"]), (200, "2026Q3", 3), r.text[:300])
        self.assertEqual(client.get("/api/hub/eps-board", params={"season": "2026Q2"}).json()["published"], 4)
        self.assertEqual(client.get("/api/hub/eps-board", params={"season": "2020Q1"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/eps-board", params={"season": "Q3"}).status_code, 422)

    def test_save_keeps_first_seen(self) -> None:
        _, rows = module.parse_season(payload7("2026Q3"))
        module.save_season("2026Q3", rows, "t1")
        later = payload7("2026Q3")
        later["seen"] = {**SEEN, "1815": "2026-10-30"}      # 之後鏡像又更新：第一次抓到的日期不能被改
        _, rows2 = module.parse_season(later)
        module.save_season("2026Q3", rows2, "t2")
        with database.get_connection() as c:
            r = c.execute("SELECT first_seen, known FROM fin_season_cum WHERE code = '1815' AND season = '2026Q3'").fetchone()
        self.assertEqual((r["first_seen"], r["known"]), ("2026-10-28", 1))

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
