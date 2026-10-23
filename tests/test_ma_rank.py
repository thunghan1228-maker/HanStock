"""均線分數排行：排法（總分→12 項本體分→股號）、昨天名次、連續上榜、族群前十、前十名常客（同分並列全算）、查詢、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
import heilong_backtest
import ma_rank as module

GROUPS = {
    "甲": [("1111", "一號"), ("2222", "二號"), ("3333", "三號")],
    "乙": [("2222", "二號"), ("4444", "四號")],
    "千元": [("5555", "五號")],
    "股期標的": [("9999", "九號")],
    "金融股": [("2881", "富邦金")],
    "散熱": [("2241", "艾姆勒")],
}
D = ["2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08"]
ALL = 0b111111
TW = timezone(timedelta(hours=8))


def bits(n: int) -> int:
    """前 n 個天期亮燈。"""
    return (1 << n) - 1


# {日期: {代號: (站上幾條, 新高幾個, 排列, 收盤, 漲跌%)}}
SCORES = {
    D[0]: {"1111": (6, 6, 3, 10, 1.0), "2222": (5, 5, 2, 20, 0.0), "3333": (2, 2, 0, 30, -1.0), "4444": (6, 6, 3, 40, 2.0),
           "5555": (6, 6, 3, 1000, 0.0), "9999": (6, 6, 3, 9, 0.0)},
    D[1]: {"1111": (6, 6, 3, 10, 1.0), "2222": (6, 6, 2, 20, 0.0), "3333": (3, 2, 0, 30, -1.0), "4444": (6, 6, 1, 40, 2.0),
           "5555": (6, 6, 3, 1000, 0.0), "9999": (6, 6, 3, 9, 0.0)},
    D[2]: {"1111": (6, 6, 3, 10, 1.0), "2222": (6, 6, 3, 20, 0.0), "3333": (6, 5, 3, 30, -1.0), "4444": (6, 6, 2, 40, 2.0),
           "5555": (5, 5, 3, 1000, 0.0), "9999": (6, 6, 3, 9, 0.0)},
    D[3]: {"1111": (6, 6, 3, 10, 1.0), "2222": (6, 6, 3, 20, 3.0), "3333": (6, 5, 3, 30, -1.0), "4444": (6, 6, 2, 40, 2.0),
           "5555": (4, 4, 0, 1000, 0.0), "9999": (6, 6, 3, 9, 0.0), "8888": (6, 6, 3, 50, 0.0),
           "2881": (6, 6, 3, 80, 0.0), "2241": (6, 6, 3, 90, 0.0)},     # 金融股、艾姆勒不算
}


class MaRankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.groups_patch = patch.object(module, "STOCK_GROUPS", GROUPS)
        self.groups_patch.start()
        self.top_patch = patch.object(module, "TOP_N", 3)
        self.top_patch.start()
        # 預設「現在」是 10/08 收盤後（今天的整表已經有）：strength() 不會去算盤中即時分數、更不會打報價中心
        self.now = datetime(2026, 10, 8, 18, 0, tzinfo=TW)
        self.now_patch = patch.object(module, "_tw_now", lambda: self.now)
        self.now_patch.start()
        module._live_cache.update({"day": None, "base": None, "at": 0.0, "data": None})
        module._live_hist.update({"day": None, "closes": {}})
        database.initialize_database()
        rows = []
        for d, recs in SCORES.items():
            for code, (ma, hi, align, close, chg) in recs.items():
                rows.append((d, code, close, close, close, close, 1000, chg, ma + hi + align, bits(ma), bits(hi), align))
        rows.append((D[3], "7777", 5, 5, 5, 5, 1000, 0.0, None, None, None, None))   # 日K不足 240 根：沒分數
        with database.get_connection() as c:
            heilong_backtest._schema(c)
            c.executemany(
                """INSERT INTO heilong_daily (trade_date, stock_code, open, high, low, close, volume, change_pct, score2, ma_bits, hi_bits, align_n)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        module._cache.update({"key": None, "data": None})

    def tearDown(self) -> None:
        self.now_patch.stop()
        self.top_patch.stop()
        self.groups_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()
        module._cache.update({"key": None, "data": None})

    def test_ranking(self) -> None:
        out = module.ranking()
        self.assertEqual((out["date"], out["prevDate"], out["n"], out["dates"]), (D[3], D[2], 5, D))   # 股期標的、族群外的不算
        # 1111、2222 都 15 分 12 本體 → 股號小的先；4444 14 分但本體 12 排在 3333（14 分、本體 11）前面
        self.assertEqual([s["code"] for s in out["stocks"]], ["1111", "2222", "4444"])
        first = out["stocks"][0]
        self.assertEqual((first["sum"], first["total"], first["extra"], first["ma"], first["hi"], first["grp"]),
                         (15, 12, 3, [True] * 6, [True] * 6, "甲"))
        two = out["stocks"][1]
        self.assertEqual((two["grp"], two["prev"], two["streak"], two["chg"]), ("甲/乙", 2, 3, 3.0))   # 10/07 第 2、10/06 第 3、10/02 第 4（榜外）
        self.assertEqual((first["prev"], first["streak"]), (1, 4))
        four = out["stocks"][2]
        self.assertEqual((four["sum"], four["total"], four["extra"], four["prev"], four["streak"]), (14, 12, 2, 3, 2))
        self.assertEqual(four["ma"], [True] * 6)
        # 族群：千元不列；甲＝(15+15+14)/3、乙＝(15+14)/2
        groups = {g["name"]: g for g in out["groups"]}
        self.assertEqual(set(groups), {"甲", "乙"})
        self.assertEqual((groups["甲"]["avg"], groups["甲"]["n"], groups["甲"]["max"], groups["甲"]["rank"]), (14.67, 3, 15, 1))
        self.assertEqual((groups["乙"]["avg"], groups["乙"]["rank"], groups["乙"]["chgRank"], groups["甲"]["chgRank"]), (14.5, 2, 1, 2))
        self.assertEqual((groups["甲"]["prev"], groups["乙"]["prev"]), (1, 2))
        # 10/06：2222（14 分）排第 3，4444 13 分掉出前 3
        old = module.ranking(D[1])
        self.assertEqual([s["code"] for s in old["stocks"]], ["1111", "5555", "2222"])
        self.assertEqual(old["stocks"][1]["grp"], "")      # 只在千元：顯示時不列族群
        self.assertIsNone(module.ranking(D[0])["stocks"][0]["prev"])
        with self.assertRaises(LookupError):
            module.ranking("2026-09-01")

    def test_hits(self) -> None:
        out = module.hits(10, 5, 20)
        self.assertEqual((out["ndays"], out["to"], out["from"]), (4, D[3], D[0]))
        rows = {r["code"]: r for r in out["rows"]}
        # 每天取前 5 名（全部 5 檔都在）→ 全部 4/4
        self.assertEqual({c: r["hits"] for c, r in rows.items()}, {"1111": 4, "2222": 4, "3333": 4, "4444": 4, "5555": 4})
        out = module.hits(10, 5, 20, D[1])
        self.assertEqual(out["ndays"], 2)
        with patch.object(module, "HIT_TOPS", (1, 5, 10, 20, 30)):
            out = module.hits(10, 1, 20)
        rows = {r["code"]: r for r in out["rows"]}
        # 取前 1 名但同分並列全算：10/02 1111、4444、5555 都 15；10/06 1111、5555；10/07 1111、2222；10/08 1111、2222
        self.assertEqual({c: r["hits"] for c, r in rows.items()}, {"1111": 4, "5555": 2, "2222": 2, "4444": 1})
        self.assertEqual([r["code"] for r in out["rows"]], ["1111", "5555", "2222", "4444"])   # 同次數：先上榜的先列
        self.assertEqual((rows["2222"]["r5"], rows["2222"]["sum"], rows["2222"]["rank"]), (2, 15, 2))
        self.assertEqual(rows["4444"]["trail"][0], [D[0], 2, 15, True])
        self.assertEqual(rows["4444"]["trail"][3], [D[3], 3, 14, False])
        with self.assertRaises(ValueError):
            module.hits(7, 10, 20)

    def test_strength(self) -> None:
        # 今日強勢股：族群分數前十大裡均線分數 ≥12；弱勢股：倒數十名裡 ≤3（族群平均分低的在前）
        out = module.strength("strong")
        self.assertEqual((out["date"], [g["name"] for g in out["groups"]], out["count"]), (D[3], ["甲", "乙"], 5))
        self.assertEqual([r["code"] for r in out["groups"][0]["rows"]], ["1111", "2222", "3333"])
        with patch.object(module, "STRONG_MIN", 15):
            self.assertEqual([[r["code"] for r in g["rows"]] for g in module.strength("strong")["groups"]], [["1111", "2222"], ["2222"]])
        self.assertEqual(module.strength("weak")["count"], 0)
        with patch.object(module, "WEAK_MAX", 14):
            weak = module.strength("weak")
            self.assertEqual([g["name"] for g in weak["groups"]], ["乙", "甲"])
            self.assertEqual([[r["code"] for r in g["rows"]] for g in weak["groups"]], [["4444"], ["3333"]])
        with self.assertRaises(ValueError):
            module.strength("x")

    def _live_setup(self, prices: dict[str, float], quote_date: str = "2026-10-12"):
        """10/12（週一）10:30：歷史日K 300 根——強勢股（1111、2222、4444、5555）一路漲、3333 一路跌；prices＝現價。"""
        def bars(code: str) -> list:
            up = code != "3333"
            return [(f"d{i:03d}", 1.0, 1.0, 1.0, float(i + 1) if up else float(300 - i), 1000) for i in range(300)]

        self.now = datetime(2026, 10, 12, 10, 30, tzinfo=TW)
        payload = {"status": "ok", "quoteDate": quote_date, "quoteTime": "10:30:00",
                   "quotes": {c: {"price": p, "prevClose": 100.0, "changePercent": p - 100.0} for c, p in prices.items()}}
        return (patch("brew_launch_history.group_quotes_payload", return_value=payload),
                patch.object(module, "_load_bars", side_effect=lambda codes, **kw: {c: bars(c) for c in codes}))

    def test_strength_live(self) -> None:
        # 盤中：現價當今天收盤重算——1111、2222、4444、5555 站上全部均線＋創新高＝15 分，3333 跌到 0.5＝0 分
        q, b = self._live_setup({"1111": 400.0, "2222": 400.0, "3333": 0.5, "4444": 400.0, "5555": 400.0})
        with q, b:
            out = module.strength("strong")
            self.assertEqual((out["live"], out["date"], out["latest"], out["asOf"]), (True, "2026-10-12", D[3], "10:30:00"))
            # 乙＝(15+15)/2＝15、甲＝(15+15+0)/3＝10：族群名次乙在前；3333 只有 0 分不進強勢股；千元不列
            self.assertEqual([(g["name"], g["avg"], g["rank"]) for g in out["groups"]], [("乙", 15.0, 1), ("甲", 10.0, 2)])
            self.assertEqual([[r["code"] for r in g["rows"]] for g in out["groups"]], [["2222", "4444"], ["1111", "2222"]])
            self.assertEqual((out["groups"][0]["rows"][0]["sum"], out["groups"][0]["rows"][0]["chg"], out["groups"][0]["rows"][0]["close"]), (15, 300.0, 400.0))
            weak = module.strength("weak")
            self.assertEqual((weak["live"], [[r["code"] for r in g["rows"]] for g in weak["groups"]]), (True, [["3333"], []]))
            # 指定日期＝要正式的收盤分數，不算即時
            self.assertNotIn("live", module.strength("strong", D[3]))

    def test_strength_live_falls_back(self) -> None:
        # 報價還是昨天的（開盤前／休市）：照收盤整表
        q, b = self._live_setup({"1111": 400.0, "2222": 400.0, "3333": 0.5, "4444": 400.0, "5555": 400.0}, quote_date="2026-10-09")
        with q, b:
            out = module.strength("strong")
            self.assertNotIn("live", out)
            self.assertEqual(out["date"], D[3])
        # 收盤整表已經有今天、或不是交易日（週六）、或 09:05 以前：都不算即時
        for now in (datetime(2026, 10, 8, 10, 0, tzinfo=TW), datetime(2026, 10, 10, 10, 0, tzinfo=TW), datetime(2026, 10, 12, 9, 0, tzinfo=TW)):
            q, b = self._live_setup({"1111": 400.0})
            self.now = now
            with q as quotes, b:
                module._live_cache.update({"day": None, "base": None, "at": 0.0, "data": None})
                self.assertNotIn("live", module.strength("strong"))
                quotes.assert_not_called()
        # 報價太少（5 檔只有 1 檔）也不算
        q, b = self._live_setup({"1111": 400.0})
        with q, b:
            module._live_cache.update({"day": None, "base": None, "at": 0.0, "data": None})
            self.assertNotIn("live", module.strength("strong"))

    def test_cache_follows_rebuild(self) -> None:
        """黑龍表整張重算（日期、筆數都沒變）也要換新快取，不然舊分數會一直留著。"""
        first = module.ranking()
        with patch.object(module, "_heilong_status", return_value={"builtAt": "2026-10-24T01:00:00+08:00"}):
            key_after = module._cache_key()
        self.assertNotEqual(module._cache["key"], key_after)
        with database.get_connection() as c:
            c.execute("UPDATE heilong_daily SET score2 = 3 WHERE trade_date = ? AND stock_code = ?", (D[3], "1111"))
        with patch.object(module, "_heilong_status", return_value={"builtAt": "2026-10-24T01:00:00+08:00"}):
            again = module.ranking()
        self.assertEqual(first["stocks"][0]["sum"], 15)
        self.assertNotEqual([s["code"] for s in again["stocks"]][0], "1111")     # 1111 被改成 3 分，掉出第一名

    def test_teacher_view_drops_absent_codes(self) -> None:
        """對照老師名單：拿掉老師名單沒有的股票後重排——名次、族群平均、前十名常客都照剩下的算；預設（不帶）完全不變。"""
        with patch.object(module, "TEACHER_ABSENT", frozenset({"1111"})):
            normal = module.ranking()
            view = module.ranking(teacher=True)
            hit_view = module.hits(teacher=True)
        self.assertEqual([x["code"] for x in normal["stocks"]], ["1111", "2222", "4444"])
        self.assertEqual([x["code"] for x in view["stocks"]], ["2222", "4444", "3333"])        # 1111 拿掉，3333 遞補進前 3
        self.assertEqual((view["teacher"], normal["teacher"], view["n"], normal["n"]), (True, False, 4, 5))
        self.assertEqual(view["stocks"][0]["rank"], 1)
        self.assertEqual(view["stocks"][0]["prev"], 1)         # 昨天名次也照拿掉之後的名單排（昨天 1111 在榜首，拿掉後 2222 是第 1）
        groups = {g["name"]: g for g in view["groups"]}
        self.assertEqual((groups["甲"]["n"], groups["甲"]["avg"]), (2, round((15 + 14) / 2, 2)))   # 甲 3 檔拿掉 1111 剩 2222、3333
        self.assertNotIn("1111", [r["code"] for r in hit_view["rows"]])
        self.assertIn("1111", [r["code"] for r in module.hits()["rows"]])
        self.assertEqual(module.ranking()["n"], 5)              # 切過去再切回來，原本的資料沒被動到

    def test_bias_columns(self) -> None:
        """月乖離／季乖離：收盤 ÷ 20／60 日均線 − 1（%）；日K不夠或當天沒日K就是 None；讀日K失敗不影響排行。"""
        def bar(day: str, close: float) -> tuple:
            return (day, close, close, close, close, 100)

        days = [(datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(70)]
        up = [bar(d, 100 + i) for i, d in enumerate(days)]                   # 一路漲：收盤 169、MA20=159.5、MA60=139.5
        short = [bar(d, 50) for d in days[-10:]]                              # 只有 10 根：算不出
        stale = [bar(d, 80) for d in days[:-1]]                               # 最後一根不是查的那天
        fake = {"1111": up, "2222": short, "4444": stale}
        rows = [{"code": "1111"}, {"code": "2222"}, {"code": "4444"}, {"code": "3333"}]
        with patch.object(module, "_load_bars", return_value=fake):
            module._attach_bias(rows, days[-1])
        self.assertEqual((rows[0]["b20"], rows[0]["b60"]), (round((169 / 159.5 - 1) * 100, 1), round((169 / 139.5 - 1) * 100, 1)))
        self.assertEqual((rows[1]["b20"], rows[1]["b60"]), (None, None))
        self.assertEqual((rows[2]["b20"], rows[2]["b60"]), (None, None))
        self.assertEqual((rows[3]["b20"], rows[3]["b60"]), (None, None))
        with patch.object(module, "_load_bars", side_effect=RuntimeError("沒日K")):
            rows = [{"code": "1111"}]
            module._attach_bias(rows, days[-1])
        self.assertEqual((rows[0]["b20"], rows[0]["b60"]), (None, None))
        with patch.object(module, "_load_bars", return_value={"1111": up}):
            out = module.ranking(D[3])
        self.assertIn("b20", out["stocks"][0])

    def test_query(self) -> None:
        out = module.query(code="2222")
        self.assertEqual((out["group"], [r["code"] for r in out["rows"]]), ("甲/乙", ["1111", "2222", "4444", "3333"]))
        self.assertEqual([g["name"] for g in out["groups"]], ["甲", "乙"])
        out = module.query(group="乙")
        self.assertEqual([r["code"] for r in out["rows"]], ["2222", "4444"])
        self.assertEqual(module.query(group="甲")["rows"][0]["rank"], 1)
        out = module.query(code="8888")      # 族群外：只列自己
        self.assertEqual((out["rows"][0]["code"], out["rows"][0]["sum"], out["rows"][0]["grp"]), ("8888", 15, ""))
        with self.assertRaises(LookupError):
            module.query(code="7777")         # 沒分數
        with self.assertRaises(LookupError):
            module.query(group="不存在")
        with self.assertRaises(ValueError):
            module.query()

    def test_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        client = TestClient(persistent_app.app)
        r = client.get("/api/hub/ma-rank")
        self.assertEqual((r.status_code, r.json()["date"]), (200, D[3]), r.text[:300])
        self.assertEqual(client.get("/api/hub/ma-rank", params={"date": D[1]}).json()["date"], D[1])
        self.assertEqual(client.get("/api/hub/ma-rank", params={"date": "2026-01-02"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/ma-rank", params={"date": "10/08"}).status_code, 422)
        r = client.get("/api/hub/ma-rank/hits", params={"days": 20, "top": 10, "show": 20})
        self.assertEqual((r.status_code, r.json()["ndays"]), (200, 4))
        self.assertEqual(client.get("/api/hub/ma-rank/hits", params={"days": 3}).status_code, 422)
        r = client.get("/api/hub/ma-rank/q", params={"code": "4444"})
        self.assertEqual((r.status_code, r.json()["group"]), (200, "乙"))
        self.assertEqual(client.get("/api/hub/ma-rank/q", params={"group": "沒有"}).status_code, 404)
        self.assertEqual(client.get("/api/hub/ma-rank/q").status_code, 422)
        r = client.get("/api/hub/ma-rank/strength", params={"side": "strong"})
        self.assertEqual((r.status_code, r.json()["count"]), (200, 5))
        self.assertEqual(client.get("/api/hub/ma-rank/strength", params={"side": "bad"}).status_code, 422)

    def test_empty(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM heilong_daily")
        module._cache.update({"key": None, "data": None})
        with self.assertRaises(LookupError):
            module.ranking()


if __name__ == "__main__":
    unittest.main()
