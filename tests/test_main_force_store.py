import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from main_force_collector import collect_once
from main_force_store import (
    classify_holder_strength,
    compute_holder_strength_pct,
    latest_trade_date_with_data,
    list_tracked_stock_codes,
    load_daily_main_force_net,
    load_daily_main_force_net_amount,
    load_holder_timeline,
    load_main_force_bars,
    load_main_force_ranking,
    load_window_flips,
    main_force_storage_status,
    previous_trade_date_with_data,
    prune_old_bars,
    purge_out_of_session_bars,
    save_main_force_bars,
)
from otc_index import taipei_trade_date

BASE_TS = 1_786_413_600_000  # 2026-08-11 10:00:00+08:00（盤中連續交易時段內）


class HolderStrengthComputationTests(unittest.TestCase):
    def test_compute_pct_is_none_without_turnover(self):
        self.assertIsNone(compute_holder_strength_pct(1000, 0, 0))
        self.assertIsNone(compute_holder_strength_pct(1000, 0, -5))

    def test_compute_pct_formula(self):
        pct = compute_holder_strength_pct(40_000_000, 5_000_000, 200_000_000)
        self.assertEqual(pct, 17.5)

    def test_classify_returns_bull_label_at_signal_threshold(self):
        pct, label = classify_holder_strength(40_000_000, 5_000_000, 200_000_000)
        self.assertEqual(pct, 17.5)
        self.assertEqual(label, "盤中大戶偏買")

    def test_classify_returns_strong_buy_label_above_28_percent(self):
        pct, label = classify_holder_strength(70_000_000, 5_000_000, 200_000_000)
        self.assertEqual(pct, 32.5)
        self.assertEqual(label, "盤中大戶強力買進")

    def test_classify_returns_bear_labels_symmetrically(self):
        pct, label = classify_holder_strength(5_000_000, 40_000_000, 200_000_000)
        self.assertEqual(pct, -17.5)
        self.assertEqual(label, "盤中大戶偏賣")
        pct, label = classify_holder_strength(5_000_000, 70_000_000, 200_000_000)
        self.assertEqual(pct, -32.5)
        self.assertEqual(label, "盤中大戶強力賣出")

    def test_classify_label_none_when_below_12_percent(self):
        pct, label = classify_holder_strength(50_000_000, 45_000_000, 200_000_000)
        self.assertEqual(pct, 2.5)
        self.assertIsNone(label)

    def test_classify_label_none_when_turnover_below_1e_yuan_floor(self):
        # 百分比達門檻，但累計成交額不到1億元。
        pct, label = classify_holder_strength(4_000_000, 500_000, 10_000_000)
        self.assertEqual(pct, 35.0)
        self.assertIsNone(label)

    def test_classify_label_none_when_net_amount_below_30m_floor(self):
        # 百分比達20%（超過12%門檻），但淨額本身沒到3,000萬元（只有2,000萬）。
        pct, label = classify_holder_strength(20_500_000, 500_000, 100_000_000)
        self.assertEqual(pct, 20.0)
        self.assertIsNone(label)


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

    def test_ranking_includes_net_amount_in_dollars(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_buy_amount": 40_000_000, "main_sell_amount": 5_000_000,
            "main_force_available": True,
        }])

        ranking = {row["code"]: row for row in load_main_force_ranking(trade_date, interval="5m")}

        self.assertEqual(ranking["2330"]["netAmount"], 35_000_000)

    def test_ranking_defaults_to_empty_when_no_data_for_date(self):
        self.assertEqual(load_main_force_ranking("2000-01-01"), [])

    def test_latest_trade_date_with_data_looks_back_across_all_stocks(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }])
        self.assertEqual(latest_trade_date_with_data("2026-09-24"), trade_date)
        self.assertIsNone(latest_trade_date_with_data(trade_date))  # 不含當天
        self.assertIsNone(latest_trade_date_with_data("2026-09-24", interval="1m"))

    def test_ranking_codes_filter_keeps_only_listed_codes(self):
        # 收集器會追蹤任何開過圖的股票（含 ETF），排行端點用 codes 限縮在官方族群範圍。
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        for code, buy in (("2330", 100), ("00632R", 500), ("00991A", 400)):
            save_main_force_bars(code, "5m", [{
                "ts": base_ts, "main_buy_volume": buy, "main_sell_volume": 0,
                "main_force_available": True,
            }])
        unfiltered = [row["code"] for row in load_main_force_ranking(trade_date, interval="5m")]
        self.assertEqual(unfiltered, ["00632R", "00991A", "2330"])
        filtered = [row["code"] for row in load_main_force_ranking(trade_date, interval="5m", codes={"2330", "2317"})]
        self.assertEqual(filtered, ["2330"])
        self.assertEqual(load_main_force_ranking(trade_date, interval="5m", codes=set()), [])

    def test_ranking_includes_official_strength_pct_and_label(self):
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_buy_amount": 40_000_000, "main_sell_amount": 5_000_000,
            "total_amount": 200_000_000, "main_force_available": True,
        }])

        ranking = {row["code"]: row for row in load_main_force_ranking(trade_date, interval="5m")}

        # (40,000,000-5,000,000)/200,000,000*100 = 17.5%；達+12%正式門檻，未達28%強力
        self.assertEqual(ranking["2330"]["strengthPct"], 17.5)
        self.assertEqual(ranking["2330"]["holderLabel"], "盤中大戶偏買")

    def test_holder_timeline_keeps_morning_entries(self):
        """2026-10-13 使用者：盤中大戶力多／空只剩收盤那一刻的名單——早上進過名單、後來掉出去的也要找得到，時間＝第一次進名單。"""
        trade_date = taipei_trade_date(BASE_TS)
        step = 5 * 60 * 1000

        def bar(i, buy, sell, buy_amt, sell_amt, total):
            return {"ts": BASE_TS + i * step, "main_buy_volume": buy, "main_sell_volume": sell, "main_buy_amount": buy_amt,
                    "main_sell_amount": sell_amt, "total_amount": total, "main_force_available": True}

        # 2330：第 1 根 +300、第 2 根 +300（累計 600 ≥ 500 → 10:05 進多方）、第 3 根 −200（累計 400，收盤掉出去）
        save_main_force_bars("2330", "5m", [
            bar(0, 300, 0, 30_000_000, 0, 100_000_000),
            bar(1, 300, 0, 30_000_000, 0, 200_000_000),
            bar(2, 0, 200, 0, 20_000_000, 300_000_000),
        ])
        # 2317：一路賣，第 1 根就 −600 → 進空方，收盤還在
        save_main_force_bars("2317", "5m", [bar(0, 0, 600, 0, 50_000_000, 100_000_000), bar(1, 0, 100, 0, 5_000_000, 150_000_000)])
        # 1101：只有 +100，沒進名單
        save_main_force_bars("1101", "5m", [bar(0, 100, 0, 1_000_000, 0, 50_000_000)])

        timeline = load_holder_timeline(trade_date, interval="5m")
        self.assertEqual(set(timeline), {"2330", "2317"})
        bull = timeline["2330"]["bull"]
        self.assertEqual(bull["firstTs"], BASE_TS + step)
        self.assertEqual(bull["firstPct"], 30.0)            # 60,000,000 ÷ 200,000,000
        self.assertEqual((bull["peakPct"], bull["peakTs"]), (30.0, BASE_TS + step))
        self.assertFalse(bull["still"])                      # 收盤累計只剩 400 張
        self.assertNotIn("bear", timeline["2330"])
        bear = timeline["2317"]["bear"]
        self.assertEqual(bear["firstTs"], BASE_TS)
        self.assertEqual(bear["firstPct"], -50.0)
        self.assertTrue(bear["still"])
        self.assertEqual(set(load_holder_timeline(trade_date, interval="5m", codes={"2317"})), {"2317"})

    def test_window_flips_detect_midday_reversal(self):
        """2026-10-13 使用者：近 30 分鐘主力翻多／翻空——前 30 分鐘主力淨賣、近 30 分鐘轉淨買才發；第一次成立發一次，要先不成立才會再發。"""
        trade_date = taipei_trade_date(BASE_TS)
        step = 5 * 60 * 1000

        def bar(i, buy, sell, price=200_000):
            return {"ts": BASE_TS + i * step, "main_buy_volume": buy, "main_sell_volume": sell, "main_buy_amount": buy * price,
                    "main_sell_amount": sell * price, "total_amount": 1_000_000_000, "main_force_available": True}

        # 2330：前 6 根一直賣（每根買 10、賣 40），接著 6 根轉買（每根買 60、賣 10）→ 第 12 根收完（近 30 分 +300、前 30 分 −180）翻多
        bars = [bar(i, 10, 40) for i in range(6)] + [bar(6 + i, 60, 10) for i in range(6)] + [bar(12 + i, 60, 10) for i in range(3)]
        save_main_force_bars("2330", "5m", bars)
        # 2317：一路買，沒有「翻」
        save_main_force_bars("2317", "5m", [bar(i, 60, 10) for i in range(15)])
        events = load_window_flips(trade_date, interval="5m")
        self.assertEqual([(e["code"], e["side"]) for e in events], [("2330", "bull")])
        flip = events[0]
        self.assertEqual(flip["ts"], BASE_TS + 12 * step)       # 第 12 根（index 11）收盤
        self.assertEqual((flip["curNet"], flip["prevNet"]), (300, -180))
        self.assertEqual(flip["curRatio"], 71.4)
        self.assertEqual(flip["curNetAmount"], 60_000_000)
        # 太小（近 30 分鐘主力買賣不到 200 張）不算
        save_main_force_bars("1101", "5m", [bar(i, 2, 8) for i in range(6)] + [bar(6 + i, 8, 2) for i in range(6)])
        self.assertNotIn("1101", {e["code"] for e in load_window_flips(trade_date, interval="5m")})

    def test_window_flips_cooldown_60_minutes(self):
        """同一檔同方向 60 分鐘內不重複發（第一版一天翻 10 次）。"""
        trade_date = taipei_trade_date(BASE_TS)
        step = 5 * 60 * 1000

        def bar(i, buy, sell):
            return {"ts": BASE_TS + i * step, "main_buy_volume": buy, "main_sell_volume": sell, "main_buy_amount": buy * 200_000,
                    "main_sell_amount": sell * 200_000, "total_amount": 1_000_000_000, "main_force_available": True}

        # 賣 6 根 → 買 6 根（第 12 根收盤翻多）→ 賣 6 根 → 買 6 根（第 24 根收盤又成立，距上次 60 分鐘，可以再發）
        pattern = [(10, 40)] * 6 + [(60, 10)] * 6 + [(10, 40)] * 6 + [(60, 10)] * 6
        save_main_force_bars("2330", "5m", [bar(i, b, s) for i, (b, s) in enumerate(pattern)])
        bull = [e for e in load_window_flips(trade_date, interval="5m") if e["side"] == "bull"]
        self.assertEqual([e["ts"] for e in bull], [BASE_TS + 12 * step, BASE_TS + 24 * step])
        # 翻多後馬上大賣 15 分鐘、又買回來：45 分鐘後條件又成立，但還不到 60 分鐘，不發
        save_main_force_bars("2317", "5m", [bar(i, b, s) for i, (b, s) in enumerate([(10, 40)] * 6 + [(60, 10)] * 6 + [(0, 200)] * 3 + [(60, 10)] * 6)])
        bull2317 = [e for e in load_window_flips(trade_date, interval="5m") if e["code"] == "2317" and e["side"] == "bull"]
        self.assertEqual(len(bull2317), 1)

    def test_ranking_strength_pct_is_none_without_turnover_data(self):
        # 舊資料沒有total_amount欄位（或這個bar沒有主力金額），無法算百分比。
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_force_available": True,
        }])

        ranking = {row["code"]: row for row in load_main_force_ranking(trade_date, interval="5m")}

        self.assertIsNone(ranking["2330"]["strengthPct"])
        self.assertIsNone(ranking["2330"]["holderLabel"])

    def test_ranking_label_is_none_below_eligibility_even_if_pct_crosses_threshold(self):
        # 百分比達12%以上，但累計成交額沒有達1億元的門檻，不算正式訊號。
        base_ts = BASE_TS
        trade_date = taipei_trade_date(base_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_volume": 100, "main_sell_volume": 10,
            "main_buy_amount": 4_000_000, "main_sell_amount": 500_000,
            "total_amount": 10_000_000, "main_force_available": True,
        }])

        ranking = {row["code"]: row for row in load_main_force_ranking(trade_date, interval="5m")}

        self.assertEqual(ranking["2330"]["strengthPct"], 35.0)
        self.assertIsNone(ranking["2330"]["holderLabel"])

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

    def test_load_daily_main_force_net_amount_aggregates_per_trade_date(self):
        base_ts = BASE_TS
        day2_ts = base_ts + 86_400_000 * 3
        trade_date1 = taipei_trade_date(base_ts)
        trade_date2 = taipei_trade_date(day2_ts)
        save_main_force_bars("2330", "5m", [{
            "ts": base_ts, "main_buy_amount": 1_000_000, "main_sell_amount": 100_000,
            "main_force_available": True,
        }, {
            "ts": base_ts + 300_000, "main_buy_amount": 200_000, "main_sell_amount": 50_000,
            "main_force_available": True,
        }])
        save_main_force_bars("2330", "5m", [{
            "ts": day2_ts, "main_buy_amount": 30_000, "main_sell_amount": 500_000,
            "main_force_available": True,
        }])

        result = load_daily_main_force_net_amount("2330")

        self.assertEqual(result[trade_date1], 1_050_000)  # (1,000,000-100,000)+(200,000-50,000)
        self.assertEqual(result[trade_date2], -470_000)  # 30,000-500,000
        self.assertEqual(len(result), 2)

    def test_load_daily_main_force_net_amount_empty_for_untracked_code(self):
        self.assertEqual(load_daily_main_force_net_amount("9999"), {})

    def test_previous_trade_date_with_data_finds_most_recent_prior_day(self):
        base_ts = BASE_TS
        day_ms = 24 * 60 * 60 * 1000
        dates = []
        for offset in range(3):
            ts = base_ts + offset * day_ms
            dates.append(taipei_trade_date(ts))
            save_main_force_bars("2330", "5m", [{
                "ts": ts, "main_buy_volume": 1, "main_sell_volume": 0, "main_force_available": True,
            }])
        today = taipei_trade_date(base_ts + 3 * day_ms)

        self.assertEqual(previous_trade_date_with_data("2330", today), dates[-1])
        self.assertEqual(previous_trade_date_with_data("2330", dates[0]), None)
        self.assertIsNone(previous_trade_date_with_data("9999", today))

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
