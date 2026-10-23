"""指數均線分數：跟個股同一套 15 分算法、近 3 日平均、今天／明天兩格、分水嶺 6 分；端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import database
import fund_umbrella
import index_score as module


def days(n: int, end: date = date(2026, 10, 22)) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


class IndexScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def put(self, series: str, dates: list[str], closes: list[float]) -> None:
        with database.get_connection() as c:
            fund_umbrella._schema(c)
            c.executemany("INSERT INTO fund_umbrella_series (series, trade_date, value) VALUES (?, ?, ?)", [(series, d, v) for d, v in zip(dates, closes)])

    def test_rising_is_full_marks_and_falling_is_zero(self) -> None:
        ds = days(400)
        self.put("tpex", ds, [100 + i * 0.5 for i in range(400)])
        self.put("taiex", ds, [500 - i * 0.5 for i in range(400)])
        out = module.payload()
        self.assertEqual(out["status"], "ok")
        otc, tx = out["indexes"]["otc"], out["indexes"]["taiex"]
        self.assertEqual((otc["name"], otc["asOf"], otc["today"]["avg"], otc["next"]["avg"], otc["next"]["label"]), ("櫃買", ds[-1], 15.0, 15.0, "多"))
        self.assertEqual((tx["name"], tx["today"]["avg"], tx["next"]["avg"], tx["next"]["label"]), ("加權", 0.0, 0.0, "空"))
        self.assertEqual((otc["next"]["basis"], otc["today"]["basis"]), (ds[-1], ds[-2]))
        self.assertEqual(len(otc["daily"]), module.DAILY_SHOWN)

    def test_three_day_average_and_two_cells(self) -> None:
        """10/20、10/21、10/22 日分數 3、4、3（櫃買指數 10/22 的真實情況）：明天＝(3+4+3)/3、今天＝前一天算的 (3+3+4)/3，都是 3.33、都是空。"""
        ds = days(300)
        closes = [100 + i * 0.5 for i in range(300)]
        with patch.object(module, "official_parts") as parts:
            seq = [(0b000001, 0, 2), (0b000011, 0, 2), (0b000001, 0, 2), (0b000001, 0, 2), (0b000011, 0, 2), (0b000001, 0, 2)]
            parts.side_effect = seq
            got = module.index_payload(ds, closes)
        self.assertEqual([r["score"] for r in got["daily"]], [3, 4, 3, 3, 4, 3][-module.DAILY_SHOWN:])
        self.assertEqual((got["next"]["avg"], got["next"]["label"]), (3.33, "空"))
        self.assertEqual((got["today"]["avg"], got["today"]["label"]), (3.33, "空"))

    def test_table_block(self) -> None:
        """指數對照表：收盤、20／60／120 日線、近半年高點（盤中最高優先）與距高點。"""
        ds = days(300)
        closes = [100.0] * 280 + [100 + i for i in range(20)]       # 最後 20 天一路漲到 119
        highs = {ds[-1]: 130.0, ds[-30]: 140.0}
        t = module.table_block(ds, closes, highs)
        self.assertEqual((t["close"], t["high"], t["highDate"], t["highIntraday"]), (119.0, 140.0, ds[-30], True))
        self.assertAlmostEqual(t["fromHigh"], round((119 / 140 - 1) * 100, 1))
        self.assertEqual((t["ma20"], t["ma60"], t["ma120"]), (round(sum(closes[-20:]) / 20, 2), round(sum(closes[-60:]) / 60, 2), round(sum(closes[-120:]) / 120, 2)))
        t = module.table_block(ds, closes, None)                     # 沒有盤中高點：用收盤
        self.assertEqual((t["high"], t["highDate"], t["highIntraday"]), (119.0, ds[-1], False))
        self.assertEqual(module.table_block(ds[:50], closes[:50])["ma60"], None)

    def test_payload_has_table_and_uses_highs(self) -> None:
        ds = days(300)
        self.put("tpex", ds, [100 + i * 0.5 for i in range(300)])
        with database.get_connection() as c:
            c.execute("INSERT INTO fund_umbrella_series (series, trade_date, value) VALUES ('tpex_high', ?, 999.0)", (ds[-5],))
        t = module.payload()["indexes"]["otc"]["table"]
        self.assertEqual((t["high"], t["highDate"], t["highIntraday"]), (999.0, ds[-5], True))

    def test_threshold(self) -> None:
        self.assertEqual((module.label(5.99), module.label(6.0), module.label(6.01), module.label(9)), ("空", "平", "多", "多"))

    def test_missing_and_short(self) -> None:
        self.assertEqual(module.payload()["status"], "missing")
        ds = days(100)
        self.put("tpex", ds, [100.0 + i for i in range(100)])      # 不到 240 根：算不出分數
        self.assertEqual(module.payload()["status"], "missing")

    def test_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        import persistent_app

        ds = days(300)
        self.put("tpex", ds, [100 + i * 0.5 for i in range(300)])
        with TestClient(persistent_app.app) as client:
            body = client.get("/api/hub/index-score").json()
        self.assertEqual((body["status"], body["threshold"], body["indexes"]["otc"]["next"]["avg"]), ("ok", 6, 15.0))


if __name__ == "__main__":
    unittest.main()
