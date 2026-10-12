"""證交所報價代抓（tw-groups 首頁的報價中心）：分段同時抓、5 秒共用、30 秒內先給舊的背景再抓、每檔留最新一筆
（舊主機回的舊資料不蓋掉新的）、部分失敗照回、全部失敗丟例外、端點。"""

from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history as module
import database
import persistent_app


def mis_fetcher(fail_codes: set[str] | None = None, calls: list[str] | None = None, price: str = "101", clock: str = "09:44:55"):
    """假的證交所：每檔上市、昨收 100、成交 101（2330 沒成交只剩委買 = 漲停價 110）；fail_codes 裡的代號那一段整段失敗。"""
    lock = threading.Lock()

    def fetch(url: str):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        channels = query["ex_ch"][0].split("|")
        codes = [c.split("_")[1].replace(".tw", "") for c in channels if c.startswith("tse_")]
        with lock:
            if calls is not None:
                calls.append(",".join(codes))
        if fail_codes and fail_codes & set(codes):
            raise TimeoutError("timed out")
        items = []
        for code in codes:
            if code == "2330":
                items.append({"c": code, "n": "台積電", "z": "-", "b": "110_109.5_", "a": "-", "y": "100", "o": "102", "v": "5000",
                              "u": "110", "w": "90", "d": "20261006", "t": "09:45:00"})
            else:
                items.append({"c": code, "n": "股" + code, "z": price, "y": "100", "o": "100.5", "v": "800", "u": "110", "w": "90",
                              "d": "20261006", "t": clock})
        return {"msgArray": items}

    return fetch


class GroupQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._reset_group_quotes()

    def tearDown(self) -> None:
        module._reset_group_quotes()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_quotes_fields_and_cache(self) -> None:
        calls: list[str] = []
        codes = ",".join(str(1000 + i) for i in range(170)) + ",2330, 2330 ,bad!,12"
        payload = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(calls), 3)                                   # 171 檔 → 80／80／11 三段
        self.assertEqual((payload["missing"], payload["failedChunks"]), (0, 0))
        self.assertEqual((payload["quoteDate"], payload["quoteTime"]), ("2026-10-06", "09:45:00"))
        q = payload["quotes"]["1000"]
        self.assertEqual((q["price"], q["prevClose"], q["change"], q["open"], q["volume"], q["name"]), (101.0, 100.0, 1.0, 100.5, 800, "股1000"))
        self.assertAlmostEqual(q["changePercent"], 1.0)
        tsmc = payload["quotes"]["2330"]
        self.assertEqual((tsmc["price"], tsmc["limitUp"], tsmc["name"]), (110.0, True, "台積電"))   # 沒成交：用委買（漲停鎖死）
        again = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls))
        self.assertIs(again, payload)                                     # 5 秒內同一批：不再打證交所
        self.assertEqual(len(calls), 3)

    def test_stale_while_refresh_and_keep_newest(self) -> None:
        codes = "1000,1001"
        first = module.group_quotes_payload(codes, fetcher=mis_fetcher(price="101", clock="09:45:10"))
        key = "1000,1001"
        at, payload = module._group_quotes_cache["payloads"][key]
        module._group_quotes_cache["payloads"][key] = (at - 10, payload)     # 10 秒前的：先給舊的、背景抓
        calls: list[str] = []
        got = module.group_quotes_payload(codes, fetcher=mis_fetcher(calls=calls, price="105", clock="09:45:30"))
        self.assertIs(got, first)
        module._group_quotes_cache["threads"].get(key) and module._group_quotes_cache["threads"][key].join(5)
        for _ in range(50):
            if module._group_quotes_cache["payloads"][key][1] is not first:
                break
            threading.Event().wait(0.05)
        fresh = module.group_quotes_payload(codes, fetcher=mis_fetcher())
        self.assertEqual((fresh["quotes"]["1000"]["price"], fresh["quoteTime"]), (105.0, "09:45:30"))
        self.assertEqual(len(calls), 1)
        # 舊主機回比較舊的資料（09:44:00、價格 99）：不蓋掉 09:45:30 那筆
        at, payload = module._group_quotes_cache["payloads"][key]
        module._group_quotes_cache["payloads"][key] = (at - 60, payload)     # 超過 30 秒：等這次抓完
        older = module.group_quotes_payload(codes, fetcher=mis_fetcher(price="99", clock="09:44:00"))
        self.assertEqual((older["quotes"]["1000"]["price"], older["quoteTime"]), (105.0, "09:45:30"))
        self.assertIsNot(older, fresh)
        # 跨日：1001 那段今天沒抓到，不拿昨天那筆冒充今天
        def next_day(url: str):
            data = mis_fetcher(price="120", clock="09:01:00")(url)
            data["msgArray"] = [dict(i, d="20261007") for i in data["msgArray"] if i["c"] == "1000"]
            return data
        module._group_quotes_cache["payloads"].clear()
        nd = module.group_quotes_payload(codes, fetcher=next_day)
        self.assertEqual((sorted(nd["quotes"]), nd["quoteDate"], nd["missing"]), (["1000"], "2026-10-07", 1))

    def test_partial_and_total_failure(self) -> None:
        codes = ",".join(str(1000 + i) for i in range(160))
        partial = module.group_quotes_payload(codes, fetcher=mis_fetcher(fail_codes={"1000"}))
        self.assertEqual((partial["failedChunks"], partial["missing"], len(partial["quotes"])), (1, 80, 80))
        module._reset_group_quotes()
        with self.assertRaises(RuntimeError):
            module.group_quotes_payload(codes, fetcher=mis_fetcher(fail_codes={"1000", "1080"}))
        with self.assertRaises(ValueError):
            module.group_quotes_payload(" ,bad!")

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/group-quotes?codes=").status_code, 400)
        with patch.object(module, "_default_fetcher", mis_fetcher()):
            ok = client.get("/api/hub/group-quotes?codes=2330,2317")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(sorted(ok.json()["quotes"]), ["2317", "2330"])
        module._reset_group_quotes()
        with patch.object(module, "_default_fetcher", mis_fetcher(fail_codes={"2330"})):
            down = client.get("/api/hub/group-quotes?codes=2330,2317")
        self.assertEqual(down.status_code, 502)
        self.assertIn("證交所報價抓不到", down.json()["error"])


if __name__ == "__main__":
    unittest.main()
