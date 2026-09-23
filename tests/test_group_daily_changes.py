"""族群最近幾個交易日的平均漲跌幅（盤中打 333 的 188／199 名單用）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import group_daily_changes as module
import persistent_app


def _rows():
    # 兩個族群各兩檔；交易日 09/22（昨天）、09/19（前天）、09/18；1101 的 09/22 缺（日K沒跟上）
    rows = []
    closes = {
        "2330": {"2026-09-18": 100.0, "2026-09-19": 110.0, "2026-09-22": 99.0},   # 前天 +10%、昨天 -10%
        "2317": {"2026-09-18": 50.0, "2026-09-19": 50.0, "2026-09-22": 47.5},     # 前天 0%、昨天 -5%
        "1101": {"2026-09-18": 30.0, "2026-09-19": 36.0},                          # 前天 +20%、昨天缺
        "1102": {"2026-09-18": 20.0, "2026-09-19": 20.0, "2026-09-22": 21.0},     # 前天 0%、昨天 +5%
        "2454": {"2026-09-19": 1000.0, "2026-09-22": 1010.0},                     # 只在股期標的清單裡
    }
    for code, by_day in closes.items():
        for day, close in by_day.items():
            rows.append((code, day, close))
    return rows


FAKE_GROUPS = {"半導體": [("2330", "台積電"), ("2317", "鴻海")], "水泥": [("1101", "台泥"), ("1102", "亞泥")], "股期標的": [("2330", "台積電"), ("2454", "聯發科")]}


class GroupDailyChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        module._cache.update({"key": None, "at": 0.0, "value": None})

    def test_averages_ranks_and_market_dates(self) -> None:
        with patch.object(module, "STOCK_GROUPS", FAKE_GROUPS), patch.object(module, "_load_rows", lambda codes, today: _rows()):
            result = module.compute_group_daily_changes(3, today="2026-09-23")
        self.assertEqual(result["dates"], ["2026-09-22", "2026-09-19"])  # 09/18 沒有前一天可比，不算一天
        self.assertNotIn("股期標的", result["groups"])
        semi = result["groups"]["半導體"]
        cement = result["groups"]["水泥"]
        self.assertEqual(semi["pct"], [-7.5, 5.0])       # 昨天 (-10 + -5)/2、前天 (+10 + 0)/2
        self.assertEqual(semi["members"], [2, 2])
        self.assertEqual(cement["pct"], [5.0, 10.0])     # 昨天只有 1102（1101 的日K沒跟上）、前天 (20 + 0)/2
        self.assertEqual(cement["members"], [1, 2])
        self.assertEqual(cement["rank"], [1, 1])
        self.assertEqual(semi["rank"], [2, 2])
        self.assertEqual(result["rankedCount"], [2, 2])
        # 個股自己的昨天／前天漲跌幅（馬火多用）
        self.assertEqual(result["stocks"]["2330"]["pct"], [-10.0, 10.0])
        self.assertEqual(result["stocks"]["1101"]["pct"], [None, 20.0])
        self.assertEqual(result["stocks"]["1102"]["pct"], [5.0, 0.0])
        # 那天的收盤價與漲跌金額（三個大戶力分頁看昨天／前天用）
        self.assertEqual(result["stocks"]["2330"]["close"], [99.0, 110.0])
        self.assertEqual(result["stocks"]["2330"]["change"], [-11.0, 10.0])
        self.assertEqual(result["stocks"]["1101"]["close"], [None, 36.0])
        self.assertEqual(result["stocks"]["1101"]["change"], [None, 6.0])
        # 只在股期標的清單裡的股票也要有收盤價（盤中大戶力平面排行會列它），但不算進任何族群平均
        self.assertEqual(result["stocks"]["2454"]["close"], [1010.0, 1000.0])
        self.assertEqual(result["stocks"]["2454"]["pct"], [1.0, None])
        self.assertEqual(result["stocks"]["2454"]["change"], [10.0, None])
        self.assertNotIn("2454", str(result["groups"]))

    def test_endpoint_and_cache(self) -> None:
        calls = []

        def fake_compute(days, today=None):
            calls.append(days)
            return {"status": "ok", "today": "2026-09-23", "dates": ["2026-09-22"], "groupCount": 1, "rankedCount": [1],
                    "groups": {"半導體": {"pct": [-7.5], "rank": [1], "members": [2]}}, "generatedAt": "x"}

        with patch.object(module, "compute_group_daily_changes", fake_compute):
            client = TestClient(persistent_app.app)
            first = client.get("/api/hub/group-daily-changes?days=3").json()
            second = client.get("/api/hub/group-daily-changes?days=3").json()
        self.assertEqual(first["groups"]["半導體"]["pct"], [-7.5])
        self.assertEqual(second, first)
        self.assertEqual(calls, [3])  # 半小時內第二次直接用快取


if __name__ == "__main__":
    unittest.main()
