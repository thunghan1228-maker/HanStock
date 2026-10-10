"""營收成長榜：觀測站彙總表鏡像解析、公布日（第一次抓到的時間）、公布隔日漲跌、加速／放緩、歷月統計、查個股。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import database
import revenue_rank as module

TW = module.TW_TZ
FIELDS = ["code", "name", "market", "industry", "rev", "prevRev", "lastYearRev", "momPct", "yoyPct", "cumRev", "cumLastYearRev",
          "cumPct", "note"]


def mops(month: str, rows: list[list]) -> dict:
    return {"month": month, "fetched": "2026-10-09T18:31+08:00", "published": {"sii0": "115/10/09 18:00:09"},
            "fields": FIELDS, "rows": rows}


SEP = mops("2026-09", [
    ["2408", "南亞科", "TSE", "半導體業", 45_100_000, 44_700_000, 6_500_000, 0.9, 576.62, 300_000_000, 41_000_000, 626.95, None],
    ["3006", "晶豪科", "TSE", "半導體業", 7_460_000, 7_900_000, 1_270_000, -5.62, 470.23, 50_000_000, 11_000_000, 343.91, None],
    ["8111", "立碁", "OTC", "光電業", 100_000, 99_000, 99_000, 1.0, 1.0, 900_000, 950_000, -5.3, "備註"],
    ["5483", "中美晶", "OTC", "半導體業", 2_000_000, 2_100_000, 2_130_000, -4.8, -6.1, 20_000_000, 21_000_000, -4.8, None],
])
AUG = mops("2026-08", [
    ["2408", "南亞科", "TSE", "半導體業", 44_700_000, 40_000_000, 7_000_000, 11.75, 538.0, 0, 0, 0, None],
    ["3006", "晶豪科", "TSE", "半導體業", 7_900_000, 7_000_000, 1_200_000, 12.8, 558.0, 0, 0, 0, None],
    ["5483", "中美晶", "OTC", "半導體業", 2_100_000, 2_000_000, 2_000_000, 5.0, 10.0, 0, 0, 0, None],
])
SEEN = {"month": "2026-09", "baseline": "2026-10-02T12:31+08:00",
        "first": {"3006": "2026-10-02T12:31+08:00", "2408": "2026-10-05T23:58+08:00", "5483": "2026-10-07T00:40+08:00",
                  "8111": "2026-10-08T18:31+08:00"}}


class PureTests(unittest.TestCase):
    def test_parse_mirror(self) -> None:
        month, rows = module.parse_mirror(SEP)
        self.assertEqual(month, "2026-09")
        self.assertEqual([r["code"] for r in rows], ["2408", "3006", "8111", "5483"])
        self.assertEqual(rows[0]["yoyPct"], 576.62)

    def test_announce_day_known_and_baseline(self) -> None:
        base = SEEN["baseline"]
        self.assertEqual(module.announce_day("2026-10-02T12:31+08:00", base, "2026-09"), ("2026-10-02", False))   # 第一次抓就在
        self.assertEqual(module.announce_day("2026-10-05T23:58+08:00", base, "2026-09"), ("2026-10-05", True))
        self.assertEqual(module.announce_day("2026-10-07T00:40+08:00", base, "2026-09"), ("2026-10-06", True))    # 23:30 那輪拖到半夜
        self.assertEqual(module.announce_day("2026-11-01T12:31+08:00", "2026-11-01T12:31+08:00", "2026-10"), ("2026-11-01", True))
        self.assertEqual(module.announce_day(None, base, "2026-09"), (None, False))

    def test_next_day_change(self) -> None:
        series = [("2026-10-02", 100.0, 1), ("2026-10-05", 110.0, 1), ("2026-10-06", 99.0, 1)]
        self.assertEqual(module.next_day_change(series, "2026-10-05"), -10.0)
        self.assertEqual(module.next_day_change(series, "2026-10-03"), 10.0)    # 週六公布：拿週五收盤比下週一
        self.assertIsNone(module.next_day_change(series, "2026-10-06"))         # 最新一天公布，還沒有隔日
        self.assertIsNone(module.next_day_change(series, "2026-10-01"))         # 公布日之前沒有收盤


class DbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.clear()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def add_bars(self, code: str, bars: list[tuple[str, float]], volume: int = 1000) -> None:
        with database.get_connection() as connection:
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(code, d, c, c, c, c, volume) for d, c in bars])

    def collect(self) -> dict:
        def fake(url: str):
            if "revenue-index" in url:
                return {"months": ["2026-09", "2026-08"], "updated": "2026-10-09T18:31+08:00"}
            if "revenue-mops-2026-09" in url:
                return SEP
            if "revenue-mops-2026-08" in url:
                return AUG
            if "revenue-seen-2026-09" in url:
                return SEEN
            raise RuntimeError("404 " + url)

        with patch.object(module, "_mirror_url", lambda name, volatile: name):
            return module.collect(now=datetime(2026, 10, 9, 18, 40, tzinfo=TW), fetcher=fake)

    def test_payload_rows_flags_and_history(self) -> None:
        result = self.collect()
        self.assertEqual(result["months"], {"2026-09": 4, "2026-08": 3})
        self.add_bars("2408", [("2026-10-02", 500.0), ("2026-10-05", 510.0), ("2026-10-06", 520.2), ("2026-10-08", 514.0)], 38411)
        self.add_bars("3006", [("2026-10-02", 260.0), ("2026-10-05", 270.0), ("2026-10-08", 271.0)])
        self.add_bars("5483", [("2026-10-05", 100.0), ("2026-10-06", 100.0), ("2026-10-07", 96.6), ("2026-10-08", 95.0)])
        self.add_bars("8111", [("2026-10-07", 93.0), ("2026-10-08", 93.7)])
        p = module.build_payload(now=datetime(2026, 10, 9, 18, 40, tzinfo=TW))
        self.assertEqual((p["month"], p["months"]), ("2026-09", ["2026-09", "2026-08"]))
        idx = {f: i for i, f in enumerate(p["fields"])}
        rows = {r[idx["code"]]: r for r in p["rows"]}
        self.assertEqual(p["counts"], {"published": 4, "tse": 2, "otc": 2, "positive": 3})
        south = rows["2408"]
        self.assertEqual((south[idx["announce"]], south[idx["known"]], south[idx["next"]]), ("2026-10-05", True, 2.0))
        self.assertEqual((south[idx["close"]], south[idx["volume"]], south[idx["revYi"]], south[idx["prevYoy"]]), (514.0, 38411, 451.0, 538.0))
        self.assertEqual((rows["3006"][idx["known"]], rows["3006"][idx["next"]]), (False, None))   # 第一次抓就在：不算隔日
        self.assertEqual(rows["5483"][idx["announce"]], "2026-10-06")
        self.assertEqual(rows["5483"][idx["next"]], -3.4)
        self.assertIsNone(rows["8111"][idx["next"]])                       # 10/08 公布，還沒有隔日
        self.assertEqual(p["latestDay"], "2026-10-08")
        self.assertEqual(p["announceDays"], ["2026-10-05", "2026-10-06", "2026-10-08"])
        hist = p["history"]
        self.assertEqual([h["month"] for h in hist], ["2026-09"])            # 8 月是補抓的，沒有公布日
        sep = hist[0]
        self.assertEqual((sep["all"]["n"], sep["all"]["up"]), (2, 1))
        self.assertEqual(sep["accel"]["n"], 1)      # 南亞科 576.6 vs 538：+38.6 點
        self.assertEqual(sep["decel"]["n"], 1)      # 中美晶 -6.1 vs 10：-16.1 點
        self.assertEqual((sep["yoy100"]["n"], sep["warn"]["n"], sep["nowarn"]["n"]), (1, 1, 1))

    def test_report_section(self) -> None:
        """籌碼日報的營收面精選：基準日當時已公布、年增 ≥30%、均線分數 ≥6、成交值 ≥5,000 萬，依年增排。"""
        import heilong_backtest

        self.collect()
        with database.get_connection() as connection:
            heilong_backtest._schema(connection)
            for code, close, vol, score in (("2408", 514.0, 38411, 12), ("3006", 271.0, 100, 9), ("5483", 95.0, 9000, 10), ("8111", 93.7, 900, 3)):
                connection.execute("INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2) VALUES ('2026-10-08', ?, 1, 1, 1, ?, ?, ?)",
                                   (code, close, vol, score))
        sec = module.report_section("2026-10-08")
        self.assertEqual((sec["month"], sec["published"], sec["yoyHigh"], sec["qualified"]), ("2026-09", 4, 2, 1))
        pick = sec["picks"][0]   # 晶豪科年增也 ≥30%，但成交值 271 × 100 張 ＝ 2,710 萬不夠
        self.assertEqual((pick["code"], pick["yoy"], pick["score"], pick["turnoverYi"], pick["accel"], pick["revYi"]), ("2408", 576.62, 12, 197.43, True, 451.0))
        early = module.report_section("2026-10-05")   # 中美晶 10/06、立碁 10/08 才公布；晶豪科是第一次抓就在（公布日不確定）算已公布
        self.assertEqual((early["published"], early["yoyHigh"]), (2, 2))
        self.assertEqual(module.report_section("2026-09-30")["month"], "2026-08")
        self.assertIsNone(module.report_section("2026-08-15"))

    def test_stock_detail(self) -> None:
        self.collect()
        self.add_bars("2408", [("2026-10-02", 500.0), ("2026-10-05", 510.0), ("2026-10-06", 520.2)])
        d = module.stock_detail("2408")
        self.assertEqual((d["status"], d["name"], d["market"]), ("ok", "南亞科", "上市"))
        self.assertEqual([m["month"] for m in d["months"]], ["2026-09", "2026-08"])
        self.assertEqual(d["months"][0]["accel"], 38.62)
        self.assertEqual(d["months"][0]["next"], 2.0)
        self.assertEqual(module.stock_detail("9999")["status"], "none")


if __name__ == "__main__":
    unittest.main()
