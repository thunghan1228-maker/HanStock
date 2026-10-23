"""除權息還原：兩邊官方表解析、事件存取（只算新增／有變）、還原只動均線分數不動其他特徵、抓資料的月份切法。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import dividend_adjust as module
import heilong_backtest

TWSE_PAYLOAD = {
    "stat": "OK",
    "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息", "漲停價格", "跌停價格"],
    "data": [
        ["114年10月21日", "2330", "台積電", "1,500.00", "1,495.00", "5.00", "息", "1,644.50", "1,345.50"],
        ["114年10月22日", "1101", "台泥", "30.00", "30.00", "0.00", "息", "33.00", "27.00"],          # 沒變動：不收
        ["114年10月22日", "1234", "怪怪", "100.00", "20.00", "80.00", "權", "22.00", "18.00"],         # 因子 0.2：資料怪，不收
        ["114年10月23日", "2412", "中華電", "--", "119.00", "1.00", "息", "130", "110"],               # 沒有前收盤價：不收
    ],
}
TPEX_PAYLOAD = {
    "tables": [{
        "fields": ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值", "權/息"],
        "data": [["114/07/09", "6173", "信昌電", "273.00", "271.20", "0.000000", "1.80", "1.80", "除息"]],
    }]
}
MIRROR_PAYLOAD = {"year": 2026, "fields": TPEX_PAYLOAD["tables"][0]["fields"], "data": TPEX_PAYLOAD["tables"][0]["data"]}


class ParseTests(unittest.TestCase):
    def test_twse(self) -> None:
        rows = module.parse_table(TWSE_PAYLOAD, source="twse")
        self.assertEqual([(r["code"], r["date"], r["prevClose"], r["refPrice"]) for r in rows], [("2330", "2025-10-21", 1500.0, 1495.0)])
        self.assertAlmostEqual(rows[0]["factor"], 1495 / 1500)
        self.assertEqual(rows[0]["note"], "息")

    def test_tpex_raw_and_mirror(self) -> None:
        for payload in (TPEX_PAYLOAD, MIRROR_PAYLOAD):
            rows = module.parse_table(payload, source="tpex")
            self.assertEqual([(r["code"], r["date"], r["factor"] < 1) for r in rows], [("6173", "2025-07-09", True)])

    def test_bad_payloads(self) -> None:
        for payload in (None, [], {}, {"fields": ["a"], "data": []}, {"stat": "很抱歉，沒有符合條件的資料!"}):
            self.assertEqual(module.parse_table(payload, source="twse"), [])

    def test_date_formats(self) -> None:
        for text in ("114年10月21日", "114/10/21", "1141021", "20251021", "2025-10-21", " 114年 10月 21日 "):
            self.assertEqual(module._date(text), "2025-10-21", text)
        self.assertIsNone(module._date("--"))
        self.assertIsNone(module._date("114年13月01日"))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_save_and_load(self) -> None:
        events = module.parse_table(TWSE_PAYLOAD, source="twse") + module.parse_table(TPEX_PAYLOAD, source="tpex")
        self.assertEqual(module.save_events(events), 2)
        self.assertEqual(module.save_events(events), 0)                       # 一樣的資料再寫：沒有「變」
        changed = [{**events[0], "refPrice": 1494.0}]
        self.assertEqual(module.save_events(changed), 1)                      # 參考價變了：算一筆
        loaded = module.load_events()
        self.assertEqual(set(loaded), {"2330", "6173"})
        self.assertAlmostEqual(loaded["2330"][0][1], 1494 / 1500, places=6)
        self.assertEqual(list(module.load_events(["6173"])), ["6173"])
        self.assertEqual(module.last_event_date(), "2025-10-21")
        self.assertEqual(module.event_counts()["twse"]["n"], 1)

    def test_adjust_closes(self) -> None:
        module.save_events([{"code": "2330", "date": "2025-10-21", "prevClose": 100.0, "refPrice": 90.0, "source": "twse"}])
        out = module.adjust_closes({"2330": [100.0, 100.0, 90.0], "1101": [10.0, 10.0, 10.0]},
                                   {"2330": ["2025-10-17", "2025-10-20", "2025-10-21"], "1101": ["2025-10-17", "2025-10-20", "2025-10-21"]})
        self.assertEqual(out["2330"], [90.0, 90.0, 90.0])      # 除息日以前乘 0.9，除息日當天不動
        self.assertEqual(out["1101"], [10.0, 10.0, 10.0])

    def test_refresh_months_and_errors(self) -> None:
        calls = []

        def fetcher(url, params):
            calls.append((params["startDate"], params["endDate"]))
            if params["startDate"] == "20251101":
                raise RuntimeError("被擋")
            return TWSE_PAYLOAD

        out = module.refresh(date(2025, 10, 5), date(2025, 12, 10), fetcher=fetcher, mirror=lambda y: MIRROR_PAYLOAD, sleep=lambda s: None)
        self.assertEqual(calls, [("20251001", "20251031"), ("20251101", "20251130"), ("20251201", "20251210")])
        self.assertEqual((out["twse"], out["tpex"], out["changed"]), (2, 1, 2))   # 同一檔同一天只存一筆，所以 twse 兩個月只算新增 1 筆＋tpex 1 筆
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("2025-11", out["errors"][0])

    def test_month_ranges(self) -> None:
        self.assertEqual(module.month_ranges(date(2025, 12, 20), date(2026, 1, 5)), [(date(2025, 12, 1), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 1, 5))])
        self.assertEqual(module.month_ranges(date(2026, 2, 3), date(2026, 2, 3)), [(date(2026, 2, 1), date(2026, 2, 3))])


class ScoreOnlyTests(unittest.TestCase):
    """還原只影響均線分數（score2／maBits／hiBits／align），漲跌幅、乖離、收盤價都還是原本的。"""

    def setUp(self) -> None:
        self.days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(300)]
        # 一路小漲，倒數第 3 天除息 20 塊（價格 150 → 130）：沒還原時收盤掉到所有均線之下，還原後還是站在長均線之上
        closes = [100 + i * 0.5 for i in range(300)]
        for i in range(297, 300):
            closes[i] = closes[i] - 20
        self.bars = [(d, c, c, c, c, 1000) for d, c in zip(self.days, closes)]
        self.ex_date = self.days[297]

    def test_dividend_only_changes_score_fields(self) -> None:
        wanted = {self.days[-1]}
        raw = heilong_backtest.compute_features(self.bars, wanted)[self.days[-1]]
        prev_close = self.bars[296][4]
        events = {"AAAA": [(self.ex_date, (prev_close - 20) / prev_close)]}
        out = heilong_backtest.features_for("AAAA", self.bars, wanted, events)[self.days[-1]]
        self.assertGreater(out["score2"], raw["score2"])
        for key in ("close", "changePct", "bias", "hits20", "val5", "prevClose", "score"):
            self.assertEqual(out[key], raw[key], key)
        # 沒有事件的股票：跟原本一模一樣
        same = heilong_backtest.features_for("BBBB", self.bars, wanted, events)[self.days[-1]]
        self.assertEqual(same, raw)


if __name__ == "__main__":
    unittest.main()
