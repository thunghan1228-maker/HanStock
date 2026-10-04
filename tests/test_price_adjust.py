"""日K還原（分割減資）：官方表解析、整數倍取整、連乘因子、停止買賣跳空推測、櫃買行情反推參考價、來源優先序。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import price_adjust as module

TWSE_TWTAUU = {
    "stat": "OK",
    "fields": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格", "開盤競價基準", "除權參考價", "減資原因", "詳細資料"],
    "data": [["113/01/22", "3432", "台端", "10.65", "19.69", "21.65", "17.75", "19.70", "--", "彌補虧損", "3432  ,20240110"],
             ["113/08/26", "3481", "群創", "14.55", "15.17", "16.65", "13.70", "15.15", "--", "退還股款", "3481  ,20240814"]],
}
TWSE_TWTB8U = {
    "stat": "OK",
    "fields": ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格", "開盤競價基準", "詳細資料"],
    "data": [["114/08/25", "2327", "國巨", "546.00", "136.50", "150.00", "123.00", "136.50", "2327,20250814,20250825"],
             ["114/07/21", "6919", "康霈*", "1,215.00", "121.50", "133.50", "109.50", "121.50", "x"]],
}
TPEX_REVIVT = {   # 鏡像存的格式（fields＋data）；櫃買原始回應是 tables[0]
    "year": 2024,
    "fields": ["恢復買賣日期", "股票代號", "名稱", "最後交易日之收盤價格", "減資恢復買賣開始日參考價格", "漲停價格", "跌停價格", "開始交易基準價", "除權參考價", "減資原因", "詳細資料"],
    "data": [["1130205", "3064", "泰偉", "10.65", "35.50", "39.05", "31.95", "35.50", "0.00", "彌補虧損", "<table></table>"]],
}


class ParseTests(unittest.TestCase):
    def test_twse_reduction_and_par(self) -> None:
        rows = module.parse_official_table(TWSE_TWTAUU, source="twse", kind="reduction")
        self.assertEqual([(r["code"], r["date"], r["prevClose"], r["refPrice"], r["note"]) for r in rows],
                         [("3432", "2024-01-22", 10.65, 19.69, "彌補虧損"), ("3481", "2024-08-26", 14.55, 15.17, "退還股款")])
        par = module.parse_official_table(TWSE_TWTB8U, source="twse", kind="par")
        self.assertEqual([(r["code"], r["date"], r["factor"]) for r in par], [("2327", "2025-08-25", 0.25), ("6919", "2025-07-21", 0.1)])

    def test_tpex_formats(self) -> None:
        rows = module.parse_official_table(TPEX_REVIVT, source="tpex", kind="reduction")
        self.assertEqual([(r["code"], r["date"], r["prevClose"], r["refPrice"]) for r in rows], [("3064", "2024-02-05", 10.65, 35.5)])
        wrapped = {"tables": [{"fields": TPEX_REVIVT["fields"], "data": TPEX_REVIVT["data"]}]}
        self.assertEqual(len(module.parse_official_table(wrapped, source="tpex", kind="reduction")), 1)
        self.assertEqual(module.parse_official_table({"stat": "很抱歉"}, source="twse", kind="par"), [])
        self.assertEqual(module.parse_official_table("not json", source="twse", kind="par"), [])

    def test_snap_factor(self) -> None:
        self.assertEqual(module.snap_factor(47.50 / 188.65), 0.25)   # 0050 一拆四：開盤 47.5／前收 188.65
        self.assertEqual(module.snap_factor(2.02), 2.0)
        self.assertAlmostEqual(module.snap_factor(1.667), 1.667)      # 減資 6 成：不是整數倍，原樣


class AdjustTests(unittest.TestCase):
    def test_adjust_bars_cumulative(self) -> None:
        bars = [("2024-01-02", 400.0, 404.0, 396.0, 400.0, 100), ("2024-01-03", 100.0, 101.0, 99.0, 100.0, 400),
                ("2024-06-03", 50.0, 51.0, 49.0, 50.0, 800), ("2024-06-04", 51.0, 52.0, 50.0, 51.0, 800)]
        events = [("2024-06-03", 0.5), ("2024-01-03", 0.25)]
        out = module.adjust_bars(bars, events)
        self.assertEqual(out[0], ("2024-01-02", 50.0, 50.5, 49.5, 50.0, 800))    # 兩個事件都在後面：×0.25×0.5
        self.assertEqual(out[1], ("2024-01-03", 50.0, 50.5, 49.5, 50.0, 800))    # 事件當天以後不乘這個事件
        self.assertEqual(out[2], bars[2])
        self.assertEqual(out[3], bars[3])
        self.assertIs(module.adjust_bars(bars, None), bars)


class DetectTests(unittest.TestCase):
    def test_halt_jump_inferred(self) -> None:
        days = ["2025-06-09", "2025-06-10", "2025-06-11", "2025-06-12", "2025-06-13", "2025-06-16", "2025-06-17", "2025-06-18"]
        bars = {"0050": [("2025-06-09", 183.4, 0, 0, 183.7, 0), ("2025-06-10", 184.9, 0, 0, 188.65, 0), ("2025-06-18", 47.5, 0, 0, 47.57, 0)],
                # 只差一天沒成交（冷門股）：不算
                "9999": [("2025-06-09", 10, 0, 0, 10, 0), ("2025-06-11", 13, 0, 0, 13, 0)],
                # 有官方事件落在停止買賣期間（恢復那天沒成交，日K第一根比較晚）：不重複推測
                "2327": [("2025-06-09", 546, 0, 0, 546, 0), ("2025-06-18", 140, 0, 0, 141, 0)]}
        out = module.detect_halt_jumps(bars, days, {"2327": ["2025-06-17"]})
        self.assertEqual([(e["code"], e["date"], e["factor"], e["source"]) for e in out], [("0050", "2025-06-18", 0.25, "inferred")])
        self.assertEqual(out[0]["refPrice"], 47.1625)

    def test_quote_ref_events(self) -> None:
        rows = [["3064", "泰偉", 31.95, 32.2, 31.95, 32.2, -3.3, 8170000],      # 減資恢復：參考價 35.5
                ["6488", "環球晶", 579.0, 584.0, 574.0, 579.0, 0.0, 679837],     # 昨天有成交：一般漲跌
                ["1234", "小股", 10.0, 10.0, 10.0, 10.2, 0.2, 1000],             # 停了幾天但參考價沒變多少（除息）：不算
                ["5555", "沒漲跌", 10.0, 10.0, 10.0, 10.0, None, 1000]]
        prev = {"3064": ("2024-01-24", 10.65), "6488": ("2024-02-02", 579.0), "1234": ("2024-01-24", 10.5), "5555": ("2024-01-24", 30.0)}
        out = module.quote_ref_events("2024-02-05", rows, prev, ["2024-02-02", "2024-02-01", "2024-01-31"])
        self.assertEqual([(e["code"], e["refPrice"], round(e["factor"], 4)) for e in out], [("3064", 35.5, 3.3333)])


class SaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_source_priority_and_version(self) -> None:
        v0 = module.events_version()
        module.save_events([{"code": "0050", "date": "2025-06-18", "prevClose": 188.65, "refPrice": 47.5, "factor": 0.2518, "source": "inferred", "kind": "inferred"}])
        self.assertEqual(module.load_events()["0050"], [("2025-06-18", 0.2518)])
        # 官方蓋掉推測的
        module.save_events([{"code": "0050", "date": "2025-06-18", "prevClose": 188.65, "refPrice": 47.16, "source": "twse", "kind": "par"}])
        self.assertAlmostEqual(module.load_events()["0050"][0][1], 47.16 / 188.65, places=6)
        # 推測的不能蓋掉官方的
        module.save_events([{"code": "0050", "date": "2025-06-18", "prevClose": 188.65, "refPrice": 47.5, "source": "inferred", "kind": "inferred"}])
        self.assertEqual(module.list_events()[0]["source"], "twse")
        self.assertNotEqual(module.events_version(), v0)
        self.assertEqual(module.load_events(["2330"]), {})
        self.assertEqual(module.save_events([{"code": "1", "date": "2025-01-01", "prevClose": 0, "refPrice": 1, "source": "twse"}]), 0)


if __name__ == "__main__":
    unittest.main()
