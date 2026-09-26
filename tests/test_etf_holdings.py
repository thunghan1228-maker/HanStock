"""主動式 ETF 五檔每日持股：鏡像解析、存取、與前一份的差異、五檔同步、收集器。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import etf_holdings as module

DAY1, DAY2, DAY3 = "2026-09-23", "2026-09-24", "2026-09-29"


def mirror(date: str, etfs: dict) -> dict:
    return {"date": date, "etfs": {code: {"name": module.ETF_META[code][0], "issuer": module.ETF_META[code][1], "source": "test", "nav": 1000.0, "units": 10.0 + i, "rows": rows}
                                   for i, (code, rows) in enumerate(etfs.items())}}


class ParseTests(unittest.TestCase):
    def test_parse_mirror(self) -> None:
        date, etfs = module.parse_mirror({"date": DAY2, "etfs": {"00981A": {"rows": [["2330", "台積電", "11,464,000", "9.77"], ["bad"], ["2454", "聯發科", "x", 1]]}, "00403A": {"rows": []}}})
        self.assertEqual(date, DAY2)
        self.assertEqual(list(etfs), ["00981A"])
        self.assertEqual(etfs["00981A"]["rows"], [("2330", "台積電", 11464000, 9.77)])
        self.assertEqual((etfs["00981A"]["name"], etfs["00981A"]["issuer"]), ("主動統一台股增長", "統一投信"))
        self.assertEqual(module.parse_mirror({"date": "bad"}), (None, {}))
        self.assertEqual(module.parse_mirror([]), (None, {}))


class HoldingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self) -> None:
        d1 = mirror(DAY1, {"00981A": [["2330", "台積電", 11864000, 9.9], ["2303", "聯電", 89118000, 4.7], ["1303", "南亞", 4129000, 0.6]],
                           "00403A": [["2330", "台積電", 7700000, 12.8], ["2303", "聯電", 30000000, 3.0]]})
        d2 = mirror(DAY2, {"00981A": [["2330", "台積電", 11464000, 9.77], ["2303", "聯電", 87118000, 4.62], ["3017", "奇鋐", 5889000, 7.21]],
                           "00403A": [["2330", "台積電", 7700000, 12.81], ["2303", "聯電", 29000000, 2.9], ["3017", "奇鋐", 2850000, 6.81]],
                           "00991A": [["2330", "台灣積體", 3800000, 11.92]]})
        for payload in (d1, d2):
            date, etfs = module.parse_mirror(payload)
            for code, item in etfs.items():
                module.save_snapshot(date, code, item)

    def test_changes_and_sync(self) -> None:
        self._seed()
        self.assertEqual(module.dates(), [DAY2, DAY1])
        self.assertEqual(module.stored_codes(DAY2), {"00981A", "00403A", "00991A"})
        c = module.etf_changes("00981A", DAY2)
        self.assertEqual((c["prevDate"], c["holdings"], c["prevHoldings"]), (DAY1, 3, 3))
        self.assertEqual(c["counts"], {"new": 1, "increased": 0, "decreased": 2, "removed": 1, "unchanged": 0})
        self.assertEqual([r["code"] for r in c["decreased"]], ["2303", "2330"])   # 減最多的在前
        self.assertEqual((c["decreased"][0]["deltaShares"], c["decreased"][0]["deltaLots"], c["decreased"][0]["prevShares"]), (-2000000, -2000.0, 89118000))
        self.assertEqual((c["new"][0]["code"], c["new"][0]["deltaLots"], c["new"][0]["weight"]), ("3017", 5889.0, 7.21))
        self.assertEqual((c["removed"][0]["code"], c["removed"][0]["shares"], c["removed"][0]["deltaLots"]), ("1303", 0, -4129.0))
        self.assertEqual(c["top"][0]["code"], "2330")
        self.assertEqual(c["unitsDelta"], 0.0)
        # 復華那檔只有一天：沒有前一份
        c3 = module.etf_changes("00991A", DAY2)
        self.assertIsNone(c3["prevDate"])
        self.assertEqual(c3["counts"]["new"], 0)
        # 五檔同步：奇鋐兩檔新增（買）、聯電兩檔減碼（賣）；台積電只有一檔減 → 不算
        section = module.report_section(DAY2)
        self.assertEqual(section["date"], DAY2)
        self.assertEqual([e["code"] for e in section["etfs"]], ["00981A", "00403A", "00991A"])
        self.assertEqual(section["missing"], ["00982A", "00992A"])
        buy, sell = section["sync"]["buy"], section["sync"]["sell"]
        self.assertEqual([(b["code"], b["count"], b["deltaLots"]) for b in buy], [("3017", 2, 8739.0)])
        self.assertEqual([(s["code"], s["count"], s["deltaLots"]) for s in sell], [("2303", 2, -3000.0)])
        self.assertEqual(section["withPrev"], 2)
        # 基準日早於資料：取更早的一天；沒有就 None
        self.assertEqual(module.report_section(DAY1)["date"], DAY1)
        self.assertIsNone(module.report_section("2026-09-01"))
        self.assertIsNone(module.etf_changes("00982A", DAY2))

    def test_prev_gap_limit(self) -> None:
        self._seed()
        far = mirror("2026-10-20", {"00981A": [["2330", "台積電", 1, 1.0]]})
        date, etfs = module.parse_mirror(far)
        module.save_snapshot(date, "00981A", etfs["00981A"])
        c = module.etf_changes("00981A", "2026-10-20")
        self.assertIsNone(c["prevDate"])     # 隔太久不算前一天

    def test_collect_from_mirror(self) -> None:
        files = {
            "etf-index.json": [DAY3, DAY2, DAY1],
            f"etf-{DAY1}.json": mirror(DAY1, {"00981A": [["2330", "台積電", 100000, 9.0]]}),
            f"etf-{DAY2}.json": mirror(DAY2, {"00981A": [["2330", "台積電", 110000, 9.5]], "00403A": [["2330", "台積電", 50000, 12.0]]}),
            f"etf-{DAY3}.json": {"date": DAY2, "etfs": {}},   # 檔案日期不符
        }
        calls: list[str] = []

        def fetcher(url: str):
            name = url.split("/")[-1].split("?")[0]
            calls.append(name)
            if name not in files:
                raise module.urllib.error.HTTPError(url, 404, "nf", None, None)  # type: ignore[arg-type]
            return files[name]

        r = module.collect_once(fetcher)
        self.assertEqual(sorted(r["added"]), [f"{DAY1}:00981A", f"{DAY2}:00403A", f"{DAY2}:00981A"])
        self.assertEqual(r["errors"], [f"{DAY3}: 檔案日期 {DAY2} 不符"])
        self.assertEqual(module.dates(), [DAY2, DAY1])
        # 再抓一次：兩天都還沒滿五檔，會再看一次，但已存的不重存
        calls.clear()
        r2 = module.collect_once(fetcher)
        self.assertEqual(r2["added"], [])
        self.assertIn(f"etf-{DAY1}.json", calls)
        # 補齊 DAY2 的另外一檔
        files[f"etf-{DAY2}.json"]["etfs"]["00991A"] = {"rows": [["2330", "台灣積體", 3800000, 11.9]]}
        r3 = module.collect_once(fetcher)
        self.assertEqual(r3["added"], [f"{DAY2}:00991A"])
        self.assertEqual(module.holdings("00991A", DAY2)["2330"]["shares"], 3800000)
        status = module.collector_status()
        self.assertEqual((status["latest"], status["lastError"]), (DAY2, f"{DAY3}: 檔案日期 {DAY2} 不符"))


if __name__ == "__main__":
    unittest.main()
