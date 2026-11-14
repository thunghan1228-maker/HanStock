"""日動能／週動能／法人動能：動能公式、強弱名單照成交金額排、上榜天數、週K、盤中重算要用的狀態欄位、法人四張名單、端點。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

import heilong_picker as picker
import momentum_pulse as mp
import persistent_app


def weekdays(start: str, end: str) -> list[str]:
    out, d = [], date.fromisoformat(start)
    while d.isoformat() <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_panel(dates: list[str], data: dict[str, tuple[list, int]], groups: dict[str, str] | None = None) -> picker.Panel:
    """data＝{代號: (收盤序列, 每天成交量張)}。"""
    panel = picker.Panel("test", dates)
    for code, (cs, vol) in data.items():
        s = picker.Series(code, len(dates))
        s.name = f"股{code}"
        s.group = (groups or {}).get(code, "")
        for i, c in enumerate(cs):
            if c is None:
                continue
            s.o[i] = s.h[i] = s.l[i] = s.c[i] = c
            s.v[i] = vol
            s.chg[i] = 0.0
            s.score[i] = 12
            s.val5[i] = c * vol * 1000 / 1e8
        panel.series[code] = s
    panel.build_weeks()
    return panel


def ema_mom(closes: list[float]) -> float:
    a = 2 / 9
    e = closes[0]
    for c in closes[1:]:
        e = a * c + (1 - a) * e
    return 1.4 * (closes[-1] / e - 1) * 100


class FormulaTest(unittest.TestCase):
    def test_mom_series_matches_ema8(self):
        cs = [10, 10.5, 10.2, 11, 11.5, 12, 11.8, 12.6, 13.4, 13.0]
        self.assertAlmostEqual(mp.mom_series(cs)[-1], ema_mom(cs), places=9)
        self.assertEqual(mp.mom_series([10, 10, 10])[-1], 0.0)

    def test_streak(self):
        self.assertEqual(mp._streak_on([1, 7, 8, 9], lambda v: v >= 6), 3)
        self.assertEqual(mp._streak_on([7, 2], lambda v: v >= 6), 0)


class PulseTest(unittest.TestCase):
    def setUp(self):
        self.dates = weekdays("2026-01-05", "2026-11-13")   # 最後一天週五

    def flat_then(self, tail: list[float], base: float = 100.0) -> list[float]:
        return [base] * (len(self.dates) - len(tail)) + tail

    def test_day_lists_sorted_by_turnover(self):
        up = self.flat_then([104, 108, 112, 118])          # 連漲：動能大
        down = self.flat_then([96, 92, 88, 83])
        data = {
            "1111": (up, 1000),          # 成交金額小
            "2222": ([x * 2 for x in up], 5000),   # 成交金額大
            "3333": (down, 3000),
            "4444": (self.flat_then([100.2]), 9000),   # 平平：不上榜
        }
        out = mp.compute(make_panel(self.dates, data))
        self.assertEqual([r["code"] for r in out["day"]["strong"]], ["2222", "1111"])
        self.assertEqual([r["code"] for r in out["day"]["weak"]], ["3333"])
        r = out["day"]["strong"][0]
        self.assertAlmostEqual(r["mom"], round(ema_mom(data["2222"][0][-120:]), 1), places=1)
        self.assertGreaterEqual(r["days"], 1)
        self.assertIsNotNone(r["momChg"])

    def test_group_mom_and_rows_state(self):
        up = self.flat_then([104, 108, 112, 118])
        data = {"1111": (up, 1000), "2222": (self.flat_then([100.0]), 1000)}
        out = mp.compute(make_panel(self.dates, data, groups={"1111": "測試族", "2222": "測試族"}))
        self.assertAlmostEqual(out["day"]["strong"][0]["groupMom"], round((ema_mom(up[-120:]) + 0.0) / 2, 1), places=1)
        cols = out["cols"]
        row = dict(zip(cols, next(r for r in out["rows"] if r[0] == "1111")))
        # 盤中用的狀態：拿最新收盤當「即時價」重算，等於最新一天的動能
        a = 2 / 9
        self.assertAlmostEqual(1.4 * (row["close"] / row["emaD"] - 1) * 100, row["dm"], places=1)
        prev_ema = (row["emaD"] - a * row["close"]) / (1 - a)
        self.assertGreater(prev_ema, 0)
        self.assertEqual(out["week"], "2026-W46")

    def test_weekly_uses_week_closes(self):
        # 最後兩週大漲：週動能強勢；週成交金額＝這週 5 天加總
        cs = self.flat_then([100] * 5 + [110, 112, 115, 118, 120])
        out = mp.compute(make_panel(self.dates, {"5555": (cs, 1000)}))
        self.assertEqual([r["code"] for r in out["weekly"]["strong"]], ["5555"])
        w = out["weekly"]["strong"][0]
        self.assertAlmostEqual(w["val"], round(sum(c * 1000 * 1000 / 1e8 for c in cs[-5:]), 1), places=1)
        self.assertGreaterEqual(w["weeks"], 1)

    def test_inst_lists(self):
        n = len(self.dates)
        hi = self.flat_then([101, 102, 103, 105])                  # 創 240 日高
        data = {"6666": (hi, 1000), "7777": (self.flat_then([99.0, 99.5]), 500)}
        panel = make_panel(self.dates, data)
        panel.series["6666"].v[n - 1] = 5000                       # 今天量最大
        inst = ("2026-11-13", {
            "6666": {"foreign": [3_000_000] + [1_000_000] * 3 + [0] * 8, "trust": [500_000] * 4 + [0] * 8},
            # 7777：投信今天才開始買、之前 5 天沒買；收盤 99.5 在 60 日高 100 的 95%～100%
            "7777": {"foreign": [0] * 12, "trust": [200_000] + [0] * 11},
        })
        out = mp.compute(panel, inst=inst)
        lists = out["inst"]["lists"]
        self.assertEqual([r["code"] for r in lists["priceVolHigh"]], ["6666"])
        self.assertEqual([r["code"] for r in lists["trustFresh"]], ["7777"])
        self.assertEqual([r["code"] for r in lists["trustStreak"]], ["6666"])
        self.assertEqual([r["code"] for r in lists["foreignStreak"]], ["6666"])
        self.assertEqual(lists["priceVolHigh"][0]["f"], 3000)
        self.assertEqual(lists["priceVolHigh"][0]["ts"], 4)

    def test_endpoint(self):
        panel = make_panel(self.dates, {"1111": (self.flat_then([104, 108, 112, 118]), 1000)})
        mp._cache.update({"key": None, "payload": None})
        with patch.object(mp.heilong_picker, "load_panel", return_value=panel), patch.object(mp, "load_dividends", return_value={}), \
                patch.object(mp, "_inst_window", return_value=(None, {})):
            res = TestClient(persistent_app.app).get("/api/hub/momentum-pulse")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["day"]["strong"][0]["code"], "1111")
        mp._cache.update({"key": None, "payload": None})


if __name__ == "__main__":
    unittest.main()
