"""波段日報第一階段：個股技術面分析、報告整理（摘要／族群／三個精選）、每日保存與端點。"""

from __future__ import annotations

import tempfile
import json
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
        self.assertAlmostEqual(t["abovePrevMa20Pct"], 4.0, places=2)     # 老師「站上 +x%」是拿昨天的月線算（昨天月線 100）
        self.assertEqual((t["score"], t["prevScore"]), (15, 6))    # 最後一天拉高；前一天完全平盤：站上均線平手算站上 6、創新高平手不算 0、排列 0 → 6 分
        self.assertEqual(t["threeDayLow"], 99.0)
        self.assertEqual(t["changePct"], 4.0)
        self.assertFalse(t["ma60OverHead"])
        self.assertAlmostEqual(t["volumeRatio"], 1.67, places=2)

    def test_risks(self) -> None:
        t = {"ma60OverHead": True, "ma60": 53.93, "dev5Pct": 19.0, "limitUp": True}
        self.assertEqual(module._risks(t, {"inst5": -3.0, "instToday": -1.0}, True),
                         ["處置中", "漲停後與 5 日線乖離 19%", "法人 5 日仍為賣超", "季線 53.93 在頭上"])
        # 老師 10/21：乖離 ≥10% 就提示（芯鼎 10%、聯一光 11%）、出獄 N 天內寫「依規則觀察三日」，順序＝出獄、本益比、乖離、法人賣超、季線
        self.assertEqual(module._risks({"dev5Pct": 11.2, "limitUp": True}, {"inst5": -5.0, "pe": 110.0, "peGroupAvg": 54.0}, False, 2),
                         ["出獄第 2 天，依規則觀察三日", "本益比 110 高於族群均值 54", "漲停後與 5 日線乖離 11%", "法人 5 日仍為賣超"])
        self.assertEqual(module._risks({"dev5Pct": 9.0}, {}, False), [])
        # 隔日沖買超 ≥30% 提示（老師 10/21：聯一光 51%、今國光 31%；和益 19.5% 沒有），整數捨去、排在出獄後面、本益比前面
        self.assertEqual(module._risks({}, {"pe": 110.0, "peGroupAvg": 54.0}, False, 2, 51.4),
                         ["出獄第 2 天，依規則觀察三日", "隔日沖買超 51%，明日開盤有短線倒貨壓力", "本益比 110 高於族群均值 54"])
        self.assertEqual(module._risks({}, {}, False, None, 31.9), ["隔日沖買超 31%，明日開盤有短線倒貨壓力"])
        self.assertEqual(module._risks({}, {}, False, None, 19.5), [])

    def test_daytrade_pct_map_reads_mirror_and_survives_failures(self) -> None:
        import io

        module._daytrade_cache.clear()
        payload = json.dumps({"date": "2026-10-20", "rows": [{"code": "6209", "todayPct": 31.9}, {"code": "3441", "todayPct": 51.4}, {"code": "9999"}]}).encode()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
            self.assertEqual(module.daytrade_pct_map("2026-10-20"), {"6209": 31.9, "3441": 51.4})
        module._daytrade_cache.clear()
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(module.daytrade_pct_map("2026-10-20"), {})
        module._daytrade_cache.clear()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps({"date": "2026-10-16", "rows": []}).encode())):
            self.assertEqual(module.daytrade_pct_map("2026-10-20"), {})   # 鏡像是前一天的：不用
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
        self.patches.append(patch.object(module, "daytrade_pct_map", lambda as_of: {}))   # 不真的去抓隔日沖鏡像
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

    def test_main_group_needs_hot_and_enough_members(self) -> None:
        # 老師 10/22：資金主軸只挑熱門族、法人 5 日買超、至少 5 檔（彬彬只有 3 檔法人 +1.1 萬張沒入選）；這份測試族群只有 1 檔，不算資金主軸
        r = module.build_report("2026-09-24", disposition_codes={"6207"})
        self.assertTrue(r["summary"][0].startswith("今天沒有族群"))
        self.assertFalse(any(g["featured"] and g["name"] == "矽晶圓" for g in r["groups"] if g["aboveRatio"] >= 0.5))

    def test_main_groups_top_three_by_inst(self) -> None:
        # 老師 10/22（資料基準 10/21）：光學鏡頭 +3.4 萬、AI眼鏡 +0.7 萬、資訊 +0.6 萬（法人買超不用 1 萬張）；彬彬只有 3 檔（+1.1 萬）、鴻家軍／AI 不是熱門族沒入選
        def g(name: str, members: int, above: int, inst5: float, rank: int) -> dict:
            return {"name": name, "members": members, "aboveMa20": above, "aboveRatio": above / members, "inst5": inst5, "rank": rank}

        groups = [g("彬彬", 3, 2, 11402.2, 1), g("AI眼鏡", 6, 3, 6930.7, 9), g("鴻家軍", 7, 6, 45934.8, 17), g("AI", 5, 3, 72417.6, 25),
                  g("光學鏡頭", 13, 8, 34917.5, 41), g("資訊", 21, 13, 6124.8, 46), g("小電組", 20, 13, -27239.6, 42)]
        picked = module.pick_main_groups(groups, {"光學鏡頭", "資訊"})   # 籌碼雷達熱門族
        self.assertEqual([x["name"] for x in picked[:module.MAIN_GROUP_LIMIT]], ["光學鏡頭", "AI眼鏡", "資訊"])
        # 老師 10/21（資料基準 10/20）：AI、光學鏡頭、鴻家軍（鴻家軍收盤排名 7）；資訊 +0.7 萬排第 4 沒入選
        groups = [g("AI", 5, 3, 37132.6, 1), g("彬彬", 3, 2, 3880.3, 6), g("鴻家軍", 7, 5, 15884.0, 7), g("AI眼鏡", 6, 3, 2840.8, 9), g("光學鏡頭", 13, 7, 26285.2, 11), g("資訊", 21, 11, 7275.7, 12)]
        self.assertEqual([x["name"] for x in module.pick_main_groups(groups, {"光學鏡頭", "資訊"})[:3]], ["AI", "光學鏡頭", "鴻家軍"])
        # 老師 10/20（資料基準 10/19）：小電組、鋼鐵、光學鏡頭；能源 3 檔、電腦周邊 4 檔太小
        groups = [g("小電組", 20, 15, 59612.7, 1), g("電腦周邊", 4, 2, 1123.0, 5), g("能源", 3, 2, 854.2, 8), g("鋼鐵", 16, 9, 39522.5, 9), g("資訊", 21, 12, 6707.8, 35), g("光學鏡頭", 13, 7, 33668.4, 43)]
        self.assertEqual([x["name"] for x in module.pick_main_groups(groups, {"資訊", "光學鏡頭"})[:3]], ["小電組", "鋼鐵", "光學鏡頭"])
        # 法人賣超、站上月線不到一半的不算
        self.assertEqual(module.pick_main_groups([g("被動元件", 22, 13, -68687.1, 3), g("機殼", 7, 3, 1000.0, 21)], {"被動元件", "機殼"}), [])

    def test_pick_tech_from_zhuang_1022(self) -> None:
        # 老師 10/22（資料基準 10/21）：真穿月線 8 檔＝列鈞寶、高力、眾達-KY、環科；略過 新復興（營收年增為負）、榮星（成交值 4,703 萬）、友達（營收年增為負）；定穎投控均線分數 6 沒列
        def t(above: float, volume: int, close: float, prev: int, score: int) -> dict:
            return {"crossedMa20": True, "abovePrevMa20Pct": above, "volume": volume, "close": close, "prevScore": prev, "score": score}

        tech = {"6155": t(8.7, 4200, 71.7, 5, 9), "8996": t(4.5, 6064, 1660.0, 6, 10), "4977": t(3.1, 3981, 180.5, 4, 8), "2413": t(7.8, 3257, 57.6, 6, 9),
                "3715": t(3.6, 5539, 135.0, 3, 6), "4909": t(7.82, 1720, 38.75, 5, 9), "1617": t(3.31, 2036, 23.1, 6, 10), "2409": t(3.21, 304087, 37.25, 5, 9),
                "9999": {**t(2.9, 5000, 50.0, 5, 9)}, "9998": {**t(9.0, 300, 50.0, 5, 9)}}   # 站上不到 3%、量不到 500 張的只算「剛站上」
        chips = {"4909": {"revenueYoy": -9.8}, "2409": {"revenueYoy": -4.3}, "1617": {"revenueYoy": 22.0}}
        r = module.pick_tech(list(tech), tech, chips)
        self.assertEqual(r["picks"], ["6155", "8996", "4977", "2413"])     # 跳升 +4、+4、+4、+3，同分站上多的在前
        self.assertEqual(len(r["qualified"]), 8)
        self.assertEqual(r["skipped"], [("4909", "營收年增為負"), ("1617", "成交值 4,703 萬"), ("2409", "營收年增為負")])
        self.assertEqual(len(r["crossed"]), 10)
        # 老師 10/21（資料基準 10/20）：和益均線分數 7 有列、乙盛-KY 均線 6 沒列（站上 3.2%）
        tech = {"6695": t(9.4, 2990, 56.1, 7, 12), "1709": t(6.3, 26681, 56.5, 6, 7), "4770": t(5.6, 1924, 285.0, 5, 10), "3031": t(4.8, 5441, 32.55, 5, 9), "5243": t(3.2, 2362, 97.7, 4, 6)}
        self.assertEqual(module.pick_tech(list(tech), tech, {})["picks"], ["6695", "4770", "3031", "1709"])
        # 算不出均線分數的（日K不夠）不擋
        tech = {"1111": {**t(5.0, 2000, 30.0, 0, 0), "score": None, "prevScore": None}}
        self.assertEqual(module.pick_tech(["1111"], tech, {})["picks"], ["1111"])

    def test_pick_tech_from_zhuang_1029(self) -> None:
        # 老師 10/29（資料基準 10/28）：真穿月線 12 檔，列均線分數最高的 4 檔＝十銓 11、晶豪科 10、永捷 10、鈺創 10；
        # 站上更多的眾達-KY（7.0%、均線 9）、聯光通（8.3%、7）、川寶（6.0%、8）沒列；華宏、中揚光營收年增為負略過
        def t(above: float, volume: int, close: float, prev: int, score: int) -> dict:
            return {"crossedMa20": True, "abovePrevMa20Pct": above, "volume": volume, "close": close, "prevScore": prev, "score": score}

        tech = {"4967": t(5.8, 6174, 279.5, 6, 11), "3006": t(5.9, 18929, 275.5, 7, 10), "4714": t(6.0, 5478, 12.85, 2, 10), "5351": t(5.0, 11728, 114.0, 7, 10),
                "4977": t(7.0, 7098, 187.0, 4, 9), "1595": t(6.0, 3009, 80.5, 5, 8), "4903": t(8.3, 5767, 52.1, 5, 7),
                "8240": t(5.5, 9000, 60.0, 5, 9), "6668": t(4.0, 8000, 50.0, 6, 9)}
        chips = {"8240": {"revenueYoy": -5.0}, "6668": {"revenueYoy": -3.0}}
        r = module.pick_tech(list(tech), tech, chips)
        self.assertEqual(sorted(r["picks"]), ["3006", "4714", "4967", "5351"])
        self.assertEqual([c for c, _w in r["skipped"]], ["8240", "6668"])
        self.assertEqual(len(r["qualified"]), 9)
        # 同分才看站上月線多的：均線 10 的三檔都在，第 5 名以後的（9 分以下）不會因為站上多就擠進來
        self.assertNotIn("4977", r["picks"])

    def test_chips_follow_zhuang_1029(self) -> None:
        # 老師 10/29（資料基準 10/28）籌碼面：濱川（連 6 週）、微星（連 8 週）、今國光（連 4 週、均線 8）、富喬（連 3 週、均線 9、法人賣超）；
        # 榜第 1 名佳龍（連 1 週、成交值 607 萬）、榜第 2 名愛普（IP、只是當天漲幅前 10、不是週籌碼熱門族）都沒列
        self.enterContext(patch.object(module, "CHIPS_DAILY_HOT_GROUPS", 0))
        for d in trading_dates("2026-09-24", 5):
            chips_daily.save_institutional(d, "TSE", [{"code": "2330", "name": "台積電", "foreign": 2_000_000, "trust": 0, "dealer": 0, "total": 2_000_000}], "test")

        def picks(buy: list[str], streaks: dict[str, int], hot: list[str], hot_of: dict[str, str]) -> list[str]:
            fake = {"week": "2026-09-18", "buy": buy, "hot": hot, "hotOf": hot_of,
                    "stocks": {c: {"chip": 5.0, "streak": streaks.get(c, 1), "rank": i + 1} for i, c in enumerate(buy)}}
            with patch.object(module, "radar_week", return_value=fake):
                r = module.build_report("2026-09-24")
            return [x["code"] for x in r["picks"]["chips"]]

        hot_of = {"6182": "矽晶圓", "6207": "設備股", "2330": "半導體"}
        # 榜上前 3 名但不是週籌碼熱門族（設備股只是當天漲幅前 10）、均線和法人也不夠的：不列（愛普）；週籌碼熱門族照列（合晶）
        self.assertEqual(picks(["6182", "6207"], {}, ["矽晶圓"], hot_of), ["6182"])
        self.assertEqual(picks(["6182", "6207"], {}, ["矽晶圓", "設備股"], hot_of), ["6182", "6207"])
        # 榜上前 10 名、連續增加 ≥3 週的，不看均線和法人（今國光、富喬）；連 2 週的不行
        filler = ["9001", "9002", "9003", "9004"]
        self.assertEqual(picks(filler + ["6207"], {"6207": 3}, ["設備股"], hot_of), ["6207"])
        self.assertEqual(picks(filler + ["6207"], {"6207": 2}, ["設備股"], hot_of), [])
        # 成交值不到 5,000 萬的不列（佳龍 607 萬）：把門檻拉到合晶的成交值（1.04 億）以上，合晶就掉出來，台積電（200 億）還在
        with patch.object(module, "CHIPS_MIN_TURNOVER", 20000):
            self.assertEqual(picks(["6182", "2330"], {}, ["矽晶圓", "半導體"], hot_of), ["2330"])

    def test_order_chips_from_zhuang_1022(self) -> None:
        # 老師 10/22（資料基準 10/21）籌碼面：信昌電、緯穎、台表科、今國光；榜外連 5 週、均線 14 的濱川沒列
        stocks = {"6669": {"chip": 21.5, "streak": 1, "rank": 1}, "6173": {"chip": 17.6, "streak": 1, "rank": 2}, "6278": {"chip": 7.0, "streak": 5, "rank": 6},
                  "1569": {"chip": 3.2, "streak": 5, "rank": None}, "6209": {"chip": 5.4, "streak": 3, "rank": 14}}
        scores = {"6669": 2, "6173": 7, "6278": 15, "1569": 14, "6209": 15}
        hot = {"6173": 0, "6669": 1, "6278": 3, "6209": 3}   # 週籌碼族群排名：被動元件、機殼、資訊、光學鏡頭
        allc, shown = module.order_chips(list(stocks), stocks, scores, {"6669", "6173", "6278"}, hot)
        self.assertEqual(shown, ["6173", "6669", "6278", "6209"])
        self.assertEqual(len(allc), 5)
        # 沒有榜外的競爭者時，榜外連續週數多的照列（10/15 精誠連 8 週）
        self.assertEqual(module.order_chips(["1569"], stocks, scores, set(), {})[1], ["1569"])

    def test_build_report(self) -> None:
        # 資金主軸要至少 5 檔（真實盤）；這份測試族群只有 1 檔，把門檻降下來才測得到「精選族群」的判讀文字
        with patch.object(module, "MAIN_GROUP_MIN_MEMBERS", 1):
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
        self.assertEqual(tech[0]["tag"], "站上 +4.0%")
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
        self.assertEqual([x["tag"] for x in r["picks"]["full"]], ["6→15"])
        self.assertEqual(r["notes"]["jumpTop"], [{"code": "6182", "name": "合晶", "from": 6, "to": 15}])
        self.assertEqual(r["notes"]["sustained"], [])   # 合晶是跳升進來的，不算續強（見下面 SustainedTests 另外測沒跳升的續強情形）

    def test_chips_follow_radar(self) -> None:
        # 老師的籌碼面：籌碼暴增榜前 10 名、熱門族；連續增加週數多的優先，同週數均線分數高的在前；法人 5 日賣超的照列（只標風險）
        self.enterContext(patch.object(module, "CHIPS_DAILY_HOT_GROUPS", 0))   # 這個測試只看週籌碼熱門族
        for d in trading_dates("2026-09-24", 5):
            chips_daily.save_institutional(d, "TSE", [{"code": "2330", "name": "台積電", "foreign": 2_000_000, "trust": 0, "dealer": 0, "total": 2_000_000}], "test")
        hot_all = {"6182": "矽晶圓", "6207": "設備股", "2330": "半導體"}   # 熱門族依週籌碼族群排名：矽晶圓、設備股、半導體

        def picks(buy: list[str], streaks: dict[str, int], hot_of: dict[str, str]) -> list[str]:
            fake = {"week": "2026-09-18", "buy": buy, "hot": list(dict.fromkeys(hot_of.values())), "hotOf": hot_of,
                    "stocks": {c: {"chip": 5.0, "streak": streaks.get(c, 1), "rank": i + 1} for i, c in enumerate(buy)}}
            with patch.object(module, "radar_week", return_value=fake):
                r = module.build_report("2026-09-24")
            return [(x["code"], x["tag"], x["hot"], x["group"]) for x in r["picks"]["chips"]]

        # 榜上前 3 名一定列、照名次排；雷科法人賣超照列（老師 10/19：緯穎法人 5 日 -10.6% 也列，卡片上標風險）
        self.assertEqual(picks(["6182", "6207", "2330"], {"6182": 1, "6207": 5, "2330": 3}, hot_all),
                         [("6182", "連 1 週", True, "矽晶圓"), ("6207", "連 5 週", True, "設備股"), ("2330", "連 3 週", True, "半導體")])
        # 前 3 名之後：連續週數多的在前（合晶均線 15、台積電法人買超很多，兩個都夠格）
        filler = ["9001", "9002", "9003"]
        self.assertEqual([x[0] for x in picks(filler + ["6182", "2330"], {"6182": 1, "2330": 3}, hot_all)], ["2330", "6182"])
        # 同週數：均線分數高的（合晶 15）在前（前面墊 3 檔榜上前 3 名，這兩檔才排得到「前 3 名之後」）
        self.assertEqual([x[0] for x in picks(filler + ["2330", "6182"], {"6182": 2, "2330": 2}, hot_all)], ["6182", "2330"])
        # 前 3 名一定列（不看均線、法人）；前 3 名之後均線 <12 又沒有法人買超的不列
        self.assertEqual([x[0] for x in picks(["6182", "6207", "2330"], {}, hot_all)], ["6182", "6207", "2330"])
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
        # 榜外的一定要均線 ≥12（允強均線 7、敦陽科均線 8 老師沒列）：這份測試資料裡台積電均線不夠，單獨一檔不列
        self.assertEqual(picks({"2330": {"chip": 4.0, "streak": 5, "rank": None}}), [])
        self.enterContext(patch.object(module, "CHIPS_MIN_SCORE", 0))   # 下面幾個斷言測排序、處置、出獄、門檻，不看均線
        # 都夠的話，連續週數多的在前
        self.assertEqual([c for c, _ in picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}, "2330": {"chip": 4.0, "streak": 5, "rank": None}})], ["2330", "6182"])
        # 處置中的不列（像 10/13 的聯茂）
        self.assertEqual([c for c, _ in picks({"6182": {"chip": 3.5, "streak": 3, "rank": None}, "2330": {"chip": 4.0, "streak": 5, "rank": None}}, {"2330"})], ["6182"])
        # 門檻用兩位小數比：2.06 的（微星）不列，2.14 的（精誠）列
        self.assertEqual(picks({"6182": {"chip": 2.1, "chip2": 2.06, "streak": 7, "rank": None}}), [])
        self.assertEqual(picks({"6182": {"chip": 2.1, "chip2": 2.14, "streak": 8, "rank": None}}), [("6182", "連 8 週")])
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
        self.assertEqual(watch(2.4), [])   # 3008 不在族群表（老師 10/22：台星科、浩鼎不在族群表沒列）
        # 報告用帳本（全市場），不是只看族群表
        r = module.build_report("2026-09-24")
        self.assertEqual((r["tiles"]["upcoming"], r["tiles"]["releasing"]), (2, 1))
        self.assertEqual(r["readDate"], "2026-09-29")

    def test_high_risk_includes_volume_already_passed(self) -> None:
        # 老師 10/22 高機率處置：科嶠「成交量 > 1,729 張 會被關（今 3259 張）」、金居「成交量 > 12,625 張（今 15873 張）」——門檻已經被今天的量超過；
        # 第四款要收盤價配合的（價格那條還沒到）不算
        import disposition_jail as jail

        jail.save_punishes([{"code": "6182", "start": "2026-09-29", "end": "2026-10-09", "market": "TSE", "name": "N6182", "announce": "2026-09-24",
                             "condition": "連續三次", "measure": "第一次處置", "days": 10, "minutes": 5, "content": ""}])
        vol = lambda lots, today, text, passed=True: {"kind": "volume", "lots": lots, "today": today, "passed": passed, "clause": "第六款", "text": text}  # noqa: E731
        suspects = [
            {"code": "4542", "name": "科嶠", "market": "上櫃", "jailed": False, "high": False, "status": "連3", "lines": [vol(1729, 3259, "成交量 > 1,729 張 會被關")]},
            {"code": "8358", "name": "金居", "market": "上櫃", "jailed": False, "high": False, "status": "連3", "lines": [vol(12625, 15873, "成交量 > 12,625 張 會被關")]},
            {"code": "2033", "name": "佳大", "market": "上市", "jailed": False, "high": False, "status": "連2", "lines": [vol(11999, 3629, "成交量 > 11,999 張 會被關", False)]},
            {"code": "3037", "name": "欣興", "market": "上市", "jailed": False, "high": False, "status": "連2", "lines": [vol(5000, 9000, "收盤 > 69.14 元（+1.83%）且成交量 > 5,000 張 會被關")]},
            {"code": "5205", "name": "中茂", "market": "上櫃", "jailed": False, "high": True, "status": "連2", "lines": [{"kind": "price", "text": "漲停都會被關（6 日累積已達第一款門檻）"}]},
            {"code": "9999", "name": "已關", "market": "上市", "jailed": True, "high": True, "status": "連2", "lines": []},
        ]
        with patch.object(jail, "build_payload", return_value={"status": "ok", "dataDate": "2026-09-24", "suspects": suspects}):
            d = module.disposition_from_jail("2026-09-24")
        self.assertEqual([x["code"] for x in d["high"]], ["4542", "8358", "5205"])
        self.assertEqual(d["high"][0]["lines"], ["成交量 > 1,729 張 會被關（今 3259 張）"])
        self.assertEqual(d["high"][2]["lines"], ["漲停都會被關（6 日累積已達第一款門檻）"])

    def test_summary_lines_follow_zhuang_1022(self) -> None:
        # 老師 10/22 今日摘要：均線新滿分寫前 2 檔（精誠、台表科）、風險提示最多 4 檔、沒有新公布年增達標的月營收就不寫月營收那一點（也不退回寫整月累計首選）
        import revenue_rank

        self.assertEqual((module.SUMMARY_FULL_NAMES, module.RISK_LINE_LIMIT), (2, 4))
        base = {"month": "2026-09", "asOf": "2026-09-24", "published": 3, "yoyHigh": 2, "qualified": 1, "rule": {"yoy": 30},
                "picks": [{"code": "6182", "name": "合晶", "yoy": 40.0}], "since": "2026-09-23", "newPublished": 5, "newYoyHigh": 0, "newQualified": 0, "newPicks": []}
        with patch.object(revenue_rank, "report_section", return_value=base):
            r = module.build_report("2026-09-24")
        self.assertFalse(any(line.startswith("營收面") or line.startswith("月營收") for line in r["summary"]))
        with patch.object(revenue_rank, "report_section", return_value={**base, "newYoyHigh": 1, "newQualified": 1, "newPicks": [{"code": "6182", "name": "合晶", "yoy": 40.0}]}):
            r = module.build_report("2026-09-24")
        self.assertIn("月營收：09/23 之後到 09/24 公布 5 檔，年增 ≥30% 有 1 檔；首選 合晶", r["summary"])

    def test_chips_watch_needs_group_stock(self) -> None:
        # 老師 10/22 觀察名單只有勤凱（出獄第 5 天、籌碼 +3.5%）；不在族群表的（台星科、浩鼎）不列
        out = {"nextTradingDay": "2026-09-25", "upcoming": [], "releasing": [], "active": [], "high": [],
               "watch": [{"code": "6182", "name": "合晶", "daysOut": 5}, {"code": "3265", "name": "台星科", "daysOut": 2}]}
        fake = {"week": "2026-09-18", "buy": [], "hot": [], "hotOf": {},
                "stocks": {"6182": {"chip": 3.5, "chip2": 3.5, "streak": 1, "rank": None}, "3265": {"chip": 5.8, "chip2": 5.8, "streak": 1, "rank": None}}}
        with patch.object(module, "disposition_from_jail", return_value=out), patch.object(module, "radar_week", return_value=fake):
            r = module.build_report("2026-09-24")
        self.assertEqual([(x["code"], x["daysOut"], x["chip"]) for x in r["chipsWatch"]], [("6182", 5, 3.5)])

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
