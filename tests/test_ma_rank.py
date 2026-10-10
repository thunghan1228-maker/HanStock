"""均線分數排行：排法（總分→12 項本體分→股號）、昨天名次、連續上榜、族群前十、前十名常客（同分並列全算）、查詢、端點。"""

from __future__ import annotations

import tempfile
import unittest
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
}
D = ["2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08"]
ALL = 0b111111


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
           "5555": (4, 4, 0, 1000, 0.0), "9999": (6, 6, 3, 9, 0.0), "8888": (6, 6, 3, 50, 0.0)},
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

    def test_empty(self) -> None:
        with database.get_connection() as c:
            c.execute("DELETE FROM heilong_daily")
        module._cache.update({"key": None, "data": None})
        with self.assertRaises(LookupError):
            module.ranking()


if __name__ == "__main__":
    unittest.main()
