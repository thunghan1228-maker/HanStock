"""資金保護傘：FinMind 融資／指數解析、增量抓取、崩盤偵測與崩盤前融資高點、月線季線位置與明天要守的價、背離、保護傘點數、端點。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import fund_umbrella as module
import persistent_app

TW = module.TW_TZ


def days(n: int, start: str = "2025-01-01") -> list[str]:
    out, d = [], date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class FundUmbrellaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        # 200 天：先漲到 120（第 100 天）、崩到 96（-20%）、再回到 118；融資在高點附近 2,000 億，崩完剩 1,500 億，最後 5 天指數跌、融資增
        self.d = days(200)
        closes = [100 + 0.2 * i for i in range(101)] + [120 - 2.4 * i for i in range(1, 11)] + [96 + 0.3 * i for i in range(1, 75)]
        closes += [closes[-1] - 1.5 * i for i in range(1, 200 - len(closes) + 1)]
        self.closes = closes[:200]
        margin = [1800 + 2 * i for i in range(101)] + [2000 - 50 * i for i in range(1, 11)] + [1500 + 3 * i for i in range(1, 75)]
        margin += [margin[-1] + 10 * i for i in range(1, 200 - len(margin) + 1)]
        self.margin = margin[:200]

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def fetcher(self, calls: list):
        def call(params):
            calls.append(params)
            if params["dataset"] == "TaiwanStockTotalMarginPurchaseShortSale":
                rows = []
                for d, m in zip(self.d, self.margin):
                    rows += [{"date": d, "name": "MarginPurchaseMoney", "TodayBalance": m * 1e8},
                             {"date": d, "name": "MarginPurchase", "TodayBalance": 8_000_000}, {"date": d, "name": "ShortSale", "TodayBalance": 200_000}]
                return {"data": rows}
            scale = 1 if params["data_id"] == "TAIEX" else 0.01
            return {"data": [{"date": d, "close": c * scale} for d, c in zip(self.d, self.closes)]}
        return call

    def test_parse(self) -> None:
        got = module.parse_margin({"data": [{"date": "2026-10-08", "name": "MarginPurchaseMoney", "TodayBalance": 646405767000},
                                            {"date": "2026-10-08", "name": "ShortSale", "TodayBalance": 211949}, {"name": "X"}]})
        self.assertEqual((round(got["margin"]["2026-10-08"], 2), got["short_units"]["2026-10-08"]), (6464.06, 211949.0))
        self.assertEqual(module.parse_index({"data": [{"date": "2026-10-08", "close": 49313.44}, {"date": "x"}]}), {"2026-10-08": 49313.44})

    def test_crash_and_state(self) -> None:
        margin = dict(zip(self.d, self.margin))
        crash = module.crashes(self.d, self.closes, margin)
        self.assertEqual([(c["recovered"]) for c in crash], [True, False])      # 最後 15 天又跌了 19%，還沒回來
        c = crash[0]
        self.assertEqual((c["peakDate"], c["troughDate"], c["drop"], c["days"], c["marginPeakDate"], c["marginPeak"], c["marginTrough"]),
                         (self.d[100], self.d[110], -20.0, 10, self.d[100], 2000.0, 1500.0))
        st = module.index_state(self.d[:180], self.closes[:180])
        self.assertEqual((st["state"], st["above20"]), ("多頭", True))
        self.assertAlmostEqual(st["hold20"], round(sum(self.closes[161:180]) / 19, 2))
        self.assertEqual(st["deduct20"], round(self.closes[160], 2))
        down = module.index_state(self.d, self.closes)
        self.assertFalse(down["above20"])
        self.assertEqual(down["turnDate"], self.d[200 - down["streak"]])

    def test_fetch_and_payload(self) -> None:
        calls: list = []
        result = module.fetch(fetcher=self.fetcher(calls), now=datetime(2026, 10, 10, 22, 0, tzinfo=TW))
        self.assertEqual((result["from"], result["latest"]), ("2020-01-01", self.d[-1]))
        module.fetch(fetcher=self.fetcher(calls), now=datetime(2026, 10, 11, 22, 0, tzinfo=TW))   # 第二次只補最近 15 天
        self.assertEqual(calls[-1]["start_date"], (date.fromisoformat(self.d[-1]) - timedelta(days=15)).isoformat())
        p = module.payload()
        self.assertEqual((p["status"], p["asOf"], p["margin"]["balance"], p["margin"]["d1"]), ("ok", self.d[-1], self.margin[-1], 10.0))
        self.assertEqual(p["margin"]["shortRatio"], 2.5)
        self.assertEqual(p["crashes"][0]["gap"], round(self.margin[-1] - 2000, 1))
        self.assertEqual(p["diverge"]["tone"], "bear")                     # 指數跌、融資增
        texts = [r["text"] for r in p["umbrella"]["reasons"]]
        self.assertTrue(any("加權跌破月線" in t for t in texts))
        self.assertTrue(any("接刀" in t for t in texts))
        self.assertEqual(p["umbrella"]["score"], len(texts))
        self.assertEqual(len(p["series"]), 200)

    def test_missing_and_due(self) -> None:
        self.assertEqual(module.payload()["status"], "missing")
        at = lambda s: datetime.fromisoformat(s + "+08:00")  # noqa: E731
        self.assertTrue(module.due(at("2026-10-10T10:00:00"), None))
        self.assertFalse(module.due(at("2026-10-10T21:00:00"), "2026-10-10T10:00:00+08:00"))
        self.assertTrue(module.due(at("2026-10-10T21:45:00"), "2026-10-10T10:00:00+08:00"))
        self.assertFalse(module.due(at("2026-10-10T22:30:00"), "2026-10-10T21:45:00+08:00"))
        self.assertTrue(module.due(at("2026-10-11T21:45:00"), "2026-10-10T21:45:00+08:00"))

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.object(module, "_default_fetcher", self.fetcher([])):
            r = client.get("/api/hub/fund-umbrella")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "ok"))


if __name__ == "__main__":
    unittest.main()
