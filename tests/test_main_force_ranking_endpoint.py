"""persistent_app.py 的 /api/hub/main-force/ranking：只排 stock_groups 官方族群裡的股票。
主力副圖收集器會追蹤任何開過圖的股票（含 00632R／00991A 這類 ETF），使用者 2026-09-23
要求排行不要出現 ETF——ETF 不在 43 個主群（含股期標的清單）裡。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import persistent_app
from database import initialize_database
from stock_groups import industry_group_codes
from main_force_store import save_main_force_bars
from otc_index import TW_TZ, taipei_trade_date

BASE_TS = 1_786_413_600_000  # 2026-08-11 10:00:00+08:00


class MainForceRankingEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        initialize_database()
        self.client = TestClient(persistent_app.app)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_ranking_excludes_codes_outside_official_groups(self):
        official = next(iter(sorted(industry_group_codes())))
        trade_date = taipei_trade_date(BASE_TS)
        for code, buy in ((official, 100), ("00632R", 900), ("00991A", 800)):
            save_main_force_bars(code, "5m", [{
                "ts": BASE_TS, "main_buy_volume": buy, "main_sell_volume": 0,
                "main_force_available": True,
            }])
        response = self.client.get("/api/hub/main-force/ranking", params={"trade_date": trade_date, "limit": 1000})
        self.assertEqual(response.status_code, 200)
        codes = [row["code"] for row in response.json()["ranking"]]
        self.assertEqual(codes, [official])
        self.assertEqual(response.json()["count"], 1)
        self.assertIsNone(response.json()["heldFrom"])

    def test_holds_previous_trading_day_until_next_open_when_today_has_no_data(self):
        # 使用者 2026-09-24：過午夜三個大戶力分頁的資料不能不見，要留到下一個交易日開盤前 15 分鐘。
        official = next(iter(sorted(industry_group_codes())))
        previous_date = taipei_trade_date(BASE_TS)
        save_main_force_bars(official, "5m", [{
            "ts": BASE_TS, "main_buy_volume": 100, "main_sell_volume": 0,
            "main_force_available": True,
        }])
        today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        with patch.object(persistent_app, "_should_hold_previous_ranking", return_value=True):
            held = self.client.get("/api/hub/main-force/ranking", params={"limit": 1000}).json()
        self.assertEqual(held["tradeDate"], previous_date)
        self.assertEqual(held["heldFrom"], today)
        self.assertEqual([row["code"] for row in held["ranking"]], [official])
        with patch.object(persistent_app, "_should_hold_previous_ranking", return_value=False):
            cleared = self.client.get("/api/hub/main-force/ranking", params={"limit": 1000}).json()
        self.assertEqual(cleared["tradeDate"], today)
        self.assertIsNone(cleared["heldFrom"])
        self.assertEqual(cleared["ranking"], [])
        # 明確指定日期就不沿用
        explicit = self.client.get("/api/hub/main-force/ranking", params={"trade_date": "2000-01-03"}).json()
        self.assertEqual(explicit["ranking"], [])

    def test_holder_timeline_endpoint(self):
        """2026-10-13：盤中大戶力每檔第一次進名單的時間；不在官方族群的（ETF）不算；開盤前沿用上一個交易日。"""
        official = next(iter(sorted(industry_group_codes())))
        trade_date = taipei_trade_date(BASE_TS)
        for code in (official, "00632R"):
            save_main_force_bars(code, "5m", [{
                "ts": BASE_TS, "main_buy_volume": 800, "main_sell_volume": 0, "main_buy_amount": 40_000_000,
                "total_amount": 100_000_000, "main_force_available": True,
            }])
        data = self.client.get("/api/hub/main-force/holder-timeline", params={"trade_date": trade_date}).json()
        self.assertEqual(list(data["stocks"]), [official])
        self.assertEqual(data["stocks"][official]["bull"]["firstTs"], BASE_TS)
        self.assertTrue(data["stocks"][official]["bull"]["still"])
        with patch.object(persistent_app, "_should_hold_previous_ranking", return_value=True):
            held = self.client.get("/api/hub/main-force/holder-timeline").json()
        self.assertEqual((held["tradeDate"], list(held["stocks"])), (trade_date, [official]))
        self.assertEqual(self.client.get("/api/hub/main-force/holder-timeline", params={"trade_date": "bad"}).status_code, 422)

    def test_window_flips_endpoint(self):
        official = next(iter(sorted(industry_group_codes())))
        trade_date = taipei_trade_date(BASE_TS)
        step = 5 * 60 * 1000
        bars = [{"ts": BASE_TS + i * step, "main_buy_volume": 10 if i < 6 else 60, "main_sell_volume": 40 if i < 6 else 10,
                 "main_buy_amount": (10 if i < 6 else 60) * 300_000, "main_sell_amount": (40 if i < 6 else 10) * 300_000,
                 "total_amount": 1_000_000_000, "main_force_available": True} for i in range(12)]
        save_main_force_bars(official, "5m", bars)
        data = self.client.get("/api/hub/main-force/window-flips", params={"trade_date": trade_date}).json()
        self.assertEqual([(e["code"], e["side"]) for e in data["events"]], [(official, "bull")])
        with patch.object(persistent_app, "_should_hold_previous_ranking", return_value=True):
            held = self.client.get("/api/hub/main-force/window-flips").json()
        self.assertEqual((held["tradeDate"], held["count"]), (trade_date, 1))

    def test_hold_rule_weekend_all_day_and_weekday_until_0845(self):
        hold = persistent_app._should_hold_previous_ranking
        self.assertTrue(hold(datetime(2026, 9, 26, 15, 0, tzinfo=TW_TZ)))   # 週六整天留
        self.assertTrue(hold(datetime(2026, 9, 27, 3, 0, tzinfo=TW_TZ)))    # 週日
        self.assertTrue(hold(datetime(2026, 9, 24, 8, 44, tzinfo=TW_TZ)))   # 平日 08:44 還留
        self.assertFalse(hold(datetime(2026, 9, 24, 8, 45, tzinfo=TW_TZ)))  # 08:45 起清空等開盤
        self.assertFalse(hold(datetime(2026, 9, 24, 13, 0, tzinfo=TW_TZ)))
