"""動能排行：本週／本月／本季／今年以來漲幅（日曆期間、還原收盤）、前 N% 門檻、創高、差一個、端點。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

import heilong_picker as picker
import momentum_rank
import persistent_app


def weekdays(start: str, end: str) -> list[str]:
    out, d = [], date.fromisoformat(start)
    while d.isoformat() <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def make_panel(dates: list[str], closes: dict[str, list]) -> picker.Panel:
    panel = picker.Panel("test", dates)
    for code, cs in closes.items():
        s = picker.Series(code, len(dates))
        s.name = f"股{code}"
        for i, c in enumerate(cs):
            if c is None:
                continue
            s.o[i] = s.h[i] = s.l[i] = s.c[i] = c
            s.chg[i] = 0.0
            s.score[i] = 12
        panel.series[code] = s
    panel.build_weeks()
    return panel


class PeriodTest(unittest.TestCase):
    def test_period_starts(self):
        self.assertEqual(momentum_rank.period_starts("2026-11-13"),
                         {"wtd": "2026-11-09", "mtd": "2026-11-01", "qtd": "2026-10-01", "ytd": "2026-01-01"})
        self.assertEqual(momentum_rank.period_starts("2026-03-02")["qtd"], "2026-01-01")


class MomentumTest(unittest.TestCase):
    def setUp(self):
        self.dates = weekdays("2025-10-01", "2026-11-13")
        self.idx = {d: i for i, d in enumerate(self.dates)}

    def series(self, base: float, steps: dict[str, float]) -> list[float]:
        """從 base 開始，到了 steps 裡的日子（含）以後收盤換成那個價。"""
        out, cur = [], base
        for d in self.dates:
            if d in steps:
                cur = steps[d]
            out.append(cur)
        return out

    def test_calendar_gains_like_app(self):
        # 台玻那種：上週五 57.8、10/30 62.3、9/30 59.9、去年底 34.55，最後一天 85.1（240 日最高）
        cs = self.series(30.0, {"2025-12-31": 34.55, "2026-09-30": 59.9, "2026-10-30": 62.3, "2026-11-06": 57.8, "2026-11-13": 85.1})
        panel = make_panel(self.dates, {"1802": cs})
        out = momentum_rank.compute(panel, top=100)
        row = out["rows"][0]
        self.assertEqual(row["wtd"], 47.23)
        self.assertEqual(row["mtd"], 36.6)
        self.assertEqual(row["qtd"], round((85.1 / 59.9 - 1) * 100, 2))
        self.assertEqual(row["ytd"], round((85.1 / 34.55 - 1) * 100, 2))

    def test_top_pct_and_new_high(self):
        closes = {}
        # 20 檔：0～18 號小漲，19 號（9999）四個期間都漲最多、收盤創高
        for k in range(19):
            closes[f"{1000 + k}"] = self.series(10.0, {"2025-12-31": 10.0, "2026-11-13": 10.0 + k * 0.01})
        closes["9999"] = self.series(10.0, {"2026-11-13": 20.0})
        # 8888：漲幅也大但沒創高（之前收過 30）
        closes["8888"] = self.series(10.0, {"2026-06-01": 30.0, "2026-06-02": 10.0, "2026-11-13": 19.0})
        panel = make_panel(self.dates, closes)
        out = momentum_rank.compute(panel, top=5)        # 21 檔 → 前 5% 取 2 檔
        self.assertEqual([r["code"] for r in out["rows"]], ["9999"])
        self.assertEqual(out["rows"][0]["wtdRank"], 1)
        self.assertEqual(out["universe"], 21)

    def test_almost_one_missing(self):
        # 其他 18 檔這週才從 10 漲到 10.5 左右（週、月、季、年都 +5%）
        closes = {f"{1000 + k}": self.series(10.0, {"2026-11-09": 10.5 + k * 0.001}) for k in range(18)}
        closes["9999"] = self.series(10.0, {"2026-11-13": 20.0})
        # 7777：上週五就漲到 18.3、今天 18.5 創高 → 本週只 +1.1%（輸給其他檔），月／季／年都 +85%
        closes["7777"] = self.series(10.0, {"2026-11-06": 18.3, "2026-11-13": 18.5})
        panel = make_panel(self.dates, closes)
        out = momentum_rank.compute(panel, top=10)       # 20 檔 → 前 10% 取 2 檔
        self.assertEqual([r["code"] for r in out["rows"]], ["9999"])
        almost = {r["code"]: r["miss"] for r in out["almost"]}
        self.assertEqual(almost.get("7777"), ["週"])

    def test_dividend_adjusted_base_and_new_high(self):
        # 去年底 50、今年 7/1 除息（因子 0.9）後 45、今天 45：沒還原是 −10%、而且沒創高；還原後年漲幅 0%、今天平高點算創高
        cs = self.series(50.0, {"2026-07-01": 45.0})
        panel = make_panel(self.dates, {"2882": cs})
        raw = momentum_rank.compute(panel, top=100)
        self.assertEqual(raw["rows"], [])
        adj = momentum_rank.compute(panel, top=100, dividends={"2882": [("2026-07-01", 0.9)]})
        self.assertEqual(adj["rows"][0]["ytd"], 0.0)

    def test_skip_not_traded_today(self):
        cs = self.series(10.0, {"2026-11-12": 20.0})
        cs[-1] = None
        panel = make_panel(self.dates, {"1111": cs})
        self.assertEqual(momentum_rank.compute(panel)["universe"], 0)

    def test_endpoint_and_top_validation(self):
        panel = make_panel(self.dates, {"9999": self.series(10.0, {"2026-11-13": 20.0})})
        momentum_rank._cache.clear()
        client = TestClient(persistent_app.app)
        with patch.object(momentum_rank.heilong_picker, "load_panel", return_value=panel), patch.object(momentum_rank, "load_dividends", return_value={}):
            ok = client.get("/api/hub/momentum-rank")
            bad = client.get("/api/hub/momentum-rank?top=7")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["rows"][0]["code"], "9999")
        self.assertEqual(bad.status_code, 422)
        momentum_rank._cache.clear()


if __name__ == "__main__":
    unittest.main()
