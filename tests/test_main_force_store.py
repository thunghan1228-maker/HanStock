import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from main_force_collector import collect_once
from main_force_store import (
    list_tracked_stock_codes,
    load_daily_main_force_net,
    load_main_force_bars,
    load_main_force_ranking,
    main_force_storage_status,
    prune_old_bars,
    purge_out_of_session_bars,
    save_main_force_bars,
)
from otc_index import taipei_trade_date

BASE_TS = 1_786_413_600_000  # 2026-08-11 10:00:00+08:00（盤中連續交易時段內）


class MainForceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_persists_multiple_days_without_zero_filling_missing_data(self):
        valid = {
            "ts": BASE_TS,
            "main_buy_volume": 12,
            "main_sell_volume": 7,
            "main_buy_amount": 1_200_000,
            "main_sell_amount": 700_000,
            "main_tick_count": 2,
            "main_force_available": True,
        }
        missing = {"ts": valid["ts"] + 60_000, "main_net_volume": 0}
        self.assertEqual(save_main_force_bars("2330", "1m", [valid, missing]), 1)
        rows = load_main_force_bars("2330", "1m", days=400)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["main_net_volume"], 5)
        self.assertEqual(main_force_storage_status()["stockCount"], 1)

    def test_rejects_bars_outside_regular_trading_session(self):
        # 09:00-13:30以外的bar（例如14:30盤後定價撮合被上游誤判成一根K棒）
        # 不能存進main_force_bars，否則MAX(bar_ts)會被撮合時間蓋掉，讓排行
        # 「最後更新時間」看起來像只有盤後資料、缺了整個盤中時段。
        after_hours_ts = BASE_TS - (10 * 60 * 60 * 1000) + (14 * 60 + 30) * 60 * 1000  # 同一天14:30
        pre_market_ts = BASE_TS - (10 * 60 * 60 * 1000) + 8 * 60 * 60 * 1000  # 同一天08:00
        saved = save_main_force_bars("2330", "5m", [
            {"ts": BASE_TS, "main_buy_volume": 10, "main_sell_volume": 0, "main_force_available": True},
            {"ts": after_hours_ts, "main_buy_volume": 999, "main_sell_volume": 0, "main_force_available": True},
            {"ts": pre_market_ts, "main_buy_volume": 999, "main_sell_volume": 0, "main_force_available": True},
        ])
        self.assertEqual(saved, 1)
        rows = load_main_force_bars("2330", "5m", days=400)
        self.assertEqual([row["ts"] for row in rows], [BASE_TS])

    def test_purge_out_of_session_bars_removes_only_dirty_rows(self):
        after_hours_ts = BASE_TS - (10 * 60 * 60 * 1000) + (14 * 60 + 30) * 60 * 1000  # 同一天14:30
        save_main_force_bars("2330", "5m", [
            {"ts": BASE_TS, "main_buy_volume": 10, "main_sell_volume": 0, "main_force_available": True},
        ])
        # 模擬修復前就已經寫進去的髒資料：直接寫DB，跳過現在會擋掉它的save_main_force_bars。
        with database.get_connection() as connection:
            connection.execute(
                """INSERT INTO main_force_bars
                    (stock_code, trade_date, interval, bar_ts, main_buy_volume, main_sell_volume,
                     main_net_volume, main_buy_amount, main_sell_amount, main_net_amount,
                     main_tick_count, updated_at)
                    VALUES ('2330', ?, '5m', ?, 999, 0, 999, 0, 0, 0, 0, ?)""",
                (taipei_trade_date(after_hours_ts), after_hours_ts, "2026-08-11T14:30:00+08:00"),
            )
        self.assertEqual(len(load_main_force_bars("2330", "5m", days=400)), 2)

        purged = purge_out_of_session_bars()

        self.assertEqual(purged, 1)
        rows = load_main_force_bars("2330", "5m", days=400)
        self.assertEqual([row["ts"] for row in rows], [BASE_TS])
        self.assertEqual(purge_out_of_session_bars(), 0)  # 冪等：清完之後再跑不會再刪

    def test_collector_snapshots_all_active_codes_and_intervals(self):
        bar = {
            "ts": BASE_TS,
            "main_buy_volume": 3,
            "main_sell_volume": 1,
            "main_force_available": True,
        }

        class Service:
            def get_active_stock_codes(self):
                return ["2330", "2317"]

        class Hub:
            def get_live_bars_1m(self, code):
                return [bar]

            def get_live_bars(self, code):
                return [bar]

        result = collect_once(service=Service(), hub=Hub())
        self.assertEqual(result, {"stockCount": 2, "saved1m": 2, "saved5m": 2})
        self.assertEqual(main_force_storage_status()["barCount"], 4)

    def test_collector_only_rewrites_latest_two_bars(self):
        bars = [{
            "ts": BASE_TS + offset * 60_000,
            "main_buy_volume": offset + 1,
            "main_sell_volume": 0,
            "main_force_available": True,
        } for offset in range(5)]

        class Service:
            def get_active_stock_codes(self):
                return ["2330"]

        class Hub:
            def get_live_bars_1m(self, code):
                return bars

            def get_live_bars(self, code):
                return bars

        result = collect_once(service=Service(), hub=Hub())
        self.assertEqual(result, {"stockCount": 1, "saved1m": 2, "saved5m": 2})
        self.assertEqual(len(load_main_force_bars("2330", "1m")), 2)

    def test_ranking_orders_by_absolute_net_volume_desc(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }])
        save_main_force_bars("2317", "5m", [{
            "ts": base_ts, "main_buy_volume": 5, "main_sell_volume": 300,
            "main_force_available": True,
        }])
        save_main_force_bars("1101", "5m", [{
            "ts": base_ts, "main_buy_volume": 20, "main_sell_volume": 15,
            "main_force_available": True,
        }])

        ranking = load_main_force_ranking(trade_date, interval="5m", limit=2)

        self.assertEqual([row["code"] for row in ranking], ["2317", "2330"])
        self.assertEqual(ranking[0]["side"], "sell")
        self.assertEqual(ranking[0]["netVolume"], -295)
        self.assertEqual(ranking[0]["buyVolume"], 5)
        self.assertEqual(ranking[0]["sellVolume"], 300)
        self.assertEqual(ranking[1]["side"], "buy")
        self.assertEqual(ranking[1]["netVolume"], 90)

    def test_ranking_defaults_to_empty_when_no_data_for_date(self):
        self.assertEqual(load_main_force_ranking("2000-01-01"), [])

    def test_ranking_includes_stock_name_when_available_and_falls_back_to_code(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }])
        save_main_force_bars("9999", "5m", [{
            "ts": base_ts, "main_buy_volume": 50, "main_sell_volume": 0,
            "main_force_available": True,
        }])
        with database.get_connection() as connection:
            connection.execute(
                "INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, ?)",
                ("2330", "台積電", "TSE", "2026-09-18T00:00:00+00:00"),
            )

        ranking = {row["code"]: row for row in load_main_force_ranking(trade_date, interval="5m")}

        self.assertEqual(ranking["2330"]["name"], "台積電")
        self.assertEqual(ranking["9999"]["name"], "9999")

    def test_load_daily_main_force_net_aggregates_per_trade_date(self):
        base_ts = BASE_TS
        day2_ts = base_ts + 86_400_000 * 3
        trade_date1 = taipei_trade_date(base_ts)
        trade_date2 = taipei_trade_date(day2_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }, {
            "ts": base_ts + 300_000, "main_buy_volume": 20, "main_sell_volume": 5,
            "main_force_available": True,
        }])
        save_main_force_bars("2330", "5m", [{
            "ts": day2_ts, "main_buy_volume": 3, "main_sell_volume": 50,
            "main_force_available": True,
        }])

        result = load_daily_main_force_net("2330")

        self.assertEqual(result[trade_date1], 105)  # (100-10) + (20-5)
        self.assertEqual(result[trade_date2], -47)  # 3-50
        self.assertEqual(len(result), 2)

    def test_load_daily_main_force_net_empty_for_untracked_code(self):
        self.assertEqual(load_daily_main_force_net("9999"), {})

    def test_list_tracked_stock_codes_returns_sorted_distinct_codes_for_date(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "1m", [{
            "ts": base_ts, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
        }])
        save_main_force_bars("1101", "1m", [{
            "ts": base_ts + 60_000, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
        }])
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
        }])
        self.assertEqual(list_tracked_stock_codes(trade_date, "1m"), ["1101", "2330"])
        self.assertEqual(list_tracked_stock_codes(trade_date, "5m"), ["2330"])
        self.assertEqual(list_tracked_stock_codes("2000-01-01", "1m"), [])

    def test_prune_old_bars_keeps_only_most_recent_trading_days(self):
        base_ts = BASE_TS
        day_ms = 24 * 60 * 60 * 1000
        dates = []
        for offset in range(6):
            ts = base_ts + offset * day_ms
            dates.append(taipei_trade_date(ts))
            save_main_force_bars("2330", "1m", [{
                "ts": ts, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
            }])
        deleted = prune_old_bars(keep_days=4)
        remaining_dates = sorted({row["trade_date"] for row in load_main_force_bars("2330", "1m", days=400)})
        self.assertEqual(remaining_dates, sorted(dates[-4:]))
        self.assertEqual(deleted, 2)

    def test_prune_old_bars_no_op_when_fewer_days_than_keep(self):
        base_ts = BASE_TS
        save_main_force_bars("2330", "1m", [{
            "ts": base_ts, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
        }])
        self.assertEqual(prune_old_bars(keep_days=4), 0)


if __name__ == "__main__":
    unittest.main()
