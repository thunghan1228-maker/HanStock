"""國際大事行事曆：TradingView 事件換台灣時間、同報告合一筆、單位與期別中文化、記者會掛附註、整天事件合併、
固定行程（台指期結算、四巫日、公司法說）、下一件大事、小學堂、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import macro_calendar as module
import persistent_app

TW = module.TW_TZ


def row(country, title, when, actual=None, forecast=None, previous=None, unit=None, period="Sep", scale=None):
    return {"id": f"{country}-{title}-{when}", "country": country, "title": title, "date": when, "actual": actual, "forecast": forecast,
            "previous": previous, "unit": unit, "period": period, "scale": scale, "importance": 1}


PAYLOAD = {"status": "ok", "result": [
    row("US", "Inflation Rate YoY", "2026-10-14T12:30:00.000Z", None, 3.6, 3.4, "%"),
    row("US", "Core Inflation Rate MoM", "2026-10-14T12:30:00.000Z", None, 0.2, 0.3, "%"),
    row("US", "CPI", "2026-10-14T12:30:00.000Z", None, 330, 329),                                  # 指數本身不收
    row("US", "Continuing Jobless Claims", "2026-10-08T12:30:00.000Z", 1716, 1710, 1699, period="Sep/26", scale="K"),
    row("US", "Initial Jobless Claims", "2026-10-08T12:30:00.000Z", 197, 200, 199, period="Oct/03", scale="K"),
    row("US", "Fed Interest Rate Decision", "2026-10-28T18:00:00.000Z", None, None, 4, period=""),
    row("US", "Fed Press Conference", "2026-10-28T18:30:00.000Z", period=""),
    row("US", "Michigan Consumer Sentiment Prel", "2026-10-09T14:00:00.000Z", 46.3, 47.6, 48.1, period="Oct"),
    row("US", "Non Farm Payrolls", "2026-11-06T13:30:00.000Z", None, None, 29, period="Oct"),
    row("US", "Midterm Elections", "2026-11-03T00:00:00.000Z", period=""),
    row("CN", "Communist Party Fifth Plenum", "2026-10-26T00:00:00.000Z", period=""),
    row("CN", "Communist Party Fifth Plenum", "2026-10-27T00:00:00.000Z", period=""),
    row("CN", "Balance of Trade", "2026-10-14T03:00:00.000Z", None, 114.5, 119.1, "$", scale="B"),
    row("JP", "Unemployment Rate", "2026-10-29T23:30:00.000Z", None, None, 2.5),                   # 不在清單
    {"country": "US", "title": "Retail Sales MoM"},                                                 # 沒日期就跳過
]}


class MacroCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_parse(self) -> None:
        events = {e["group"] + "@" + e["date"]: e for e in module.parse_events(PAYLOAD)}
        self.assertEqual(sorted(events), ["cn_plenum@2026-10-26", "cn_trade@2026-10-14", "fomc@2026-10-29", "us_claims@2026-10-08",
                                          "us_cpi@2026-10-14", "us_jobs@2026-11-06", "us_midterm@2026-11-03", "us_umich@2026-10-09"])
        cpi = events["us_cpi@2026-10-14"]
        self.assertEqual((cpi["time"], cpi["zh"], cpi["stars"], cpi["key"], cpi["period"], cpi["released"]),
                         ("20:30", "美 消費者物價指數 CPI", 3, "cpi", "9月", False))
        self.assertEqual([(x["label"], x["previous"], x["forecast"], x["actual"]) for x in cpi["indicators"]],
                         [("CPI 年增率", "3.4%", "3.6%", None), ("核心 CPI 月增率", "0.3%", "0.2%", None)])
        claims = events["us_claims@2026-10-08"]                         # 期別跟著初領；千人換萬人
        self.assertEqual((claims["period"], claims["released"]), ("10/3 當週", True))
        self.assertEqual([(x["label"], x["actual"], x["vs"]) for x in claims["indicators"]],
                         [("初領失業金", "19.7 萬", "低於預期"), ("續領失業金", "171.6 萬", "高於預期")])
        fomc = events["fomc@2026-10-29"]                               # 美東 14:00 → 台灣隔天 02:00，記者會掛附註
        self.assertEqual((fomc["time"], fomc["notes"], fomc["indicators"][0]["previous"]), ("02:00", ["02:30 主席記者會"], "4%"))
        self.assertEqual(events["us_umich@2026-10-09"]["zh"], "美 密大消費者信心（初值）")
        self.assertEqual(events["us_jobs@2026-11-06"]["time"], "21:30")       # 11 月美國冬令時間
        self.assertEqual(events["us_jobs@2026-11-06"]["indicators"][0]["previous"], "2.9 萬")
        plenum = events["cn_plenum@2026-10-26"]
        self.assertEqual((plenum["time"], plenum.get("until")), ("", "2026-10-27"))
        self.assertEqual(events["us_midterm@2026-11-03"]["time"], "")
        self.assertEqual(events["cn_trade@2026-10-14"]["indicators"][0]["forecast"], "1,145 億美元")
        self.assertEqual(module.parse_events({"result": None}), [])

    def test_helpers(self) -> None:
        self.assertEqual(module.classify("US", "GDP Growth Rate QoQ Adv"), ("us_gdp", "GDP 季增年率", "%"))
        self.assertIsNone(module.classify("US", "CPI s.a"))
        self.assertEqual([module._period(x) for x in ("Sept", "Q3", "Oct/17", "20261014", "")], ["9月", "Q3", "10/17 當週", "10/14", ""])
        self.assertEqual(module._fmt(7.079, "m"), "707.9 萬")
        self.assertEqual(module._fmt(-1.5, None, None, "B"), "-1.5 十億")
        self.assertEqual(module._vs(3.6, "3.6"), "符合預期")
        self.assertIsNone(module._vs(None, 3.6))

    def test_fixed_events(self) -> None:
        events = module.fixed_events(date(2026, 10, 1), date(2026, 12, 31))
        taifex = [e["date"] for e in events if e["key"] == "taifex"]
        self.assertEqual(taifex, ["2026-10-21", "2026-11-18", "2026-12-16"])
        self.assertEqual([e["date"] for e in events if e["key"] == "witching"], ["2026-12-18"])
        tsmc = next(e for e in events if e["key"] == "call")
        self.assertEqual((tsmc["date"], tsmc["time"], tsmc["stars"]), ("2026-10-15", "14:00", 3))

    def test_fetch_and_calendar(self) -> None:
        now = datetime(2026, 10, 10, 9, 0, tzinfo=TW)
        urls: list[str] = []
        result = module.fetch(fetcher=lambda u: urls.append(u) or PAYLOAD, now=now)
        self.assertEqual(result["count"], 8)
        self.assertIn("from=2026-10-03T00:00:00.000Z", urls[0])
        self.assertIn("countries=US,CN,JP,EU", urls[0])
        cal = module.calendar(now=now)
        self.assertEqual((cal["status"], cal["updatedAt"], cal["from"], cal["to"]), ("ok", "2026-10-10T09:00:00+08:00", "2026-10-03", "2026-11-21"))
        self.assertEqual((cal["next"]["zh"], cal["next"]["date"]), ("美 消費者物價指數 CPI", "2026-10-14"))
        keys = [e["key"] for e in cal["events"]]
        self.assertIn("taifex", keys)
        self.assertIn("call", keys)
        dates = [(e["date"], e["time"] or "99:99") for e in cal["events"]]
        self.assertEqual(dates, sorted(dates))
        cats = [c["cat"] for c in cal["glossary"]]
        self.assertEqual(cats, ["jobs", "prices", "growth", "cb", "abroad", "politics", "tw", "basics"])
        cards = {c["key"] for cat in cal["glossary"] for c in cat["cards"]}
        self.assertTrue({e["key"] for e in cal["events"]} <= cards)       # 每個事件都有小學堂可連
        # 抓到 0 筆不覆蓋舊資料
        with self.assertRaises(RuntimeError):
            module.fetch(fetcher=lambda u: {"result": []}, now=now)
        self.assertEqual(len(module.calendar(now=now)["events"]), len(cal["events"]))

    def test_every_catalog_group_has_glossary(self) -> None:
        for group, (_, stars, key) in module.GROUPS.items():
            self.assertIn(key, module.GLOSSARY, group)
            self.assertIn(stars, (1, 2, 3))
        for _, _, group, _, fmt in module.CATALOG:
            self.assertIn(group, module.GROUPS)
            self.assertIn(fmt, (None, "%", "k", "m", "usd_b"))

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", return_value=PAYLOAD):
            r = client.get("/api/hub/macro-calendar")
        body = r.json()
        self.assertEqual((r.status_code, body["status"]), (200, "ok"))
        self.assertTrue(any(e["group"] == "us_cpi" for e in body["events"]) or body["from"] > "2026-10-14")
        with patch.object(module, "fetch", return_value={"count": 3, "at": "x"}):
            r = client.post("/api/hub/macro-calendar/fetch")
        self.assertEqual(r.json()["result"]["count"], 3)


if __name__ == "__main__":
    unittest.main()
