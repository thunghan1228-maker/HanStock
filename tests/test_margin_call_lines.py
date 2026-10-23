"""融資斷頭線：用老師 10/22「斷頭線就在腳下」的數字對答案。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import brew_launch
import brew_launch_history
import database
import margin_call_lines as module
import persistent_app


def make_bars(highs_closes: list[tuple[float, float]], start: str = "2026-09-01") -> list[tuple[str, float, float]]:
    d = date.fromisoformat(start)
    out = []
    for high, close in highs_closes:
        out.append((d.isoformat(), high, close))
        d += timedelta(days=1)
    return out


def scenario(month_high: float, closes_at_high: list[float], last_close: float) -> list[tuple[str, float, float]]:
    """24 根：前面 4 根＋月高那天（最後 5 個收盤給 closes_at_high）→ 月高在視窗內，之後掉到 last_close。"""
    pre = [(closes_at_high[0], closes_at_high[0])] * 3 + [(c * 1.0, c) for c in closes_at_high[:-1]]
    head = [(closes_at_high[0], closes_at_high[0])] * 10
    peak = [(month_high, closes_at_high[-1])]
    tail = [(last_close * 1.05, last_close + 5)] * 8 + [(last_close * 1.05, last_close)]
    return make_bars(head + pre[-4:] + peak + tail)


class LinesTests(unittest.TestCase):
    def test_nanya_and_universal_wafer(self) -> None:
        # 南亞科（上市）：月高 537、月高那天五日均價 525.4、今收 414 → A 418.9 已破、B 409.8 差 1.0%
        bars = scenario(537.0, [520.0, 524.0, 526.0, 527.0, 530.0], 414.0)
        r = module.lines_for(bars, "TSE")
        self.assertEqual((r["monthHigh"], r["ma5"], r["a"], r["b"]), (537.0, 525.4, 418.86, 409.81))
        self.assertEqual((r["aBroken"], r["bBroken"], round(r["aGap"], 1), round(r["bGap"], 1)), (True, False, -1.2, 1.0))
        # 環球晶（上櫃）：月高 1260、五日均價 1139、今收 826 → ×0.65：A 819 差 0.8%、B 740.4 差 10%
        bars = scenario(1260.0, [1100.0, 1120.0, 1140.0, 1160.0, 1175.0], 826.0)
        r = module.lines_for(bars, "OTC")
        self.assertEqual((r["factor"], r["market"], r["a"], r["b"]), (0.65, "OTC", 819.0, 740.35))
        self.assertEqual((r["aBroken"], r["bBroken"], round(r["aGap"], 1), round(r["bGap"], 1)), (False, False, 0.8, 10.4))

    def test_window_and_edge_cases(self) -> None:
        self.assertIsNone(module.lines_for(make_bars([(10.0, 10.0)] * 23), "TSE"))          # 日K不夠
        # 月高要在最近 20 個交易日內：21 天前的高點不算
        bars = make_bars([(50.0, 48.0)] * 3 + [(100.0, 90.0)] + [(50.0, 48.0)] * 20)       # 高點在往前第 21 根
        self.assertEqual(module.lines_for(bars, "TSE")["monthHigh"], 50.0)
        bars = make_bars([(50.0, 48.0)] * 4 + [(100.0, 90.0)] + [(50.0, 48.0)] * 19)       # 剛好往前第 20 根：算
        self.assertEqual(module.lines_for(bars, "TSE")["monthHigh"], 100.0)
        self.assertEqual(module.factor_of("TPEx"), 0.65)
        self.assertEqual(module.factor_of(None), 0.78)


class PayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        groups = {"被動元件": [("2327", "國巨"), ("2472", "立隆電")], "記憶體": [("2408", "南亞科")], "矽晶圓": [("6488", "環球晶")], "AI": [("3231", "緯創")]}
        self.patches = [patch.object(m, "STOCK_GROUPS", groups) for m in (brew_launch, brew_launch_history)]
        self.patches.append(patch("stock_groups.STOCK_GROUPS", groups))
        for p in self.patches:
            p.start()
        brew_launch_history._group_by_code.clear()
        days = []
        d = date(2026, 9, 21)
        while len(days) < 30:
            if d.weekday() < 5:
                days.append(d.isoformat())
            d += timedelta(days=1)
        self.days = days
        rows = []
        for code, high, close in (("2408", 537.0, 414.0), ("2327", 686.0, 507.0), ("6488", 1260.0, 826.0), ("3231", 200.0, 190.0)):
            for i, day in enumerate(days):
                if i == 12:
                    rows.append((code, day + "T00:00:00", high, high, high - 5, high - 5, 1000))
                else:
                    price = (high - 5) if i < 12 else close
                    rows.append((code, day + "T00:00:00", price, price * 1.01 if i != len(days) - 1 else price, price * 0.99, price, 1000))
        with database.get_connection() as c:
            c.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            c.execute("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('6488', '環球晶', 'OTC', 'x')")
            c.execute("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES ('2408', '南亞科', 'TSE', 'x')")

    def tearDown(self) -> None:
        brew_launch_history._group_by_code.clear()
        for p in self.patches:
            p.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_payload_default_groups_sorted_by_b_gap(self) -> None:
        r = module.payload()
        self.assertEqual((r["status"], r["date"], r["groups"]), ("ok", self.days[-1], ["被動元件", "記憶體", "矽晶圓"]))
        codes = [x["code"] for x in r["rows"]]
        self.assertEqual(sorted(codes), ["2327", "2408", "6488"])                         # 緯創不在預設三族群；立隆電沒日K
        self.assertEqual(codes, sorted(codes, key=lambda c: next(x["bGap"] for x in r["rows"] if x["code"] == c)))   # B 距離由近到遠（已破在前）
        by = {x["code"]: x for x in r["rows"]}
        self.assertEqual((by["6488"]["factor"], by["2408"]["factor"]), (0.65, 0.78))      # 上櫃 0.65、上市 0.78
        self.assertEqual(r["counts"]["stocks"], 3)
        # 指定族群／全部族群／額外代號
        self.assertEqual([x["code"] for x in module.payload(groups=["AI"])["rows"]], ["3231"])
        self.assertEqual(sorted(x["code"] for x in module.payload(groups=["all"])["rows"]), ["2327", "2408", "3231", "6488"])
        self.assertIn("3231", [x["code"] for x in module.payload(codes=["3231"])["rows"]])

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/margin-call", params={"date": "bad"}).status_code, 422)
        body = client.get("/api/hub/margin-call", params={"groups": "記憶體，AI"}).json()
        self.assertEqual((body["status"], sorted(x["code"] for x in body["rows"])), ("ok", ["2408", "3231"]))


if __name__ == "__main__":
    unittest.main()
