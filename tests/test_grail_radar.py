"""飆股雷達：條件特徵、時間點排程、盤中（MIS 報價組今天的K棒）與收盤計算、頁面資料。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import grail_radar as module

TW = module.TW_TZ


def uptrend(n: int = 150, start: float = 50.0, growth: float = 0.006, volume: int = 2000) -> list[tuple[float, float, float, float, int]]:
    """每天漲 0.6% 的紅K（開、高、低、收、量），舊到新。"""
    bars = []
    for k in range(n):
        close = start * (1 + growth) ** k
        bars.append((close * 0.998, close * 1.01, close * 0.99, close, volume))
    return bars


def columns(bars):
    return [list(col) for col in zip(*bars)]


def r_sword_today(prev_close: float, *, change: float = 0.04) -> tuple[float, float, float, float, int]:
    """創 20 日新高、上影 4%、收紅的 R劍。"""
    return (prev_close, prev_close * 1.08, prev_close * 0.995, prev_close * (1 + change), 3000)


class FeatureTests(unittest.TestCase):
    def test_r_sword_matches_and_respects_change_limit(self) -> None:
        history = uptrend()
        o, h, l, c, v = columns(history + [r_sword_today(history[-1][3])])
        features = module.compute_features(o, h, l, c, v)
        self.assertIsNotNone(features)
        self.assertEqual(features["hiago20"], 0)
        self.assertGreaterEqual(features["ushadow"], 2.2)
        self.assertIn("rsword", module.evaluate(features))
        # 漲超過 5% 就不是 R劍（上影一樣長）
        o, h, l, c, v = columns(history + [(history[-1][3], history[-1][3] * 1.10, history[-1][3] * 0.995, history[-1][3] * 1.06, 3000)])
        self.assertNotIn("rsword", module.evaluate(module.compute_features(o, h, l, c, v)))

    def test_short_history_is_skipped(self) -> None:
        o, h, l, c, v = columns(uptrend(100))
        self.assertIsNone(module.compute_features(o, h, l, c, v))

    def test_ma_score_needs_240_bars(self) -> None:
        o, h, l, c, v = columns(uptrend(150))
        self.assertIsNone(module.compute_features(o, h, l, c, v)["ma_score"])
        o, h, l, c, v = columns(uptrend(260))
        self.assertEqual(module.compute_features(o, h, l, c, v)["ma_score"], 15)   # 一路漲：短均線全在長均線上


class SlotTests(unittest.TestCase):
    def at(self, hhmmss: str) -> datetime:
        hour, minute, second = (int(x) for x in hhmmss.split(":"))
        return datetime(2026, 10, 7, hour, minute, second, tzinfo=TW)

    def test_due_slots_window(self) -> None:
        self.assertEqual(module.due_slots(self.at("09:44:50"), set()), [])
        self.assertEqual(module.due_slots(self.at("09:45:10"), set()), ["09:45"])
        self.assertEqual(module.due_slots(self.at("09:45:20"), {"09:45"}), [])
        # 盤中時間點過了 8 分鐘不補（報價已經是後來的）
        self.assertEqual(module.due_slots(self.at("09:54:00"), set()), [])
        # 13:25 是收盤集合競價開始，提早 30 秒算（13:20 那一輪還在 8 分鐘內，也還沒算就一起算）
        self.assertEqual(module.due_slots(self.at("13:24:35"), set()), ["13:20", "13:25"])
        self.assertEqual(module.due_slots(self.at("13:24:35"), {"13:20"}), ["13:25"])
        # 收盤後的時間點用最後報價，晚一點也補
        self.assertEqual(module.due_slots(self.at("16:00:00"), set()), ["13:45", "14:00", "15:00"])

    def test_every_logic_has_slots_and_description(self) -> None:
        self.assertEqual(len(module.LOGICS), 15)
        for logic in module.LOGICS:
            self.assertTrue(logic["times"] and logic["desc"] and logic["calibration"])
            for slot in logic["times"]:
                self.assertIn(slot, module.ALL_SLOTS)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._history_cache.update({"key": None, "bars": None, "universe": None, "shares": None})
        self.history = uptrend()
        days = []
        cursor = datetime(2026, 4, 1)
        while len(days) < len(self.history):
            if module.is_trading_day(cursor.date()):
                days.append(cursor.date().isoformat())
            cursor += timedelta(days=1)
        self.days = days
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, ?)",
                                   [("2368", "金像電", "TSE", "x"), ("0050", "元大台灣50", "TSE", "x")])
            rows = [("2368", f"{d}T00:00:00+00:00", *bar) for d, bar in zip(days, self.history)]
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_intraday_slot_uses_live_quote_as_today_bar(self) -> None:
        today = datetime.fromisoformat(self.days[-1]) + timedelta(days=1)
        while not module.is_trading_day(today.date()):
            today += timedelta(days=1)
        o, h, l, c, v = r_sword_today(self.history[-1][3])
        requested = []

        def fetcher(url: str):
            requested.append(url)
            return {"msgArray": [{"c": "2368", "o": f"{o:.2f}", "h": f"{h:.2f}", "l": f"{l:.2f}", "z": f"{c:.2f}", "v": str(v),
                                  "y": f"{self.history[-1][3]:.2f}", "d": today.strftime("%Y%m%d"), "t": "11:30:05"}]}

        now = today.replace(hour=11, minute=30, second=10, tzinfo=TW)
        result = module.run_slot("11:30", now=now, fetcher=fetcher)
        self.assertEqual(result["counts"]["rsword"], 1)
        self.assertIn("tse_2368.tw", requested[0])
        self.assertNotIn("0050", requested[0])   # ETF 不算
        payload = module.day_payload(today.date().isoformat())
        run = payload["runs"]["rsword"]["11:30"]
        self.assertEqual(run["n"], 1)
        self.assertEqual(run["stocks"][0]["c"], "2368")
        self.assertEqual(run["stocks"][0]["n"], "金像電")
        self.assertEqual(run["source"], "mis")
        self.assertEqual(payload["closeSlot"], "收盤")
        self.assertEqual(len(payload["logics"]), 15)

    def test_close_waits_for_complete_official_bars(self) -> None:
        self.assertIn("skipped", module.run_close(self.days[-1]))
        result = module.run_close(self.days[-1], force=True)
        self.assertEqual(result["stocks"], 1)
        payload = module.day_payload(self.days[-1])
        self.assertEqual(set(payload["runs"]), {logic["key"] for logic in module.LOGICS})
        self.assertIn(self.days[-1], payload["dates"])


if __name__ == "__main__":
    unittest.main()
