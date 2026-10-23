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


class StabilizeTests(unittest.TestCase):
    @staticmethod
    def series(closes: list[float], vols: list[float], lows: list[float] | None = None) -> list[tuple[str, float, float, float, float]]:
        lows = lows or [c * 0.99 for c in closes]
        return [(f"2026-09-{i + 1:02d}", c * 1.01, low, c, v) for i, (c, v, low) in enumerate(zip(closes, vols, lows))]

    def test_burst_then_smaller_drop_without_new_low(self) -> None:
        closes = [100.0] * 24 + [96.0, 91.0, 90.0]                         # 暴跌 −4%、−5.2% 之後今天只跌 −1.1%
        vols = [1000.0] * 24 + [1000.0, 3200.0, 1500.0]                    # 前一天爆量（3.2 倍），今天量縮
        lows = [99.0] * 24 + [95.0, 88.0, 89.5]                            # 今天最低 89.5 比爆量日最低 88 高
        r = module.stabilize(self.series(closes, vols, lows))
        self.assertEqual((r["burst"], r["burstRatio"], r["burstDate"]), (True, 3.2, "2026-09-26"))
        self.assertEqual((r["chg"], r["chgPrev"], r["shrinking"], r["noNewLow"]), (-1.1, -5.21, True, True))

    def test_not_stabilizing_cases(self) -> None:
        closes = [100.0] * 24 + [96.0, 91.0, 85.0]                         # 今天跌更多（−6.6%）→ 跌幅沒縮小
        vols = [1000.0] * 24 + [1000.0, 3200.0, 1500.0]
        r = module.stabilize(self.series(closes, vols))
        self.assertFalse(r["shrinking"])
        r = module.stabilize(self.series([100.0] * 24 + [96.0, 91.0, 90.0], [1000.0] * 24 + [1000.0, 1500.0, 1200.0]))   # 量最大只 1.5 倍 → 不算爆量
        self.assertEqual((r["burst"], r["burstRatio"]), (False, 1.5))
        r = module.stabilize(self.series([100.0] * 24 + [96.0, 91.0, 92.0], [1000.0] * 24 + [1000.0, 3200.0, 1500.0], [99.0] * 24 + [95.0, 88.0, 87.0]))
        self.assertFalse(r["noNewLow"])                                    # 今天最低 87 又破爆量日最低 88
        self.assertIsNone(module.stabilize(self.series([100.0] * 20, [1000.0] * 20)))   # 日K不夠


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
        self.assertEqual(sorted(by["2408"]["stab"]), ["burst", "burstDate", "burstRatio", "chg", "chgPrev", "noNewLow", "shrinking"])   # 名單每檔帶止跌特徵（給「近似落底」）
        self.assertEqual(r["counts"]["stocks"], 3)
        # 老師最後一頁的總結：A 線已破的名單、B 線 5% 內（還沒破）的名單（照 B 距離排）
        self.assertEqual([x["code"] for x in r["summary"]["aBroken"]], ["2327", "2408"])
        self.assertEqual([x["code"] for x in r["summary"]["bWithin"]], ["6488"])
        self.assertEqual(r["rule"]["summaryNearPct"], 5.0)
        # 指定族群／全部族群／額外代號
        self.assertEqual([x["code"] for x in module.payload(groups=["AI"])["rows"]], ["3231"])
        self.assertEqual(sorted(x["code"] for x in module.payload(groups=["all"])["rows"]), ["2327", "2408", "3231", "6488"])
        self.assertIn("3231", [x["code"] for x in module.payload(codes=["3231"])["rows"]])

    def test_margin_balance_attached(self) -> None:
        import margin_balance

        row = lambda **kw: {"code": "2408", "buy": 0, "sell": 0, "repay": 0, "prev": 0, "limit": 774656, "short": 0, **kw}  # noqa: E731
        high_date = next(x for x in module.payload()["rows"] if x["code"] == "2408")["highDate"]
        margin_balance.save_margin(high_date, "TSE", [row(balance=1000, prev=990)], "twse")             # 月高那天
        margin_balance.save_margin(self.days[-1], "TSE", [row(balance=800, prev=900)], "twse")
        by = {x["code"]: x for x in module.payload()["rows"]}
        m = by["2408"]["margin"]
        self.assertEqual((m["balance"], m["chg1"], m["sinceHighPct"], m["util"]), (800, -100, -20.0, 0.1))
        self.assertIsNone(by["2327"]["margin"])                                                          # 那檔還沒有融資資料：不擋名單
        self.assertEqual((by["2408"]["aNew"], by["2408"]["bNew"]), (False, False))                      # 昨收就在線下，不算今天剛破
        self.assertEqual(module.payload()["summary"]["aNew"], [])

    def test_new_break_flags(self) -> None:
        bars = scenario(537.0, [520.0, 524.0, 526.0, 527.0, 530.0], 414.0)
        bars = bars[:-2] + [(bars[-2][0], 440.0, 440.0), (bars[-1][0], 430.0, 414.0)]                  # 昨收 440（A 418.86 之上）、今收 414：剛跌破 A
        r = module.lines_for(bars, "TSE")
        self.assertEqual((r["aBroken"], r["aNew"], r["bNew"]), (True, True, False))                     # B 409.81：昨今都在線上

    def test_single_stock_line(self) -> None:
        r = module.single("2408")
        self.assertEqual((r["status"], r["code"], r["date"], r["line"]["factor"], r["line"]["a"]), ("ok", "2408", self.days[-1], 0.78, round(max(b for b in (537.0 * 1.0, 532.0 * 1.01)) * 0.78, 2)))
        self.assertEqual(module.single("6488")["line"]["factor"], 0.65)                    # 上櫃 0.65
        self.assertEqual(module.single("9999")["status"], "missing")                       # 沒日K
        self.assertEqual(module.single("")["status"], "empty")
        client = TestClient(persistent_app.app)
        body = client.get("/api/hub/margin-line", params={"code": "2408"}).json()
        self.assertEqual((body["status"], body["line"]["code"] if "code" in body["line"] else body["code"]), ("ok", "2408"))
        self.assertEqual(client.get("/api/hub/margin-line", params={"code": "bad code!"}).status_code, 422)
        self.assertEqual(client.get("/api/hub/margin-line", params={"code": "2408", "date": "x"}).status_code, 422)

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        self.assertEqual(client.get("/api/hub/margin-call", params={"date": "bad"}).status_code, 422)
        body = client.get("/api/hub/margin-call", params={"groups": "記憶體，AI"}).json()
        self.assertEqual((body["status"], sorted(x["code"] for x in body["rows"])), ("ok", ["2408", "3231"]))


if __name__ == "__main__":
    unittest.main()
