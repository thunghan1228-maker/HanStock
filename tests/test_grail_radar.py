"""飆股雷達：條件特徵、時間點排程、盤中（MIS 報價組今天的K棒）與收盤計算、頁面資料。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import grail_radar as module
import heilong_backtest

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

    def test_three_day_dragon_needs_launch_two_days_ago(self) -> None:
        """三日飛龍（第二版）：兩天前漲 4% 以上創 60 日新高，之後兩天整理。"""
        history = uptrend()
        last = history[-1][3]

        def days_after(launch: float) -> list[tuple[float, float, float, float, int]]:
            a = last * (1 + launch)
            b = a * 0.99
            c = b * 1.005
            return [(last, a * 1.005, last * 0.998, a, 6000), (a, a * 1.0, b * 0.995, b, 3000), (b, c * 1.004, b * 0.997, c, 2500)]

        o, h, l, c, v = columns(history + days_after(0.06))
        features = module.compute_features(o, h, l, c, v)
        self.assertTrue(features["nh60_2"])
        self.assertGreaterEqual(features["launch_chg"], 4)
        self.assertIn("fly3", module.evaluate(features))
        o, h, l, c, v = columns(history + days_after(0.02))   # 兩天前只漲 2%：不算發動
        self.assertNotIn("fly3", module.evaluate(module.compute_features(o, h, l, c, v)))


class SlotTests(unittest.TestCase):
    def at(self, hhmmss: str) -> datetime:
        hour, minute, second = (int(x) for x in hhmmss.split(":"))
        return datetime(2026, 10, 7, hour, minute, second, tzinfo=TW)

    def test_due_slots_window(self) -> None:
        self.assertEqual(module.ALL_SLOTS, ["11:50", "12:50", "13:10", "13:35", "14:50"])
        self.assertEqual(module.due_slots(self.at("11:49:50"), set()), [])
        self.assertEqual(module.due_slots(self.at("11:50:10"), set()), ["11:50"])
        self.assertEqual(module.due_slots(self.at("11:50:20"), {"11:50"}), [])
        # 盤中時間點過了 8 分鐘不補（報價已經是後來的）
        self.assertEqual(module.due_slots(self.at("11:59:00"), set()), [])
        self.assertEqual(module.due_slots(self.at("12:50:05"), {"11:50"}), ["12:50"])
        self.assertEqual(module.due_slots(self.at("13:10:30"), {"11:50", "12:50"}), ["13:10"])
        # 收盤後的時間點用最後報價，晚一點也補
        self.assertEqual(module.due_slots(self.at("16:00:00"), set()), ["13:35", "14:50"])

    def test_every_logic_runs_ten_minutes_before_zhuang(self) -> None:
        """2026-10-08 使用者：以莊爸的時點為主、全部提早 10 分鐘——波段（穿山鱷龍、飛龍戰法）13:00／15:00 → 12:50／14:50，
        隔日沖（黑龍短沖含 R劍、黑飛舞家族）12:00／13:20／13:45 → 11:50／13:10／13:35。"""
        self.assertEqual(len(module.LOGICS), 15)
        swing = {logic["key"] for logic in module.LOGICS if logic["kind"] == "波段"}
        self.assertEqual(swing, {"cross2022", "breakred", "crossconv", "fly3", "flyburst", "flybreak", "red3"})
        for logic in module.LOGICS:
            self.assertTrue(logic["desc"] and logic["calibration"])
            self.assertEqual(logic["times"], ["12:50", "14:50"] if logic["kind"] == "波段" else ["11:50", "13:10", "13:35"])


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._history_cache.update({"key": None, "bars": None, "universe": None, "shares": None, "scores": None})
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

        # 均線分數用前一交易日收盤那一版（黑龍表 score2）：昨天 13 分、前天 7 分 → 名單上是 13
        with database.get_connection() as connection:
            heilong_backtest._schema(connection)
            insert = "INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, score2) VALUES (?, ?, 1, 1, 1, 1, 1, ?)"
            connection.executemany(insert, [(self.days[-1], "2368", 13), (self.days[-2], "2368", 7)])
        now = today.replace(hour=11, minute=50, second=10, tzinfo=TW)
        result = module.run_slot("11:50", now=now, fetcher=fetcher)
        self.assertEqual(result["counts"]["rsword"], 1)
        self.assertIn("tse_2368.tw", requested[0])
        self.assertNotIn("0050", requested[0])   # ETF 不算
        payload = module.day_payload(today.date().isoformat())
        run = payload["runs"]["rsword"]["11:50"]
        self.assertEqual(run["n"], 1)
        self.assertEqual(run["stocks"][0]["c"], "2368")
        self.assertEqual(run["stocks"][0]["n"], "金像電")
        self.assertEqual(run["stocks"][0]["m"], 13)
        self.assertEqual(run["source"], "mis")
        self.assertEqual(payload["closeSlot"], "收盤")
        self.assertEqual(len(payload["logics"]), 15)

    def test_trading_day_morning_opens_on_today_with_next_slot(self) -> None:
        """2026-10-08 使用者：早上打開停在昨天。交易日預設看今天（還沒有名單），告訴頁面下一輪幾點。"""
        module._save(self.days[-1], module.CLOSE_SLOT, {"rsword": []}, "official")
        morning = datetime(2026, 10, 8, 9, 30, tzinfo=TW)
        with patch.object(module, "_now", return_value=morning):
            payload = module.day_payload()
            self.assertEqual(payload["date"], "2026-10-08")
            self.assertEqual(payload["dates"][0], "2026-10-08")
            self.assertIn(self.days[-1], payload["dates"])
            self.assertEqual(payload["runs"], {})
            self.assertEqual(payload["nextSlot"], "11:50")
            self.assertIsNone(module.day_payload(self.days[-1])["nextSlot"])   # 看往日不給下一輪
        with patch.object(module, "_now", return_value=datetime(2026, 10, 11, 10, 0, tzinfo=TW)):   # 星期日：照舊看最近有資料那天
            self.assertEqual(module.day_payload()["date"], self.days[-1])
        self.assertEqual(module._next_slot(datetime(2026, 10, 8, 13, 10, tzinfo=TW)), "13:35")
        self.assertEqual(module._next_slot(datetime(2026, 10, 8, 14, 0, tzinfo=TW)), "14:50")
        self.assertEqual(module._next_slot(datetime(2026, 10, 8, 15, 0, tzinfo=TW)), "收盤")

    def test_payload_hides_runs_from_old_time_points(self) -> None:
        day = self.days[-1]
        module._save(day, "13:20", {"rsword": [{"c": "2368"}]}, "mis")    # 改時間點以前的盤中輪次
        module._save(day, "13:10", {"rsword": []}, "mis")
        self.assertEqual(set(module.day_payload(day)["runs"]["rsword"]), {"13:10"})

    def test_recompute_also_redoes_older_saved_close_lists(self) -> None:
        """條件改版：存著的「收盤」名單（就算超過回推天數）全部用新條件重算。"""
        old = self.days[-30]
        module._save(old, module.CLOSE_SLOT, {"rsword": [{"c": "9999"}]}, "official")
        with patch.object(module, "_day_complete", return_value=True):
            module.backfill_close(1, today=datetime.fromisoformat(self.days[-1]).date())
            self.assertEqual(module.day_payload(old)["runs"]["rsword"]["收盤"]["n"], 1)      # 沒要重算就不動
            result = module.backfill_close(1, today=datetime.fromisoformat(self.days[-1]).date(), recompute=True)
        self.assertIn(old, result["filled"])
        self.assertEqual(module.day_payload(old)["runs"]["rsword"]["收盤"]["n"], 0)

    def test_close_waits_for_complete_official_bars(self) -> None:
        self.assertIn("skipped", module.run_close(self.days[-1]))
        result = module.run_close(self.days[-1], force=True)
        self.assertEqual(result["stocks"], 1)
        payload = module.day_payload(self.days[-1])
        self.assertEqual(set(payload["runs"]), {logic["key"] for logic in module.LOGICS})
        self.assertIn(self.days[-1], payload["dates"])


if __name__ == "__main__":
    unittest.main()
