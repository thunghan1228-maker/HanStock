"""內部人研究室：鏡像解析與存取、董監／經理人／大股東增減、估算金額、集中市場明細、400 張大戶疊加（雙買／雙賣）、個股歷史、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import fundamentals_daily
import insider_watch as module
import persistent_app

FIELDS = ["code", "name", "market", "issued", "dirInc", "dirDec", "dirHold", "dirPct", "mgrHold", "bigHold"]
JULY = {"month": "2026-07", "fetched": "2026-08-20T20:40+08:00", "published": {"sii": "115/08/20"}, "fields": FIELDS, "rows": [
    ["3037", "欣興電子股份", "TSE", 1_500_000_000, 0, 0, 230_000_000, 15.33, 2_000_000, 204_423_723],
    ["1240", "茂生農經股份", "OTC", 44_232_373, 0, 0, 4_918_113, 11.11, 1_272_941, 0],
    ["5880", "配股金控", "TSE", 10_000_000, 0, 0, 1_000_000, 10.0, 0, 0],
], "detail": {}}
AUG = {"month": "2026-08", "fetched": "2026-09-22T20:40+08:00", "published": {"sii": "115/09/22", "otc": "115/09/18"}, "fields": FIELDS, "rows": [
    ["3037", "欣興電子股份", "TSE", 1_500_000_000, 1_000_000, 0, 231_000_000, 15.4, 2_500_000, 204_423_723],   # 董監 +100 萬、經理人 +50 萬
    ["1240", "茂生農經股份", "OTC", 44_232_373, 0, 300_000, 4_618_113, 10.44, 1_272_941, 0],                    # 董監 -30 萬
    ["9999", "沒動股份", "TSE", 10_000_000, 0, 0, 1_000_000, 10.0, 0, 0],
    ["5880", "配股金控", "TSE", 11_000_000, 100_000, 0, 1_100_000, 10.0, 0, 0],                                  # 股本 +10%、董監 +10 萬＝全部是配股
], "detail": {"3037": {"buyC": 800_000, "buyO": 200_000, "sellC": 0, "sellO": 0, "people": [["董事長", "某甲", 800000, 0, 0, 0, 5000000]]}}}
INDEX = {"months": ["2026-08", "2026-07"], "updated": "x"}


class InsiderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.clear()
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [("3037", f"2026-08-{d:02d}T00:00:00", 100, 100, 100, p, 1000) for d, p in ((3, 190), (4, 210))] +
                          [("1240", "2026-09-01T00:00:00", 50, 50, 50, 50, 100)])
        fundamentals_daily.save_tdcc("2026-09-04", [("3037", lv, 10, 1, 10.0) for lv in (12, 13, 14, 15)] + [("1240", 12, 1, 1, 30.0)])
        fundamentals_daily.save_tdcc("2026-10-02", [("3037", lv, 10, 1, 10.5) for lv in (12, 13, 14, 15)] + [("1240", 12, 1, 1, 29.0)])

    def tearDown(self) -> None:
        module._cache.clear()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetch(self, url: str):
        name = url.split("/")[-1].split("?")[0]
        return {"insider-index.json": INDEX, "insider-2026-08.json": AUG, "insider-2026-07.json": JULY}[name]

    def test_collect_and_overview(self) -> None:
        result = module.collect(fetcher=self.fetch, now=datetime(2026, 10, 10, 21, 30, tzinfo=module.TW_TZ))
        self.assertEqual(result["months"], {"2026-08": {"rows": 4, "detail": 1}, "2026-07": {"rows": 3, "detail": 0}})
        o = module.overview()
        self.assertEqual((o["status"], o["month"], o["months"], o["moved"], o["detailCount"]), ("ok", "2026-08", ["2026-08", "2026-07"], 2, 1))
        b = o["buys"][0]
        self.assertEqual((b["code"], b["dirNet"], b["mgrNet"], b["net"], b["netLots"], b["price"], b["amount"]), ("3037", 1_000_000, 500_000, 1_500_000, 1500, 200.0, 3.0))
        self.assertEqual((b["dirPctChg"], b["central"]["net"], b["central"]["amount"], b["central"]["otherNet"]), (0.07, 800_000, 1.6, 200_000))
        self.assertEqual((b["big400"]["pct"], b["big400"]["chg4w"], b["combo"], b["group"]), (42.0, 2.0, "雙買", "PCB"))
        s = o["sells"][0]                     # 8 月沒有日K，用 9 月第一根收盤估
        self.assertEqual((s["code"], s["net"], s["price"], s["amount"], s["combo"]), ("1240", -300_000, 50.0, -0.15, "雙賣"))
        self.assertEqual(o["centralBuys"][0]["code"], "3037")
        self.assertEqual((o["summary"]["buy"]["count"], o["summary"]["sell"]["amount"]), (1, -0.15))
        self.assertEqual(module.overview("2026-07")["moved"], 0)            # 7 月沒有上個月可比，只看董監增減
        div = [x for x in o["buys"] + o["sells"] if x["code"] == "5880"]
        self.assertEqual(div, [])                                           # 配股扣掉後沒動，不上榜

    def test_stock(self) -> None:
        module.collect(fetcher=self.fetch)
        st = module.stock("3037")
        self.assertEqual((st["status"], [h["month"] for h in st["history"]], st["history"][0]["net"]), ("ok", ["2026-08", "2026-07"], 1_500_000))
        self.assertEqual([w["p"] for w in st["big400"]["weeks"]], [40.0, 42.0])
        self.assertEqual(module.stock("1234")["status"], "missing")

    def test_missing_and_due(self) -> None:
        self.assertEqual(module.overview()["status"], "missing")
        at = lambda s: datetime.fromisoformat(s + "+08:00")  # noqa: E731
        self.assertTrue(module.due(at("2026-10-10T08:00:00"), None))
        self.assertFalse(module.due(at("2026-10-10T20:00:00"), "2026-10-09T21:30:00+08:00"))
        self.assertTrue(module.due(at("2026-10-10T21:30:00"), "2026-10-09T21:30:00+08:00"))
        self.assertFalse(module.due(at("2026-10-10T23:00:00"), "2026-10-10T21:31:00+08:00"))

    def test_endpoints(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", self.fetch):
            r = client.post("/api/hub/insider/collect")
        self.assertEqual(r.json()["result"]["months"]["2026-08"]["rows"], 4)
        self.assertEqual(client.get("/api/hub/insider?month=2026-08").json()["buys"][0]["code"], "3037")
        self.assertEqual(client.get("/api/hub/insider?month=bad").status_code, 422)
        self.assertEqual(client.get("/api/hub/insider/stock?code=1240").json()["history"][0]["dirNet"], -300_000)


if __name__ == "__main__":
    unittest.main()
