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
import chip_radar
import chips_daily
import database
import fundamentals_daily
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
        self.assertEqual((t["score"], t["prevScore"]), (15, 0))    # 最後一天拉高；前一天完全平盤，創新高平手不算 → 0 分
        self.assertEqual(t["threeDayLow"], 99.0)
        self.assertEqual(t["changePct"], 4.0)
        self.assertFalse(t["ma60OverHead"])
        self.assertAlmostEqual(t["volumeRatio"], 1.67, places=2)

    def test_risks(self) -> None:
        t = {"ma60OverHead": True, "ma60": 53.93, "dev5Pct": 19.0, "limitUp": True}
        self.assertEqual(module._risks(t, {"inst5": -3.0, "instToday": -1.0}, True),
                         ["處置中", "季線 53.93 在頭上", "漲停後與 5 日線乖離 19%", "法人 5 日仍為賣超"])
        # 法人今天轉賣超不算風險（2026-10-14：老師 10/14 光頡法人今天賣超，卡片沒有提示）
        self.assertEqual(module._risks({"ma60OverHead": False, "dev5Pct": 2.0}, {"inst5": 10.0, "instToday": -2.0}, False), [])
        # 本益比高於族群均值 1.5 倍才提示（老師 10/14：天二科技 69 vs 44 有；光頡 52 vs 44 沒有）
        self.assertEqual(module._risks({}, {"pe": 69.35, "peGroupAvg": 44.0}, False), ["本益比 69 高於族群均值 44"])
        self.assertEqual(module._risks({}, {"pe": 52.03, "peGroupAvg": 44.0}, False), [])


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        main_force_store._table_ready_path = None
        main_force_store._ensure_table()
        self.patches = [patch.object(m, "STOCK_GROUPS", GROUPS) for m in (module, brew_launch, brew_launch_history, chip_radar)]
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        chip_radar._cache.update({"at": 0.0, "key": None, "radar": None})
        # 集保（400 張以上 12～15 級、合計 17 級）：合晶 9/11 大戶 +1%、9/18 +2%（籌碼% 3√2＝4.24 上買超榜，連 2 週）；雷科 9/18 −1%
        for d, big_6182, big_6207 in (("2026-09-04", 30_000_000, 30_000_000), ("2026-09-11", 31_000_000, 30_000_000),
                                      ("2026-09-18", 33_000_000, 29_000_000)):
            fundamentals_daily.save_tdcc(d, [(c, lv, 1, sh, 0.0) for c, big in (("6182", big_6182), ("6207", big_6207), ("2330", 30_000_000))
                                             for lv, sh in ((12, big // 2), (15, big - big // 2), (17, 100_000_000))])
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
        chip_radar._cache.update({"at": 0.0, "key": None, "radar": None})
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        main_force_store._table_ready_path = None

    def test_main_group_needs_hot_and_big_inst(self) -> None:
        # 2026-10-19 老師 10/20：資金主軸只挑熱門族、法人 5 日 ≥1 萬張；法人買超不到門檻的（這份測試資料 5,000 張）不算資金主軸
        r = module.build_report("2026-09-24", disposition_codes={"6207"})
        self.assertTrue(r["summary"][0].startswith("今天沒有族群"))
        self.assertFalse(any(g["featured"] and g["name"] == "矽晶圓" for g in r["groups"] if g["aboveRatio"] >= 0.5))

    def test_build_report(self) -> None:
        # 資金主軸要法人 5 日 ≥1 萬張（真實盤）；這份測試資料只有 5,000 張，把門檻降下來才測得到「精選族群」的判讀文字
        with patch.object(module, "MAIN_GROUP_MIN_INST5", 1000):
            r = module.build_report("2026-09-24", disposition_codes={"6207"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["date"], "2026-09-24")
        self.assertEqual((r["basis"]["stocks"], r["basis"]["withScore"], r["basis"]["hasInst"]), (3, 2, True))
        self.assertEqual(r["basis"]["instDates"][0], "2026-09-24")
        self.assertEqual(r["tiles"]["crossed"], 1)
        self.assertEqual((r["tiles"]["bodyJump"], r["tiles"]["strong"]), (1, 1))
        self.assertEqual(r["tiles"]["newFull"], 1)
        self.assertEqual(r["tiles"]["disposition"], 1)
        tech = r["picks"]["tech"]
        self.assertEqual([x["code"] for x in tech], ["6182"])
        self.assertEqual(tech[0]["tag"], "站上 +3.8%")
        self.assertEqual(tech[0]["defense"], {"ma20": 100.2, "threeDayLow": 99.0})
        self.assertEqual((tech[0]["name"], tech[0]["group"], tech[0]["hot"]), ("合晶", "矽晶圓", True))
        chips = r["picks"]["chips"]
        self.assertEqual([x["code"] for x in chips], ["6182"])
        self.assertEqual((chips[0]["inst5"], chips[0]["instStreak"], chips[0]["instToday"], chips[0]["tag"]), (5000.0, 5, 1000.0, "連 2 週"))
        self.assertEqual(chips[0]["inst5VolPct"], 166.7)    # 5000 張 ÷ 5 天成交量 3000 張
        self.assertEqual(chips[0]["radar"], {"chip": 4.2, "chip2": 4.24, "streak": 2, "rank": 1, "week": "2026-09-18", "listSize": 1})
        self.assertEqual((chips[0]["mf5"], chips[0]["mfStreak"]), (800, 2))
        self.assertEqual(chips[0]["risks"], [])
        self.assertEqual(r["radar"], {"week": "2026-09-18", "hot": ["矽晶圓", "半導體", "設備股"], "buy": 1, "pool": 10})
        self.assertEqual(r["basis"]["chipWeek"], "2026-09-18")
        body = r["picks"]["body"]
        self.assertEqual([x["code"] for x in body], ["6182"])
        # 前一天完全平盤，內定算法算 6 分（創新高條件同值也算）；問診七科：均線／族群／籌碼週增（集保大戶 9/18 週增）／法人各 2 分，總分 8
        self.assertEqual((body[0]["tag"], body[0]["seven"], body[0]["sevenCls"]), ("問診 8 分", 8, "強勢"))
        groups = {g["name"]: g for g in r["groups"]}
        self.assertEqual([g["name"] for g in r["groups"]], ["矽晶圓", "半導體", "設備股"])   # 依當天平均漲跌幅排
        self.assertEqual((groups["矽晶圓"]["aboveMa20"], groups["矽晶圓"]["members"], groups["矽晶圓"]["inst5"], groups["矽晶圓"]["rank"]), (1, 1, 5000.0, 1))
        # 2026-10-13 老師 10/14 報告：精選族群寫當天族群均漲
        self.assertEqual(groups["矽晶圓"]["judge"], "資金已進、型態已翻；族群 09/24 均漲 +4.0%")
        self.assertEqual((groups["矽晶圓"]["featured"], groups["矽晶圓"]["featuredOrder"]), (True, 0))
        # 設備股：籌碼雷達熱門族（週籌碼先進）、法人 5 日賣超、站上月線不到一半 → 低檔醞釀
        self.assertEqual(groups["設備股"]["judge"], "週籌碼先進、法人 5 日賣超、價格未表態：屬「低檔醞釀」非「發動」，只挑族內已站上月線者")
        self.assertIn("資金主軸在已站上月線的族群：矽晶圓", r["summary"][0])
        self.assertEqual(r["summary"][1], "籌碼進、法人未跟：半導體、設備股 —— 低檔醞釀，等站上月線再進")
        self.assertIn("籌碼面首選 合晶；技術面首選 合晶", r["summary"][2])
        self.assertIn("均線新滿分 合晶", r["summary"][3])
        self.assertIn("體質轉強 合晶", r["summary"][3])
        self.assertTrue(r["summary"][-1].startswith("風險提示"))
        self.assertEqual(r["counts"], {"chips": 1, "tech": 1, "techNear": 0, "body": 1, "strong": 1, "full": 1})
        self.assertEqual([x["tag"] for x in r["picks"]["full"]], ["0→15"])
        self.assertEqual(r["notes"]["jumpTop"], [{"code": "6182", "name": "合晶", "from": 0, "to": 15}])
        self.assertEqual(r["notes"]["sustained"], [])   # 合晶是跳升進來的，不算續強（見下面 SustainedTests 另外測沒跳升的續強情形）

    def test_chips_follow_radar(self) -> None:
        # 老師的籌碼面：籌碼暴增榜前 10 名、熱門族；連續增加週數多的優先，同週數均線分數高的在前；法人 5 日賣超的照列（只標風險）
        self.enterContext(patch.object(module, "CHIPS_DAILY_HOT_GROUPS", 0))   # 這個測試只看週籌碼熱門族
        for d in trading_dates("2026-09-24", 5):
            chips_daily.save_institutional(d, "TSE", [{"code": "2330", "name": "台積電", "foreign": 2_000_000, "trust": 0, "dealer": 0, "total": 2_000_000}], "test")
        hot_all = {"6182": "矽晶圓", "2330": "半導體", "6207": "設備股"}

        def picks(buy: list[str], streaks: dict[str, int], hot_of: dict[str, str]) -> list[str]:
            fake = {"week": "2026-09-18", "buy": buy, "hot": sorted(set(hot_of.values())), "hotOf": hot_of,
                    "stocks": {c: {"chip": 5.0, "streak": streaks.get(c, 1), "rank": i + 1} for i, c in enumerate(buy)}}
            with patch.object(module, "radar_week", return_value=fake):
                r = module.build_report("2026-09-24")
            return [(x["code"], x["tag"], x["hot"], x["group"]) for x in r["picks"]["chips"]]

        # 連續週數多的在前；雷科法人賣超照列（老師 10/19：緯穎法人 5 日 -10.6% 也列，卡片上標風險）
        self.assertEqual(picks(["6182", "6207", "2330"], {"6182": 1, "6207": 5, "2330": 3}, hot_all),
                         [("6207", "連 5 週", True, "設備股"), ("2330", "連 3 週", True, "半導體"), ("6182", "連 1 週", True, "矽晶圓")])
        # 同週數：均線分數高的（合晶 15）在前
        self.assertEqual([x[0] for x in picks(["2330", "6182"], {"6182": 2, "2330": 2}, hot_all)], ["6182", "2330"])
        # 不是熱門族不列；榜上第 11 名以後不列
        self.assertEqual([x[0] for x in picks(["6182", "2330"], {}, {"6182": "矽晶圓"})], ["6182"])
        self.assertEqual([x[0] for x in picks([f"9{i:03d}" for i in range(10)] + ["2330", "6182"], {}, hot_all)], [])

    def test_tdcc_week_available(self) -> None:
        # 老師 10/16（週五）那份還是 10/08 那週，10/19 才換 10/16 那週；10/08（週四、隔天連假）當天就用 10/08 那週
        f = module._tdcc_week_available
        self.assertTrue(f("2026-10-08", "2026-10-12"))
        self.assertFalse(f("2026-10-16", "2026-10-16"))
        self.assertTrue(f("2026-10-16", "2026-10-19"))
        self.assertTrue(f("2026-10-08", "2026-10-08"))
        self.assertFalse(f("2026-10-16", "2026-10-15"))

    def test_chips_limit_four(self) -> None:
        self.enterContext(patch.object(module, "CHIPS_DAILY_HOT_GROUPS", 0))
        codes = ["6182", "2330", "6207"]
        fake = {"week": "2026-09-18", "buy": codes, "hot": ["x"], "hotOf": {c: "x" for c in codes},
                "stocks": {c: {"chip": 5.0, "streak": 1, "rank": i + 1} for i, c in enumerate(codes)}}
        with patch.object(module, "radar_week", return_value=fake), patch.object(module, "CHIPS_PICK_LIMIT", 2):
            r = module.build_report("2026-09-24")
        self.assertEqual(len(r["picks"]["chips"]), 2)

    def test_chips_tier2(self) -> None:
        # 2026-10-13 老師 10/14 報告：暴增榜前 10 之後，補熱門族、籌碼連續增加 ≥3 週、這一週籌碼% ≥3% 的
        self.enterContext(patch.object(module, "CHIPS_DAILY_HOT_GROUPS", 0))
        for d in trading_dates("2026-09-24", 5):
            chips_daily.save_institutional(d, "TSE", [{"code": "2330", "name": "台積電", "foreign": 2_000_000, "trust": 0, "dealer": 0, "total": 2_000_000}], "test")
        hot_all = {"6182": "矽晶圓", "2330": "半導體"}

        def picks(stocks: dict[str, dict], disposed: set[str] | None = None) -> list[tuple[str, str]]:
            fake = {"week": "2026-09-18", "buy": [], "hot": sorted(set(hot_all.values())), "hotOf": hot_all, "stocks": stocks}
            with patch.object(module, "radar_week", return_value=fake):
                r = module.build_report("2026-09-24", disposition_codes=disposed)
            return [(x["code"], x["tag"]) for x in r["picks"]["chips"]]

        # 台積電連 9 週但這週只 +1.5%（像聯寶、台光電）不列；合晶連 3 週 +3.5%（像光頡）列
        self.assertEqual(picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}, "2330": {"chip": 1.5, "streak": 9, "rank": None}}), [("6182", "連 3 週")])
        # 連 2 週不夠
        self.assertEqual(picks({"6182": {"chip": 5.0, "streak": 2, "rank": None}}), [])
        # 都夠的話，連續週數多的在前
        self.assertEqual([c for c, _ in picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}, "2330": {"chip": 4.0, "streak": 5, "rank": None}})], ["2330", "6182"])
        # 處置中的不列（像 10/13 的聯茂）
        self.assertEqual([c for c, _ in picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}, "2330": {"chip": 4.0, "streak": 5, "rank": None}}, {"2330"})], ["6182"])
        # 門檻用兩位小數比：一位小數 3.0、其實 2.96 的不列
        self.assertEqual(picks({"6182": {"chip": 3.0, "chip2": 2.96, "streak": 3, "rank": None}}), [])
        # 剛出獄 5 天內的只放觀察名單，不列精選（像 10/13 的嘉晶）
        out = {"nextTradingDay": "2026-09-29", "upcoming": [], "releasing": [], "active": [], "high": [],
               "watch": [{"code": "6182", "name": "合晶", "daysOut": 2}]}
        with patch.object(module, "disposition_from_jail", return_value=out):
            self.assertEqual(picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}}), [])
        # 營收年增為負的照列（老師 10/12 GIS-KY 年增 -30% 也列，卡片上標風險）
        with patch.object(module, "latest_revenue", return_value={"6182": {"yoy": -37.2, "ym": "2026-09"}}):
            self.assertEqual(picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}}), [("6182", "連 3 週")])

    def test_revenue_cards_outside_groups(self) -> None:
        # 營收面入選、不在族群表的（老師的台汽電）也有完整卡片；但不算進族群統計、其他精選
        import revenue_rank

        dates = trading_dates("2026-09-24", 241)
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [("8926", d + "T00:00:00", 50, 51, 49, 50, 2000) for d in dates[:-1]] + [("8926", dates[-1] + "T00:00:00", 50, 55, 50, 54, 4000)])
        for d in dates[-5:]:
            chips_daily.save_institutional(d, "TSE", [{"code": "8926", "name": "台汽電", "foreign": 600_000, "trust": 0, "dealer": 0, "total": 600_000}], "test")
        section = {"month": "2026-09", "asOf": "2026-09-24", "published": 2, "yoyHigh": 2, "qualified": 2, "rule": {},
                   "picks": [{"code": "8926", "name": "台汽電", "yoy": 1114.06}, {"code": "6182", "name": "合晶", "yoy": 40.0}]}
        with patch.object(revenue_rank, "report_section", return_value=section):
            r = module.build_report("2026-09-24")
        cards = {p["code"]: p["card"] for p in r["revenue"]["picks"]}
        self.assertEqual((cards["8926"]["tag"], cards["8926"]["close"], cards["8926"]["inst5"]), ("年增 +1114%", 54.0, 3000.0))
        self.assertEqual(cards["8926"]["inst5VolPct"], 25.0)     # 3000 張 ÷ 5 天 12000 張
        self.assertEqual(cards["6182"]["tag"], "年增 +40%")
        self.assertEqual((r["basis"]["stocks"], r["basis"]["stale"]), (3, 0))
        self.assertNotIn("8926", [x["code"] for sec in r["picks"].values() for x in sec])

    def test_last_week_followup(self) -> None:
        with patch.object(module, "_disposition_codes", return_value=set()):
            r = module.build_report("2026-09-24")
            self.assertIsNone(r["lastWeek"])   # 一週前沒有報告
            week_ago = trading_dates("2026-09-24", 6)[0]   # 往前 5 個交易日
            self.assertEqual(week_ago, "2026-09-17")
            module.save_report({"date": week_ago, "generatedAt": "x", "picks": {"chips": [
                {"code": "6182", "name": "合晶", "group": "矽晶圓", "tag": "法人連買 3 天", "close": 100.0},
                {"code": "6207", "name": "雷科", "group": "設備股", "tag": "主力連買 2 天", "close": 101.0},
            ], "tech": []}})
            r = module.build_report("2026-09-24")
            lw = r["lastWeek"]
            self.assertEqual((lw["date"], lw["count"], lw["withData"], lw["aboveMa20"]), ("2026-09-17", 2, 2, 1))
            a, b = lw["picks"]
            # 合晶：入榜 100 → 104（+4%），期間最高收 104、最低收 100，法人這週 5 天共 5000 張，守住月線
            self.assertEqual((a["code"], a["pickClose"], a["close"], a["changePct"], a["maxClose"], a["maxGainPct"], a["minClose"]), ("6182", 100.0, 104.0, 4.0, 104.0, 4.0, 100.0))
            self.assertEqual((a["instSince"], a["instDays"], a["days"], a["aboveMa20"], a["verdict"]), (5000.0, 5, 5, True, "續強，守住月線"))
            # 雷科：緩跌、在月線下 → 跌破月線
            self.assertEqual((b["code"], b["aboveMa20"], b["verdict"], b["instSince"]), ("6207", False, "跌破月線，型態走弱", -250.0))
            self.assertLess(b["changePct"], 0)
            self.assertAlmostEqual(lw["avgChangePct"], (a["changePct"] + b["changePct"]) / 2, places=2)
            self.assertIn("lastWeek", r["rules"])
            # 那份存下來後再讀：上一週那一組也在裡面
            module.save_report(r)
            self.assertEqual(module.load_report("2026-09-24")["lastWeek"]["date"], "2026-09-17")

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

    def test_disposition_from_jail(self) -> None:
        # 2026-10-13：處置動態改用處置監獄帳本（全市場）；9/24 基準、下一個交易日 9/29（9/25 中秋節、9/28 教師節）
        import disposition_jail as jail

        def punish(code: str, start: str, end: str) -> dict:
            return {"code": code, "start": start, "end": end, "market": "TSE", "name": f"N{code}", "announce": start,
                    "condition": "連續三次", "measure": "第一次處置", "days": 10, "minutes": 5, "content": ""}

        jail.save_punishes([
            punish("6182", "2026-09-29", "2026-10-09"),   # 明日起處置
            punish("6207", "2026-09-14", "2026-09-24"),   # 明日出獄
            punish("2330", "2026-09-14", "2026-09-24"),   # 期滿但 9/29 又被再次處置：不算出獄
            punish("2330", "2026-09-29", "2026-10-09"),
            punish("3008", "2026-09-08", "2026-09-22"),   # 9/23 出獄，到 9/29 是出獄第 3 天
        ])
        d = module.disposition_from_jail("2026-09-24")
        self.assertEqual((d["source"], d["nextTradingDay"]), ("jail", "2026-09-29"))
        self.assertEqual([x["code"] for x in d["upcoming"]], ["2330", "6182"])
        self.assertEqual([x["code"] for x in d["releasing"]], ["6207"])
        self.assertEqual([x["code"] for x in d["active"]], ["2330", "6207"])
        self.assertEqual([(x["code"], x["daysOut"]) for x in d["watch"]], [("3008", 3)])
        self.assertEqual(d["upcoming"][1]["group"], "矽晶圓")
        self.assertEqual(d["high"], [])
        # 籌碼面觀察名單：出獄 5 天內、籌碼% ≥2%（兩位小數比：1.97% 不列）
        def watch(chip2: float) -> list:
            fake = {"week": "2026-09-18", "buy": [], "hot": [], "hotOf": {}, "stocks": {"3008": {"chip": round(chip2, 1), "chip2": chip2, "streak": 1, "rank": None}}}
            with patch.object(module, "radar_week", return_value=fake):
                return module.build_report("2026-09-24")["chipsWatch"]

        self.assertEqual(watch(1.97), [])
        self.assertEqual(watch(2.4), [{"code": "3008", "name": "N3008", "daysOut": 3, "chip": 2.4}])
        # 報告用帳本（全市場），不是只看族群表
        r = module.build_report("2026-09-24")
        self.assertEqual((r["tiles"]["upcoming"], r["tiles"]["releasing"]), (2, 1))
        self.assertEqual(r["readDate"], "2026-09-29")

    def test_refresh_window(self) -> None:
        self.assertTrue(module._in_refresh_window(datetime(2026, 9, 24, 15, 10, tzinfo=TW)))
        self.assertFalse(module._in_refresh_window(datetime(2026, 9, 24, 14, 0, tzinfo=TW)))
        self.assertFalse(module._in_refresh_window(datetime(2026, 9, 25, 16, 0, tzinfo=TW)))   # 中秋節
        # 晚上 21:05～23:35、早上 07:30～08:30（交易日）
        self.assertTrue(module._in_evening_window(datetime(2026, 9, 24, 21, 30, tzinfo=TW)))
        self.assertTrue(module._in_evening_window(datetime(2026, 9, 24, 7, 45, tzinfo=TW)))
        self.assertFalse(module._in_evening_window(datetime(2026, 9, 24, 20, 0, tzinfo=TW)))
        self.assertFalse(module._in_evening_window(datetime(2026, 9, 25, 21, 30, tzinfo=TW)))   # 中秋節


class SustainedTests(unittest.TestCase):
    """體質轉強（問診七科判強勢＋均線跳升）跟續強確認（昨日已強、今日維持）是兩條不相交的名單，
    用跟 ReportTests 不同的小族群獨立測，避免跟那邊的共用 fixture 糾在一起。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        main_force_store._table_ready_path = None
        main_force_store._ensure_table()
        groups = {"面板": [("2409", "友達")], "矽晶圓2": [("6183", "合晶乙")]}
        self.patches = [patch.object(m, "STOCK_GROUPS", groups) for m in (module, brew_launch, brew_launch_history)]
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        dates = trading_dates("2026-09-24", 241)
        rows = []
        # 友達：241 天線性緩漲，前一天、今天均線分數都是 15，沒有跳升 → 應該落在「續強確認」，不進「體質轉強」名單
        rising = [100 + i * 0.5 for i in range(241)]
        rows += [("2409", d + "T00:00:00", c - 0.3, c + 0.5, c - 0.5, c, 500) for d, c in zip(dates, rising)]
        # 合晶乙：跟 ReportTests 的合晶同款「平盤 240 天、最後一天拉 4%」，均線 6→15 有跳升 → 進「體質轉強」，不算續強
        rows += [("6183", d + "T00:00:00", 100, 101, 99, 100, 500) for d in dates[:-1]] + [("6183", dates[-1] + "T00:00:00", 100, 105, 103, 104, 1000)]
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        for d in dates[-5:]:
            chips_daily.save_institutional(d, "OTC", [
                {"code": "2409", "name": "友達", "foreign": 500_000, "trust": 0, "dealer": 0, "total": 500_000},
                {"code": "6183", "name": "合晶乙", "foreign": 500_000, "trust": 0, "dealer": 0, "total": 500_000},
            ], "test")

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        main_force_store._table_ready_path = None

    def test_sustained_vs_jumped(self) -> None:
        with patch.object(module, "_disposition_codes", return_value=set()):
            r = module.build_report("2026-09-24")
        self.assertEqual(r["status"], "ok")
        self.assertEqual([x["code"] for x in r["picks"]["body"]], ["6183"])          # 有跳升的那檔進體質轉強名單
        self.assertEqual([x["code"] for x in r["notes"]["sustained"]], ["2409"])     # 沒跳升、昨天就已經強的那檔算續強確認
        # 兩檔問診七科總分都是 6（均線／族群／法人各 2 分，其餘沒資料不計入）
        self.assertEqual(r["picks"]["body"][0]["tag"], "問診 6 分")
        self.assertEqual(r["notes"]["sustained"][0], {"code": "2409", "name": "友達", "score": 15, "total": 6})
        self.assertEqual((r["tiles"]["bodyJump"], r["tiles"]["strong"]), (1, 2))
        self.assertEqual((r["counts"]["body"], r["counts"]["strong"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
