"""波段日報第一階段：個股技術面分析、報告整理（摘要／族群／三個精選）、每日保存與端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import chips_daily
import database
import main_force_store
import persistent_app
import swing_report as module
from trading_days import previous_trading_day

TW = timezone(timedelta(hours=8))
GROUPS = {"矽晶圓": [("6182", "合晶")], "設備股": [("6207", "雷科")], "半導體": [("2330", "台積電")]}


def trading_dates(last: str, count: int) -> list[str]:
    """往前數 count 個交易日（含 last），舊到新。"""
    out = [last]
    d = date.fromisoformat(last)
    while len(out) < count:
        d = previous_trading_day(d)
        out.append(d.isoformat())
    return list(reversed(out))


class AnalyzeBarsTests(unittest.TestCase):
    def test_short_history_and_cross(self) -> None:
        self.assertIsNone(module.analyze_bars([("d", 1, 1, 1, 1)] * 20))
        dates = trading_dates("2026-09-24", 241)
        bars = [(d, 101.0, 99.0, 100.0, 500) for d in dates[:-1]] + [(dates[-1], 105.0, 103.0, 104.0, 1000)]
        t = module.analyze_bars(bars)
        self.assertEqual(t["asOf"], "2026-09-24")
        self.assertTrue(t["crossedMa20"] and t["aboveMa20"])
        self.assertEqual(t["ma20"], 100.2)
        self.assertAlmostEqual(t["aboveMa20Pct"], 3.79, places=2)
        self.assertEqual((t["score"], t["prevScore"]), (15, 0))    # 最後一天拉高：短均線全部在長均線上面
        self.assertEqual(t["threeDayLow"], 99.0)
        self.assertEqual(t["changePct"], 4.0)
        self.assertFalse(t["ma60OverHead"])
        self.assertAlmostEqual(t["volumeRatio"], 1.67, places=2)

    def test_risks(self) -> None:
        t = {"ma60OverHead": True, "ma60": 53.93, "dev5Pct": 19.0, "limitUp": True}
        self.assertEqual(module._risks(t, {"inst5": -3.0, "instToday": -1.0}, True),
                         ["處置中", "季線 53.93 在頭上", "漲停後與 5 日線乖離 19%", "法人 5 日仍為賣超"])
        self.assertEqual(module._risks({"ma60OverHead": False, "dev5Pct": 2.0}, {"inst5": 10.0, "instToday": -2.0}, False), ["法人今天轉賣超"])


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        main_force_store._table_ready_path = None
        main_force_store._ensure_table()
        self.patches = [patch.object(m, "STOCK_GROUPS", GROUPS) for m in (module, brew_launch, brew_launch_history)]
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        dates = trading_dates("2026-09-24", 241)
        rows = []
        # 合晶：平盤 240 天，最後一天拉 4% 站上月線（真穿月線、均線分數 0→15）
        rows += [("6182", d + "T00:00:00", 100, 101, 99, 100, 500) for d in dates[:-1]] + [("6182", dates[-1] + "T00:00:00", 100, 105, 103, 104, 1000)]
        # 雷科：60 天緩跌，在月線下，日K不夠算不出均線分數
        rows += [("6207", d + "T00:00:00", 120 - i * 0.33, 121 - i * 0.33, 119 - i * 0.33, 120 - i * 0.33, 800) for i, d in enumerate(dates[-60:])]
        # 台積電：完全平盤
        rows += [("2330", d + "T00:00:00", 1000, 1001, 999, 1000, 20000) for d in dates]
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            c.executemany("INSERT INTO main_force_bars (stock_code, trade_date, interval, bar_ts, main_buy_volume, main_sell_volume, main_net_volume, main_buy_amount, main_sell_amount, main_net_amount, main_tick_count, updated_at, total_amount) VALUES (?, ?, '5m', ?, ?, ?, ?, ?, ?, ?, 1, 'x', ?)", [
                ("6182", "2026-09-23", 1, 800, 500, 300, 8.0e7, 5.0e7, 3.0e7, 2.0e9),
                ("6182", "2026-09-24", 2, 900, 400, 500, 9.0e7, 4.0e7, 5.0e7, 2.5e9),
            ])
        for d in dates[-5:]:   # 合晶法人連買 5 天（每天 100 萬股）、雷科連賣
            chips_daily.save_institutional(d, "OTC", [{"code": "6182", "name": "合晶", "foreign": 1_000_000, "trust": 0, "dealer": 0, "total": 1_000_000},
                                                      {"code": "6207", "name": "雷科", "foreign": -50_000, "trust": 0, "dealer": 0, "total": -50_000}], "test")

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        main_force_store._table_ready_path = None

    def test_build_report(self) -> None:
        r = module.build_report("2026-09-24", disposition_codes={"6207"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["date"], "2026-09-24")
        self.assertEqual((r["basis"]["stocks"], r["basis"]["withScore"], r["basis"]["hasInst"]), (3, 2, True))
        self.assertEqual(r["basis"]["instDates"][0], "2026-09-24")
        self.assertEqual(r["tiles"]["crossed"], 1)
        self.assertEqual(r["tiles"]["maJump"], 1)
        self.assertEqual(r["tiles"]["newFull"], 1)
        self.assertEqual(r["tiles"]["disposition"], 1)
        tech = r["picks"]["tech"]
        self.assertEqual([x["code"] for x in tech], ["6182"])
        self.assertEqual(tech[0]["tag"], "站上 +3.8%")
        self.assertEqual(tech[0]["defense"], {"ma20": 100.2, "threeDayLow": 99.0})
        self.assertEqual((tech[0]["name"], tech[0]["group"], tech[0]["hot"]), ("合晶", "矽晶圓", True))
        chips = r["picks"]["chips"]
        self.assertEqual([x["code"] for x in chips], ["6182"])
        self.assertEqual((chips[0]["inst5"], chips[0]["instStreak"], chips[0]["instToday"], chips[0]["tag"]), (5000.0, 5, 1000.0, "法人連買 5 天"))
        self.assertEqual((chips[0]["mf5"], chips[0]["mfStreak"]), (800, 2))
        self.assertEqual(chips[0]["risks"], [])
        ma = r["picks"]["ma"]
        self.assertEqual([x["code"] for x in ma], ["6182"])
        self.assertEqual((ma[0]["tag"], ma[0]["health"]), ("均線 0→15", 7))
        groups = {g["name"]: g for g in r["groups"]}
        self.assertEqual([g["name"] for g in r["groups"]], ["矽晶圓", "半導體", "設備股"])   # 依當天平均漲跌幅排
        self.assertEqual((groups["矽晶圓"]["aboveMa20"], groups["矽晶圓"]["members"], groups["矽晶圓"]["inst5"], groups["矽晶圓"]["rank"]), (1, 1, 5000.0, 1))
        self.assertEqual(groups["矽晶圓"]["judge"], "資金已進、型態已翻，只挑籌碼連續上榜者")
        self.assertEqual(groups["設備股"]["judge"], "資金未進、型態未翻：先看族群再看個股")
        self.assertIn("資金主軸在已站上月線的族群：矽晶圓", r["summary"][0])
        self.assertIn("籌碼面首選 合晶；技術面首選 合晶", r["summary"][1])
        self.assertIn("均線新滿分 合晶", r["summary"][2])
        self.assertTrue(r["summary"][-1].startswith("風險提示"))
        self.assertEqual(r["counts"], {"chips": 1, "tech": 1, "techNear": 0, "ma": 1, "full": 1})
        self.assertEqual([x["tag"] for x in r["picks"]["full"]], ["0→15"])
        self.assertEqual(r["notes"]["jumpTop"], [{"code": "6182", "name": "合晶", "from": 0, "to": 15}])
        self.assertEqual((r["notes"]["sustained"], r["notes"]["prevDate"]), ([], None))   # 還沒有前一份報告

    def test_sustained_uses_previous_report(self) -> None:
        with patch.object(module, "_disposition_codes", return_value=set()):
            module.refresh_report("2026-09-23")
            r = module.build_report("2026-09-24")
            self.assertEqual(r["notes"]["prevDate"], "2026-09-23")
            # 09/23 那份：合晶法人連買 4 天已在籌碼面精選；今天仍在月線上、均線 15 → 續強確認
            self.assertEqual(r["notes"]["sustained"], [{"code": "6182", "name": "合晶", "score": 15}])
            fake = {"date": "2026-09-23", "generatedAt": "x", "picks": {"tech": [{"code": "6207"}]}}
            module.save_report(fake)
            r = module.build_report("2026-09-24")
            self.assertEqual(r["notes"]["sustained"], [])   # 雷科在月線下、沒分數，不算續強

    def test_save_load_and_lookup(self) -> None:
        with patch.object(module, "_disposition_codes", return_value=set()):
            latest = module.swing_report(None)
            self.assertEqual((latest["status"], latest["date"], latest["dates"]), ("ok", "2026-09-24", ["2026-09-24"]))
            past = module.swing_report("2026-09-23")   # 沒存過、但有日K：當場算
            self.assertEqual((past["status"], past["date"]), ("ok", "2026-09-23"))
            self.assertEqual(past["picks"]["tech"], [])   # 那天合晶還是平盤
            self.assertEqual(module.report_dates(), ["2026-09-24", "2026-09-23"])
            self.assertEqual(module.swing_report("2020-01-01")["status"], "empty")
            result = module.run_once(datetime(2026, 9, 24, 15, 10, tzinfo=TW))
            self.assertEqual(result["date"], "2026-09-24")
            self.assertTrue(len(result["backfilled"]) >= 1)   # 更早幾天也補起來
            self.assertTrue(module.collector_status()["lastRunAt"].startswith("2026-09-24"))

    def test_endpoint(self) -> None:
        with patch.object(module, "_disposition_codes", return_value=set()):
            client = TestClient(persistent_app.app)
            self.assertEqual(client.get("/api/hub/swing-report", params={"date": "bad"}).status_code, 422)
            body = client.get("/api/hub/swing-report").json()
            self.assertEqual((body["status"], body["date"]), ("ok", "2026-09-24"))
            self.assertEqual([x["code"] for x in body["picks"]["chips"]], ["6182"])
            self.assertIn("dates", body)
            self.assertEqual(client.post("/api/hub/swing-report/refresh").json()["status"], "ok")

    def test_refresh_window(self) -> None:
        self.assertTrue(module._in_refresh_window(datetime(2026, 9, 24, 15, 10, tzinfo=TW)))
        self.assertFalse(module._in_refresh_window(datetime(2026, 9, 24, 14, 0, tzinfo=TW)))
        self.assertFalse(module._in_refresh_window(datetime(2026, 9, 25, 16, 0, tzinfo=TW)))   # 中秋節


if __name__ == "__main__":
    unittest.main()
