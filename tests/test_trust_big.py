"""首頁「大戶＋投信同買／同賣」：老師版大戶那天淨額跟投信買賣超同方向的，照合計金額排。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history
import chips_daily as module
import database
import main_force_store
import persistent_app


def inst(code, name, trust_lots):
    return {"code": code, "name": name, "foreign": 0, "trust": trust_lots * 1000, "dealer": 0, "total": trust_lots * 1000}


class TrustBigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        main_force_store._table_ready_path = None
        main_force_store._ensure_table()
        self.codes_patch = patch.object(module, "industry_group_codes", return_value=frozenset({"2330"}))
        self.codes_patch.start()
        self.groups_patch = patch.object(brew_launch_history, "STOCK_GROUPS", {"半導體": [("2330", "台積電")]})
        self.groups_patch.start()
        brew_launch_history._group_by_code.clear()
        module._trust_big_cache.clear()
        module.save_institutional("2026-11-20", "TSE", [
            inst("2330", "台積電", 500), inst("2603", "長榮", 40), inst("3037", "欣興", -300), inst("2368", "金像電", -10),
            inst("1101", "台泥", 5), inst("0050", "元大台灣50", 999)], "twse")
        module.save_institutional("2026-11-20", "OTC", [inst("6488", "環球晶", 200), inst("6182", "合晶", 100)], "mirror")
        bars = [   # (代號, 日期, bar_ts, 老師版大戶買, 賣, 有沒有老師版)
            ("2330", "2026-11-20", 1, 25e8, 10e8, 1), ("2330", "2026-11-20", 2, 5e8, 0, 1),   # +20 億
            ("2603", "2026-11-20", 3, 1e8, 3e8, 1),                                            # 大戶賣、投信買：不算
            ("3037", "2026-11-20", 4, 1e8, 5e8, 1),                                            # 同賣 −4 億
            ("2368", "2026-11-20", 5, 2e8, 2.5e8, 1),                                          # 同賣 −0.5 億
            ("6488", "2026-11-20", 6, 3e8, 2e8, 1),                                            # 上櫃同買 +1 億
            ("6182", "2026-11-20", 7, 9e8, 0, 1), ("6182", "2026-11-20", 8, 0, 0, 0),          # 只有半天老師版：不算
            ("0050", "2026-11-20", 9, 50e8, 0, 1),                                             # ETF 不算
            ("2330", "2026-11-19", 10, 1e8, 0, 1),
        ]
        with database.get_connection() as c:
            c.executemany("INSERT INTO main_force_bars (stock_code, trade_date, interval, bar_ts, updated_at, big_buy_amount, big_sell_amount, big_available) VALUES (?, ?, '5m', ?, 'x', ?, ?, ?)", bars)
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, ?, 1000)", [
                ("2330", "2026-11-19T00:00:00", 1000), ("2330", "2026-11-20T00:00:00", 1020),
                ("6488", "2026-11-19T00:00:00", 520), ("6488", "2026-11-20T00:00:00", 500),
                ("3037", "2026-11-20T00:00:00", 200), ("2368", "2026-11-20T00:00:00", 100),
            ])

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        module._trust_big_cache.clear()
        self.groups_patch.stop()
        self.codes_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        main_force_store._table_ready_path = None

    def test_same_direction_ranked_by_combined_amount(self) -> None:
        data = module.trust_big_rank()
        self.assertEqual((data["status"], data["date"], data["markets"]), ("ok", "2026-11-20", ["OTC", "TSE"]))
        self.assertEqual([r["code"] for r in data["buy"]], ["2330", "6488"])
        self.assertEqual([r["code"] for r in data["sell"]], ["3037", "2368"])
        self.assertEqual(data["counts"], {"buy": 2, "sell": 2})
        tsmc = data["buy"][0]
        self.assertEqual((tsmc["trust"], tsmc["trustAmt"], tsmc["big"], tsmc["total"]), (500, 5.1, 20.0, 25.1))   # 500 張 × 1020 元＝5.1 億
        self.assertEqual((tsmc["chg"], tsmc["group"], tsmc["mkt"]), (2.0, "半導體", "TSE"))
        self.assertEqual((data["buy"][1]["mkt"], data["buy"][1]["total"]), ("OTC", 2.0))
        self.assertEqual((data["sell"][0]["trust"], data["sell"][0]["total"]), (-300, -4.6))
        self.assertEqual(module.trust_big_rank(top=1)["buy"][0]["code"], "2330")
        self.assertEqual(len(module.trust_big_rank(top=1)["buy"]), 1)

    def test_falls_back_to_day_with_big_data_and_endpoint(self) -> None:
        module.save_institutional("2026-11-23", "TSE", [inst("2330", "台積電", 100)], "twse")   # 法人進來了、大戶資料還沒有
        module._trust_big_cache.clear()
        self.assertEqual(module.trust_big_rank()["date"], "2026-11-20")
        client = TestClient(persistent_app.app)
        body = client.get("/api/hub/trust-big?top=5").json()
        self.assertEqual((body["status"], body["date"], [r["code"] for r in body["buy"]]), ("ok", "2026-11-20", ["2330", "6488"]))
        self.assertEqual(client.get("/api/hub/trust-big?date=bad").status_code, 422)
        self.assertEqual(client.get("/api/hub/trust-big?top=2").status_code, 422)
        self.assertEqual(client.get("/api/hub/trust-big?date=2026-01-01").json()["status"], "empty")

    def test_empty(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM institutional_daily")
        module._trust_big_cache.clear()
        self.assertEqual(module.trust_big_rank()["status"], "empty")


if __name__ == "__main__":
    unittest.main()
