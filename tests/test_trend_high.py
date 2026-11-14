"""熱門趨勢概念股：240 日最高收盤、240 日漲幅（還原）、隔天盤中用的 hi239／b239、範圍過濾、端點。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

import heilong_picker as picker
import persistent_app
import trend_high


def weekdays(count: int) -> list[str]:
    out, d = [], date(2025, 1, 1)
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_panel(dates: list[str], closes: dict[str, list]) -> picker.Panel:
    panel = picker.Panel("test", dates)
    for code, cs in closes.items():
        s = picker.Series(code, len(dates))
        s.name = f"股{code}"
        s.group = "測試族"
        for i, c in enumerate(cs):
            if c is None:
                continue
            s.o[i] = s.h[i] = s.l[i] = s.c[i] = c
            s.chg[i] = 0.0
            s.score[i] = 12
            s.hilen[i] = 5
            s.val5[i] = 3.5
        panel.series[code] = s
    panel.build_weeks()
    return panel


class TrendHighTest(unittest.TestCase):
    def setUp(self):
        self.dates = weekdays(260)

    def test_high_is_max_close_and_gain_vs_240_days_ago(self):
        # 第 0～19 天 10 元、之後一路漲到 25；中間有一天收 30（240 天內最高收盤）
        cs = [10.0] * 20 + [10.0 + i * 0.0625 for i in range(240)]
        cs[100] = 30.0
        panel = make_panel(self.dates, {"1802": cs})
        out = trend_high.compute(panel, near_pct=100.0)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["date"], self.dates[-1])
        row = out["rows"][0]
        self.assertEqual(row["hi240"], 30.0)                                  # 240 天內最高「收盤」
        base = cs[-241]
        self.assertAlmostEqual(row["g240"], round((cs[-1] / base - 1) * 100, 2))   # 跟 240 個交易日前那天的收盤比
        self.assertEqual(row["hi239"], round(max(cs[-239:]), 2))
        self.assertEqual(row["b239"], round(cs[-240], 4))                     # 下一個交易日的基準價
        self.assertEqual(row["close"], round(cs[-1], 2))

    def test_new_high_today(self):
        cs = [10.0] * 19 + [10.0 + i * 0.1 for i in range(241)]
        panel = make_panel(self.dates, {"3443": cs})
        row = trend_high.compute(panel)["rows"][0]
        self.assertEqual(row["hi240"], row["close"])

    def test_far_from_high_filtered_and_missing_today_skipped(self):
        far = [100.0] * 200 + [80.0] * 60            # 離高點 20%：不回
        near = [100.0] * 200 + [95.0] * 60           # 5%：回
        halted = [50.0] * 259 + [None]               # 最新一天沒成交：不回
        panel = make_panel(self.dates, {"1111": far, "2222": near, "3333": halted})
        codes = [r["code"] for r in trend_high.compute(panel)["rows"]]
        self.assertEqual(codes, ["2222"])

    def test_short_history_has_no_gain(self):
        dates = weekdays(100)
        panel = make_panel(dates, {"6999": [10.0 + i * 0.01 for i in range(100)]})
        row = trend_high.compute(panel)["rows"][0]
        self.assertIsNone(row["g240"])
        self.assertIsNone(row["b239"])
        self.assertEqual(row["hi240"], row["close"])

    def test_sorted_by_gain(self):
        a = [10.0] * 20 + [10.0 + i * 0.05 for i in range(240)]
        b = [10.0] * 20 + [10.0 + i * 0.2 for i in range(240)]
        panel = make_panel(self.dates, {"1000": a, "2000": b})
        self.assertEqual([r["code"] for r in trend_high.compute(panel)["rows"]], ["2000", "1000"])

    def test_dividend_adjusted(self):
        # 一直 100 元，第 200 天除息 5 元（因子 0.95）之後 95 元：還原後 240 天都是 95 → 漲幅 0%、今天就是 240 日最高
        cs = [100.0] * 200 + [95.0] * 60
        panel = make_panel(self.dates, {"2881": cs})
        raw = trend_high.compute(panel)["rows"][0]
        self.assertEqual(raw["hi240"], 100.0)
        adj = trend_high.compute(panel, dividends={"2881": [(self.dates[200], 0.95)]})["rows"][0]
        self.assertEqual(adj["hi240"], 95.0)
        self.assertEqual(adj["g240"], 0.0)
        self.assertEqual(adj["close"], 95.0)
        self.assertEqual(adj["b239"], 95.0)

    def test_dividend_tomorrow_only_for_next_day_values(self):
        # 明天除息（因子 0.9）：今天的值不動，隔天盤中用的 hi239／b239 先乘；更後面的除息不算
        cs = [10.0 + i * 0.01 for i in range(260)]
        panel = make_panel(self.dates, {"1101": cs})
        tomorrow = trend_high._next_weekday(self.dates[-1])
        out = trend_high.compute(panel, dividends={"1101": [(tomorrow, 0.9), ("2099-01-01", 0.5)]})["rows"][0]
        self.assertEqual(out["hi240"], round(cs[-1], 2))
        self.assertEqual(out["hi239"], round(cs[-1] * 0.9, 2))
        self.assertEqual(out["b239"], round(cs[-240] * 0.9, 4))

    def test_endpoint(self):
        panel = make_panel(self.dates, {"1802": [10.0] * 20 + [10.0 + i * 0.1 for i in range(240)]})
        trend_high._cache.update({"key": None, "payload": None})
        with patch.object(trend_high.heilong_picker, "load_panel", return_value=panel), patch.object(trend_high, "load_dividends", return_value={}):
            res = TestClient(persistent_app.app).get("/api/hub/trend-high")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["rows"][0]["code"], "1802")
        trend_high._cache.update({"key": None, "payload": None})


if __name__ == "__main__":
    unittest.main()
