"""醞釀／發動每日保存：醞釀快照一天一份、盤中發動一檔一天記一次、不在盤中只存快照、歷史端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch_history as module
import database
import persistent_app

TW = timezone(timedelta(hours=8))
RULES = {"maPeriods": [5, 10, 20, 60, 120, 240], "launchMinScore": 11, "turnoverMinPct": 5.0, "volumeRatioMin": 1.5}
BULL = {5: 0.99, 10: 0.97, 20: 0.95, 60: 0.85, 120: 0.75, 240: 0.6}
BEAR = {5: 0.9, 10: 0.95, 20: 1.0, 60: 1.05, 120: 1.1, 240: 1.2}


def _sums(ratio: dict[int, float], price: float = 100.0) -> dict[str, float]:
    return {str(p): r * price * p - price for p, r in ratio.items()}


def _info(**over):
    base = {"prevClose": 96.0, "boxHigh": 98.0, "boxLow": 90.0, "boxRangePct": 8.9, "maSpreadPct": 2.1, "score": 15,
            "maSums": _sums(BULL), "avgVol5": 1000.0, "sharesLots": 20000.0, "brewing": True}
    base.update(over)
    return base


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        self.groups_patch = patch.object(module, "STOCK_GROUPS", {"玻璃基板": [("6207", "雷科")], "矽晶圓": [("3016", "嘉晶")], "金融股": [("2881", "富邦金")]})
        self.groups_patch.start()
        module._group_by_code.clear()
        self.payload = {"session": "2026-09-24", "rules": RULES, "stocks": {
            "6207": _info(boxHigh=124.0, maSums=_sums(BULL, 125.0)),
            "3016": _info(boxHigh=170.0, maSums=_sums(BULL, 160.0), brewing=False),
            "2881": _info(boxHigh=1.0, skipped=True),  # 金融股：不進醞釀快照、不算發動
        }}
        self.quotes = {
            "6207": {"price": 125.0, "prevClose": 114.0, "volume": 500, "quoteDate": "2026-09-24", "quoteTime": "10:00:00"},
            "3016": {"price": 160.0, "prevClose": 145.5, "volume": 500, "quoteDate": "2026-09-24", "quoteTime": "10:00:00"},
            "2881": {"price": 100.0, "prevClose": 90.0, "volume": 99999, "quoteDate": "2026-09-24", "quoteTime": "10:00:00"},
        }

    def tearDown(self) -> None:
        module._group_by_code.clear()
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _scan(self, hour=10, minute=0, day=24):
        return module.scan_once(now=datetime(2026, 9, day, hour, minute, tzinfo=TW), payload=self.payload, quotes=self.quotes)

    def test_snapshot_once_and_launch_once_per_day(self) -> None:
        result = self._scan()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["brewSnapshot"], 1)                    # 只有 6207 在醞釀（3016 不是、2881 金融股）
        self.assertEqual(result["codes"], ["6207"])                     # 3016 沒過箱頂、2881 金融股
        again = self._scan(10, 5)
        self.assertEqual(again["brewSnapshot"], 0)                      # 快照一天一份
        self.assertEqual(again["launched"], 0)                          # 發動一檔一天記一次
        self.assertEqual(again["launchedToday"], 1)
        day = module.history(date="2026-09-24")["days"]["2026-09-24"]
        self.assertEqual([r["code"] for r in day["brew"]], ["6207"])
        self.assertEqual(day["brew"][0]["name"], "雷科")
        self.assertEqual(day["brew"][0]["group"], "玻璃基板")
        self.assertEqual(day["brew"][0]["boxHigh"], 124.0)
        launch = day["launch"][0]
        self.assertEqual(launch["code"], "6207")
        self.assertEqual(launch["price"], 125.0)
        self.assertEqual(launch["score"], 15)
        self.assertTrue(launch["brewing"])
        self.assertTrue(launch["recordedAt"].startswith("2026-09-24T10:00"))
        self.assertAlmostEqual(launch["projTurnoverPct"], 10.0)         # 500 張 × 4 ÷ 20000 張

    def test_outside_window_stores_snapshot_but_not_launches(self) -> None:
        result = self._scan(14, 0)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["brewSnapshot"], 1)
        self.assertEqual(module.launched_codes("2026-09-24"), set())
        weekend = module.scan_once(now=datetime(2026, 9, 26, 10, 0, tzinfo=TW), payload=self.payload, quotes=self.quotes)
        self.assertEqual(weekend["status"], "skipped")

    def test_session_mismatch_does_not_record_launch(self) -> None:
        # 醞釀資料還是昨天的交易日（例如凌晨快取）就不能拿今天的報價記發動
        result = self._scan(day=25)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("不是今天", result["reason"])

    def test_history_endpoint(self) -> None:
        self._scan()
        client = TestClient(persistent_app.app)
        data = client.get("/api/hub/brew-launch/history?days=5").json()
        self.assertEqual(data["dates"], ["2026-09-24"])
        self.assertEqual(len(data["days"]["2026-09-24"]["launch"]), 1)
        self.assertIn("scan", data)
        self.assertEqual(client.get("/api/hub/brew-launch/history?date=2026-09-23").json()["days"]["2026-09-23"], {"brew": [], "launch": []})
        self.assertEqual(client.get("/api/hub/brew-launch/history?date=bad").status_code, 422)

    def _insert_bars(self, rows) -> None:
        with database.get_connection() as connection:
            connection.executemany(
                "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows
            )

    def _backfill(self, **kwargs):
        # compute_brew_launch 是 brew_launch 的：回補時用那天「之前」的日K重算醞釀，這裡直接給算好的 payload
        with patch("brew_launch.compute_brew_launch", side_effect=lambda *, session: dict(self.payload, session=session)), \
             patch("brew_launch.group_codes", return_value=["6207", "3016", "2881"]):
            return module.backfill_past_days(**kwargs)

    def test_backfill_past_day_from_daily_bars(self) -> None:
        # 保存功能上線前的日子：醞釀快照補回來，發動用收盤價回推（收盤過箱頂＋全天量夠），標 eod
        self._insert_bars([
            ("6207", "2026-09-23T00:00:00", 120, 121, 119, 120, 800),     # 9/23 只有一檔日K → 不完整，先不回推
            ("6207", "2026-09-24T00:00:00", 120, 126, 119, 125, 1200),    # 收盤 125 > 箱頂 124；1200 張 ÷ 20000 張 = 6% ≥ 5%
            ("3016", "2026-09-24T00:00:00", 150, 160, 150, 160, 500),     # 160 沒過箱頂 170
            ("2881", "2026-09-24T00:00:00", 90, 100, 90, 100, 99999),     # 金融股不算
        ])
        result = self._backfill(session="2026-09-25", days=3, now=datetime(2026, 9, 25, 0, 30, tzinfo=TW))
        self.assertEqual([(d["date"], d["status"]) for d in result["days"]], [("2026-09-24", "ok"), ("2026-09-23", "skipped")])
        self.assertEqual(result["days"][0]["brewAdded"], 1)
        self.assertEqual(result["days"][0]["launchAdded"], 1)
        self.assertIn("還不完整", result["days"][1]["reason"])
        day = module.history(date="2026-09-24")["days"]["2026-09-24"]
        self.assertEqual([r["code"] for r in day["brew"]], ["6207"])
        launch = day["launch"]
        self.assertEqual(len(launch), 1)
        self.assertEqual(launch[0]["code"], "6207")
        self.assertTrue(launch[0]["eod"])
        self.assertEqual(launch[0]["recordedAt"], "2026-09-24T13:30:00+08:00")
        self.assertEqual(launch[0]["price"], 125.0)
        self.assertAlmostEqual(launch[0]["projTurnoverPct"], 6.0)
        self.assertAlmostEqual(launch[0]["changePct"], round((125 / 96 - 1) * 100, 2))
        self.assertEqual(module.history(date="2026-09-23")["days"]["2026-09-23"], {"brew": [], "launch": []})
        # 再跑一次不重複
        again = self._backfill(session="2026-09-25", days=3, now=datetime(2026, 9, 25, 0, 30, tzinfo=TW))
        self.assertEqual((again["days"][0]["brewAdded"], again["days"][0]["launchAdded"]), (0, 0))

    def test_backfill_keeps_intraday_record_and_includes_session_day_after_1530(self) -> None:
        # 盤中已經記到的那筆（10:00、125 元）不會被收盤回推蓋掉；session 當天 15:30 前不回推、之後才算
        self._scan()  # 9/24 10:00 記到 6207 發動
        self._insert_bars([
            ("6207", "2026-09-24T00:00:00", 120, 130, 119, 128, 1500),
            ("3016", "2026-09-24T00:00:00", 150, 175, 150, 172, 3000),    # 收盤 172 > 箱頂 170、3000 ÷ 20000 = 15%：盤中沒掃到，收盤回推補上
            ("2881", "2026-09-24T00:00:00", 90, 100, 90, 100, 99999),
        ])
        before = self._backfill(session="2026-09-24", now=datetime(2026, 9, 24, 15, 0, tzinfo=TW))
        self.assertEqual(before["days"], [])
        after = self._backfill(session="2026-09-24", now=datetime(2026, 9, 24, 15, 30, tzinfo=TW))
        self.assertEqual(after["days"][0]["date"], "2026-09-24")
        self.assertEqual(after["days"][0]["brewAdded"], 0)       # 快照當天已經存過
        self.assertEqual(after["days"][0]["launchAdded"], 1)     # 只補 3016
        launch = {r["code"]: r for r in module.history(date="2026-09-24")["days"]["2026-09-24"]["launch"]}
        self.assertEqual(launch["6207"]["price"], 125.0)          # 盤中那筆保留
        self.assertFalse(launch["6207"].get("eod"))
        self.assertTrue(launch["6207"]["recordedAt"].startswith("2026-09-24T10:00"))
        self.assertTrue(launch["3016"]["eod"])
        self.assertEqual(launch["3016"]["price"], 172.0)

    def test_backfill_due_at_startup_then_daily_after_1530(self) -> None:
        module._state["backfillDate"] = None
        self.assertTrue(module._backfill_due(datetime(2026, 9, 24, 10, 0, tzinfo=TW)))
        with patch.object(module, "backfill_past_days", return_value={"days": []}):
            module._run_backfill(datetime(2026, 9, 24, 10, 0, tzinfo=TW))
            self.assertFalse(module._backfill_due(datetime(2026, 9, 24, 12, 0, tzinfo=TW)))   # 開機跑過，15:30 前不再跑
            self.assertTrue(module._backfill_due(datetime(2026, 9, 24, 15, 30, tzinfo=TW)))
            module._run_backfill(datetime(2026, 9, 24, 15, 30, tzinfo=TW))
            self.assertFalse(module._backfill_due(datetime(2026, 9, 24, 18, 0, tzinfo=TW)))   # 當天跑過
            self.assertTrue(module._backfill_due(datetime(2026, 9, 25, 15, 31, tzinfo=TW)))
        module._state["backfillDate"] = None

    def test_volume_factor_and_window(self) -> None:
        self.assertEqual(module.volume_factor("2026-09-24", "10:00:00", "2026-09-24"), 4.0)
        self.assertAlmostEqual(module.volume_factor("2026-09-24", "12:00:00", "2026-09-24"), 1.5)
        self.assertEqual(module.volume_factor("2026-09-23", "10:00:00", "2026-09-24"), 1.0)
        self.assertTrue(module.in_scan_window(datetime(2026, 9, 24, 13, 34, tzinfo=TW)))
        self.assertFalse(module.in_scan_window(datetime(2026, 9, 24, 13, 35, tzinfo=TW)))


if __name__ == "__main__":
    unittest.main()
