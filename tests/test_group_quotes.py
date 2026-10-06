"""證交所報價代抓（tw-groups 自己抓不到時的備援）：分段同時抓、10 秒共用、部分失敗照回、全部失敗丟例外、端點。"""

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


def mis_fetcher(fail_codes: set[str] | None = None, calls: list[str] | None = None):
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
                items.append({"c": code, "n": "股" + code, "z": "101", "y": "100", "o": "100.5", "v": "800", "u": "110", "w": "90",
                              "d": "20261006", "t": "09:44:55"})
        return {"msgArray": items}

    return fetch


class GroupQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._group_quotes_cache.update(key=None, at=0.0, payload=None)

    def tearDown(self) -> None:
        module._group_quotes_cache.update(key=None, at=0.0, payload=None)
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
        self.assertIs(again, payload)                                     # 10 秒內同一批：不再打證交所
        self.assertEqual(len(calls), 3)

    def test_partial_and_total_failure(self) -> None:
        codes = ",".join(str(1000 + i) for i in range(160))
        partial = module.group_quotes_payload(codes, fetcher=mis_fetcher(fail_codes={"1000"}))
        self.assertEqual((partial["failedChunks"], partial["missing"], len(partial["quotes"])), (1, 80, 80))
        module._group_quotes_cache.update(key=None, at=0.0, payload=None)
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
        module._group_quotes_cache.update(key=None, at=0.0, payload=None)
        with patch.object(module, "_default_fetcher", mis_fetcher(fail_codes={"2330"})):
            down = client.get("/api/hub/group-quotes?codes=2330,2317")
        self.assertEqual(down.status_code, 502)
        self.assertIn("證交所報價抓不到", down.json()["error"])


if __name__ == "__main__":
    unittest.main()
