"""自選股一頁看完：內部人近三月＋連續月數＋大戶雙買、下次法說（自辦／論壇、已結束的不算）、季報摘要與本益比、
季報補抓額度與 pending、代號清理、端點。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database
import fundamentals_daily
import insider_watch
import macro_calendar
import persistent_app
import watch_digest as module
from test_insider_watch import AUG, INDEX, JULY
from test_stock_profile import fin_rows

NOW = datetime(2026, 10, 10, 21, 0, tzinfo=macro_calendar.TW_TZ)
CONF = [
    {"code": "3037", "name": "欣興", "start": "2026-09-01", "end": "2026-09-01", "time": "14:00", "summary": "公布第二季"},     # 已經過了
    {"code": "3037", "name": "欣興", "start": "2026-10-20", "end": "2026-10-20", "time": "14:00", "place": "線上", "summary": "公布第三季財務報告"},
    {"code": "3037", "name": "欣興", "start": "2026-11-02", "end": "2026-11-02", "time": "09:00", "summary": "受邀參加某證券論壇"},
    {"code": "1240", "name": "茂生農經", "start": "2026-10-05", "end": "2026-10-12", "time": "", "summary": "受邀參加券商論壇"},   # 區間還沒過完
]


class WatchDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        insider_watch._cache.clear()
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [("3037", f"2026-08-{d:02d}T00:00:00", 100, 100, 100, p, 1000) for d, p in ((3, 190), (4, 210))] +
                          [("3037", "2026-10-08T00:00:00", 1, 1, 1, 241.5, 1)])
            macro_calendar._schema(c)
            c.execute("INSERT INTO macro_calendar (key, value, updated_at) VALUES ('conference', ?, 'x')", (json.dumps(CONF, ensure_ascii=False),))
        fundamentals_daily.save_tdcc("2026-09-04", [("3037", lv, 10, 1, 10.0) for lv in (12, 13, 14, 15)] + [("1240", 12, 1, 1, 30.0)])
        fundamentals_daily.save_tdcc("2026-10-02", [("3037", lv, 10, 1, 10.5) for lv in (12, 13, 14, 15)] + [("1240", 12, 1, 1, 29.0)])
        mirror = {"insider-index.json": INDEX, "insider-2026-08.json": AUG, "insider-2026-07.json": JULY}
        insider_watch.collect(fetcher=lambda url: mirror[url.split("/")[-1].split("?")[0]], now=NOW)
        self.calls: list[dict] = []

    def tearDown(self) -> None:
        insider_watch._cache.clear()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetcher(self, params):
        self.calls.append(params)
        return fin_rows() if params.get("data_id") == "3037" else {"data": []}

    def test_digest(self) -> None:
        d = module.digest(" 3037, 1240,abc!,3037,0050 ", now=NOW, fetcher=self.fetcher)
        self.assertEqual((d["status"], d["insiderMonth"], [r["code"] for r in d["rows"]], d["pending"]), ("ok", "2026-08", ["3037", "1240", "0050"], []))
        by = {r["code"]: r for r in d["rows"]}
        self.assertEqual((by["3037"]["name"], by["1240"]["name"], by["0050"]["name"]), ("欣興", "茂生農經", None))   # 法說會的短名優先
        ins = by["3037"]["insider"]
        self.assertEqual((ins["netLots"], ins["amount"], ins["centralLots"], ins["streak"], ins["combo"]), (1500, 3.0, 800, 1, "雙買"))
        self.assertEqual([m["month"] for m in ins["months"]], ["2026-08", "2026-07"])
        self.assertEqual((ins["big400"]["pct"], ins["big400"]["chg4w"]), (42.0, 2.0))
        self.assertEqual((by["1240"]["insider"]["netLots"], by["1240"]["insider"]["centralLots"], by["1240"]["insider"]["combo"]), (-300, None, "雙賣"))
        self.assertIsNone(by["0050"]["insider"])
        call = by["3037"]["call"]
        self.assertEqual((call["date"], call["time"], call["invited"]), ("2026-10-20", "14:00", False))
        self.assertEqual((by["1240"]["call"]["date"], by["1240"]["call"]["until"], by["1240"]["call"]["invited"]), ("2026-10-05", "2026-10-12", True))
        fin = by["3037"]["fin"]
        self.assertEqual((fin["ttmEps"], fin["pe"], fin["quarter"], fin["eps"], fin["closeDate"]), (12.43, 19.4, "2026Q2", 8.45, "2026-10-08"))
        self.assertEqual((fin["epsYoY"], fin["revYoY"]), (None, 34.0))           # 去年同季 EPS 0.05 太小，不算百分比
        self.assertEqual((by["0050"]["fin"]["ttmEps"], by["0050"]["fin"]["quarters"]), (None, 0))
        # 第二次：都有快取，不再抓
        n = len(self.calls)
        module.digest("3037,1240", now=NOW, fetcher=self.fetcher)
        self.assertEqual(len(self.calls), n)

    def test_budget_and_pending(self) -> None:
        d = module.digest("3037,1240,2330", now=NOW, budget=1, fetcher=self.fetcher)
        self.assertEqual((d["pending"], [c["data_id"] for c in self.calls]), (["1240", "2330"], ["3037"]))
        self.assertIsNone(next(r for r in d["rows"] if r["code"] == "2330")["fin"])
        d = module.digest("3037,1240,2330", now=NOW, budget=5, fetcher=self.fetcher)
        self.assertEqual(d["pending"], [])
        self.assertEqual(module.digest("", now=NOW)["status"], "empty")

        def broken(params):
            raise OSError("down")

        d = module.digest("9999", now=NOW, fetcher=broken)
        self.assertEqual((d["pending"], "OSError" in d["rows"][0]["finError"]), ([], True))

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "digest", return_value={"status": "ok", "rows": [], "pending": []}) as dg:
            r = client.get("/api/hub/watch-digest", params={"codes": "2330,3037"})
        self.assertEqual((r.status_code, r.json()["status"], dg.call_args[0][0]), (200, "ok", "2330,3037"))


if __name__ == "__main__":
    unittest.main()
