"""個股融資餘額：證交所／櫃買解析、收集、融資水位計算（用 2026-10-22 的真實欄位格式）。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import margin_balance as module
import persistent_app

TW = timezone(timedelta(hours=8))
TWSE_FIELDS = ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]
TPEX_FIELDS = ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]


def twse_payload(day: str, balance: int = 60136, prev: int = 60320) -> dict:
    return {"stat": "OK", "date": day.replace("-", ""), "tables": [{}, {"title": "融資融券彙總 (股票)", "fields": TWSE_FIELDS, "data": [
        ["　", "合計", "216,658", "269,074", "9,030", "6,690,788", "6,629,342", "192,948,336", "15,513", "21,881", "760", "118,948", "124,556", "192,948,336", "4,258", "　"],
        ["2408", "南亞科", "1,593", "1,758", "19", f"{prev:,}", f"{balance:,}", "774,656", "110", "714", "0", "1,374", "1,978", "774,656", "15", " "],
        ["2303", "聯電", "2,509", "3,190", "44", "149,840", "149,115", "3,142,523", "309", "331", "0", "2,163", "2,185", "3,142,523", "63", " "],
    ]}]}


def tpex_payload(day: str, balance: str = "12,958") -> dict:
    return {"date": day.replace("-", ""), "stat": "ok", "tables": [{"title": "上櫃股票融資融券餘額", "fields": TPEX_FIELDS, "data": [
        ["6488", "環球晶", "13,071", "576", "670", "19", balance, "231", "9.81", "132,028", "176", "18", "9", "0", "185", "0", "0.14", "132,028", "3", ""],
        ["6173", "信昌電", "5,971", "628", "1,511", "25", "5,063", "133", "12.58", "40,232", "4,030", "488", "618", "1", "3,899", "66", "9.69", "40,232", "24", "11  *A"],
    ]}]}


class ParseTests(unittest.TestCase):
    def test_twse_margn(self) -> None:
        day, rows = module.parse_twse_margn(twse_payload("2026-10-22"))
        self.assertEqual(day, "2026-10-22")
        self.assertEqual([r["code"] for r in rows], ["2408", "2303"])                      # 合計列（代號是全形空白）不算
        self.assertEqual(rows[0], {"code": "2408", "name": "南亞科", "buy": 1593, "sell": 1758, "repay": 19, "prev": 60320, "balance": 60136,
                                    "limit": 774656, "short": 1978})
        self.assertEqual(module.parse_twse_margn({"stat": "很抱歉，沒有符合條件的資料"}), (None, []))
        bad = twse_payload("2026-10-22")
        bad["tables"][1]["fields"] = ["代號", "名稱"] + ["x"] * 14
        with self.assertRaises(RuntimeError):
            module.parse_twse_margn(bad)

    def test_tpex_margin(self) -> None:
        day, rows = module.parse_tpex_margin(tpex_payload("2026-10-22"))
        self.assertEqual(day, "2026-10-22")
        self.assertEqual(rows[0], {"code": "6488", "name": "環球晶", "buy": 576, "sell": 670, "repay": 19, "prev": 13071, "balance": 12958,
                                    "limit": 132028, "short": 185})
        self.assertEqual(module.parse_tpex_margin({"date": "20261024", "stat": "ok", "tables": [{"fields": TPEX_FIELDS, "data": []}]}), (None, []))   # 休市日


class MetricsTests(unittest.TestCase):
    def test_metrics(self) -> None:
        series = {f"2026-10-{d:02d}": {"balance": b, "prev": p, "limit": 774656, "buy": 1, "sell": 2, "repay": 0}
                  for d, p, b in ((12, 100, 100), (13, 100, 120), (14, 120, 110), (15, 110, 105), (16, 105, 100), (19, 100, 90), (20, 90, 80), (21, 80, 70), (22, 70, 60))}
        m = module.metrics(series, "2026-10-22", "2026-10-13", 414.0)
        self.assertEqual((m["balance"], m["chg1"], m["chg1Pct"]), (60, -10, -14.29))
        self.assertEqual(m["sinceHighPct"], -50.0)                                      # 月高那天 120 張 → 今天 60 張，退場一半
        self.assertEqual(m["chg5Pct"], -42.86)                                          # 5 個交易日前（10/15）餘額 105 張
        self.assertEqual((m["util"], m["valueYi"]), (0.01, 0.2))
        self.assertIsNone(module.metrics(series, "2026-10-23", None, 414.0))            # 那天沒資料
        self.assertIsNone(module.metrics(None, "2026-10-22", None, 414.0))
        m = module.metrics({"2026-10-22": {"balance": 5, "prev": 0, "limit": 0, "buy": 0, "sell": 0, "repay": 0}}, "2026-10-22", "2026-10-08", 10.0)
        self.assertEqual((m["chg1"], m["chg5Pct"], m["sinceHighPct"], m["util"]), (None, None, None, None))   # 只有一天：能算的才算


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.days_patch = patch.object(module, "BACKFILL_DAYS", 3)
        self.days_patch.start()
        self.twse_calls: list[str] = []

    def tearDown(self) -> None:
        self.days_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetcher(self, url: str):
        self.twse_calls.append(url)
        ymd = url.split("date=")[1][:8]
        if ymd == "20261027":
            return {"stat": "很抱歉，沒有符合條件的資料"}       # 還沒公布
        return twse_payload(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}")

    def mirror(self, url: str):
        if "margin-stock-index.json" in url:
            return ["2026-10-22", "2026-10-21"]
        day = url.split("margin-stock-")[1][:10]
        return tpex_payload(day)

    def test_collect_twse_direct_and_tpex_mirror(self) -> None:
        now = datetime(2026, 10, 27, 22, 0, tzinfo=TW)   # 10/23 颱風假、10/26 光復節補假都休市，往前推就是 10/22、10/21
        r = module.collect_once(now, fetcher=self.fetcher, delay=0, mirror_fetcher=self.mirror)
        self.assertEqual(r["errors"], [])
        self.assertEqual([(x["date"], x["rows"]) for x in r["twse"]], [("2026-10-27", 0), ("2026-10-22", 2), ("2026-10-21", 2)])
        self.assertEqual([(x["date"], x["rows"]) for x in r["tpex"]], [("2026-10-22", 2), ("2026-10-21", 2)])   # 鏡像清單沒有的日子不去問
        self.assertEqual(module.stored_dates(), ["2026-10-22", "2026-10-21"])
        h = module.history(["2408", "6488"], "2026-10-01", "2026-10-22")
        self.assertEqual(h["2408"]["2026-10-22"]["balance"], 60136)
        self.assertEqual(h["6488"]["2026-10-21"], {"balance": 12958, "prev": 13071, "limit": 132028, "buy": 576, "sell": 670, "repay": 19})
        # 再跑一次：已經有的不重抓（公布前的 10/23 空的會再問一次）
        self.twse_calls.clear()
        module.collect_once(now, fetcher=self.fetcher, delay=0, mirror_fetcher=self.mirror)
        self.assertEqual(len(self.twse_calls), 1)

    def test_twse_blocked_stops_run(self) -> None:
        def blocked(url: str):
            raise RuntimeError("HTTP Error 307: Temporary Redirect")

        r = module.collect_once(datetime(2026, 10, 27, 22, 0, tzinfo=TW), fetcher=blocked, delay=0, mirror_fetcher=self.mirror)
        self.assertEqual(len(r["errors"]), 1)                                            # 被擋就先停，不連續打
        self.assertEqual(r["twse"], [])

    def test_before_publish_uses_previous_day(self) -> None:
        r = module.collect_once(datetime(2026, 10, 23, 15, 0, tzinfo=TW), fetcher=self.fetcher, delay=0, mirror_fetcher=lambda u: [])
        self.assertEqual([x["date"] for x in r["twse"]], ["2026-10-22", "2026-10-21", "2026-10-20"])

    def test_status_endpoints(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/margin-balance/status").json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
