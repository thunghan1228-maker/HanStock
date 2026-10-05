"""自選股：同步碼存取、版本號避免兩台電腦互相蓋掉、清單格式整理、端點。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
import watchlist_store as module


class WatchlistStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_key_rules(self) -> None:
        self.assertEqual(module.normalize_key("  Judy-2026 "), "judy-2026")
        self.assertEqual(module.normalize_key("我的自選股2026"), "我的自選股2026")
        for bad in ("", "abc", "has space", "semi;colon", "x" * 41, None):
            with self.assertRaises(module.WatchlistError):
                module.normalize_key(bad)

    def test_sanitize_cleans_items_and_groups(self) -> None:
        raw = {"groups": [
            {"id": "hold", "name": "  持股  ", "items": [
                {"code": "2330", "note": "  長抱 ", "addedAt": "2026-10-05", "above": "1500", "below": -3, "ma": 10, "junk": 1},
                {"code": "2330"},                      # 重複
                {"code": "abc"},                       # 不是代號
                {"code": "6669a"},                     # 小寫轉大寫還是不合格式（6 碼以內英數字）的會被收下
                "2317",                                # 格式不對
            ]},
            {"id": "hold", "name": "", "items": [{"code": "3707", "ma": 7, "lots": 2}]},   # id 重複、名字空白
            "garbage",
        ], "settings": {"notifyLaunch": True, "bad key": 1, "level": 3, "tag": "x" * 80, "obj": {"a": 1}}}
        clean = module.sanitize(raw)
        first, second = clean["groups"]
        self.assertEqual((first["id"], first["name"]), ("hold", "持股"))
        self.assertEqual([i["code"] for i in first["items"]], ["2330", "6669A"])
        self.assertEqual(first["items"][0], {"code": "2330", "note": "長抱", "addedAt": "2026-10-05", "above": 1500.0, "ma": 10})
        self.assertNotEqual(second["id"], "hold")
        self.assertEqual((second["name"], second["items"]), ("自選", [{"code": "3707", "lots": 2.0}]))   # ma 7 不在選項裡
        self.assertEqual(clean["settings"], {"notifyLaunch": True, "level": 3, "tag": "x" * 50})
        with self.assertRaises(module.WatchlistError):
            module.sanitize({"groups": "not a list"})
        many_groups = module.sanitize({"groups": [{"name": f"g{i}", "items": [{"code": "2330"}]} for i in range(25)]})
        self.assertEqual(len(many_groups["groups"]), module.MAX_GROUPS)
        many_items = module.sanitize({"groups": [{"name": "g", "items": [{"code": f"{1000 + j}"} for j in range(250)]}]})
        self.assertEqual(len(many_items["groups"][0]["items"]), module.MAX_ITEMS)
        with self.assertRaises(module.WatchlistError):              # 備註寫太多，整份超過 64KB
            module.sanitize({"groups": [{"name": "g", "items": [{"code": f"{1000 + j}", "note": "長" * 200} for j in range(200)]}]})

    def test_save_load_and_version_conflict(self) -> None:
        empty = module.load("judy-2026")
        self.assertEqual((empty["version"], empty["data"]["groups"]), (0, []))
        data = {"groups": [{"id": "g1", "name": "持股", "items": [{"code": "2330"}]}]}
        first = module.save("judy-2026", data, 0)
        self.assertEqual((first["status"], first["version"]), ("ok", 1))
        self.assertEqual(module.load("JUDY-2026")["data"]["groups"][0]["items"], [{"code": "2330"}])   # 大小寫都同一份
        second = module.save("judy-2026", {"groups": [{"id": "g1", "name": "持股", "items": [{"code": "2330"}, {"code": "2317"}]}]}, 1)
        self.assertEqual(second["version"], 2)
        # 另一台電腦還拿著版本 1 就存：不能蓋掉，回最新的一份
        stale = module.save("judy-2026", {"groups": []}, 1)
        self.assertEqual((stale["status"], stale["version"]), ("conflict", 2))
        self.assertEqual([i["code"] for i in stale["data"]["groups"][0]["items"]], ["2330", "2317"])
        again = module.save("judy-2026", {"groups": []}, 0)        # 以為是第一次存，但已經有了
        self.assertEqual(again["status"], "conflict")
        self.assertEqual(module.load("other-code")["version"], 0)  # 不同同步碼互不相干

    def test_database_keeps_only_the_hash_of_the_key(self) -> None:
        module.save("secret-code-1", {"groups": []}, 0)
        with sqlite3.connect(self.db_path) as connection:
            keys = [row[0] for row in connection.execute("SELECT key_hash FROM watchlists")]
        self.assertEqual(len(keys), 1)
        self.assertNotIn("secret", keys[0])
        self.assertEqual(len(keys[0]), 64)

    def test_endpoints(self) -> None:
        client = TestClient(persistent_app.app)
        bad = client.post("/api/hub/watchlist/load", json={"key": "abc"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("同步碼", bad.json()["error"])
        ok = client.post("/api/hub/watchlist/save", json={"key": "judy-2026", "data": {"groups": [{"name": "觀察", "items": [{"code": "3707"}]}]}, "baseVersion": 0})
        self.assertEqual((ok.status_code, ok.json()["version"]), (200, 1))
        conflict = client.post("/api/hub/watchlist/save", json={"key": "judy-2026", "data": {"groups": []}, "baseVersion": 0})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["data"]["groups"][0]["items"], [{"code": "3707"}])
        loaded = client.post("/api/hub/watchlist/load", json={"key": "judy-2026"})
        self.assertEqual((loaded.status_code, loaded.json()["version"]), (200, 1))
        bad_data = client.post("/api/hub/watchlist/save", json={"key": "judy-2026", "data": [], "baseVersion": 1})
        self.assertEqual(bad_data.status_code, 400)


if __name__ == "__main__":
    unittest.main()
