"""週轉率排行：今天成交量 ÷ 已發行股數；盤中用 MIS 即時報價，抓不到或檔數太少用最近一個交易日的官方日K。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import database
import fundamentals_daily
import turnover_rank as module

TW = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 19, 10, 30, tzinfo=TW)   # 星期四、盤中


class TurnoverRankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()
        module._cache.update({"at": None, "payload": None})
        with database.get_connection() as connection:
            connection.executemany("INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, ?)",
                                   [("2368", "金像電", "TSE", "x"), ("6182", "合晶", "OTC", "x"), ("3037", "欣興", "TSE", "x"),
                                    ("0050", "元大台灣50", "TSE", "x"), ("9999", "沒股數", "TSE", "x")])
            bars = [("2368", "2026-11-17T00:00:00+00:00", 100, 2000), ("2368", "2026-11-18T00:00:00+00:00", 110, 5000),
                    ("6182", "2026-11-17T00:00:00+00:00", 50, 100), ("6182", "2026-11-18T00:00:00+00:00", 45, 40000)]
            connection.executemany("INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, ?, ?)", bars)
        # 股數（股）：金像電 1 億股、合晶 5 億股、欣興 15 億股
        fundamentals_daily.save_shares("TSE", {"2368": 100_000_000, "3037": 1_500_000_000, "0050": 10_000_000})
        fundamentals_daily.save_shares("OTC", {"6182": 500_000_000})

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def mis(self, quotes: dict[str, tuple[float, float, int]], calls: list | None = None):
        """quotes：{代號: (現價, 昨收, 量 張)}，回 MIS 格式的假抓取。"""
        def fetcher(url: str):
            if calls is not None:
                calls.append(url)
            return {"msgArray": [{"c": code, "o": f"{px:.2f}", "h": f"{px:.2f}", "l": f"{px:.2f}", "z": f"{px:.2f}", "y": f"{prev:.2f}",
                                  "v": str(vol), "d": "20261119", "t": "10:29:55"} for code, (px, prev, vol) in quotes.items()]}
        return fetcher

    def test_live_ranks_by_volume_over_shares(self) -> None:
        live = {"2368": (121.0, 110.0, 8000), "6182": (40.5, 45.0, 60000), "3037": (200.0, 200.0, 30000), "0050": (180.0, 179.0, 900000)}
        with patch.object(module, "MIN_LIVE_QUOTES", 2):
            payload = module.turnover_rank(20, now=NOW, fetcher=self.mis(live))
        self.assertEqual(payload["source"], "mis")
        self.assertEqual(payload["date"], "2026-11-19")
        self.assertEqual(payload["asOf"], "10:29:55")
        self.assertTrue(payload["session"])
        codes = [r["code"] for r in payload["rows"]]
        self.assertEqual(codes, ["6182", "2368", "3037"])          # 12%、8%、2%；ETF 不算、沒股數的不列
        top = payload["rows"][0]
        self.assertEqual(top["turnover"], 12.0)                      # 60,000 張 ×1000 ÷ 5 億股
        self.assertEqual(top["chg"], -10.0)
        self.assertEqual(top["sharesLots"], 500_000)
        self.assertEqual(payload["rows"][1]["turnover"], 8.0)

    def test_top_limits_rows_and_cache_skips_refetch(self) -> None:
        calls: list = []
        live = {"2368": (121.0, 110.0, 8000), "6182": (40.5, 45.0, 60000), "3037": (200.0, 200.0, 30000)}
        with patch.object(module, "MIN_LIVE_QUOTES", 2):
            first = module.turnover_rank(2, now=NOW, fetcher=self.mis(live, calls))
            n = len(calls)
            second = module.turnover_rank(1, now=NOW, fetcher=self.mis(live, calls))
        self.assertEqual([r["code"] for r in first["rows"]], ["6182", "2368"])
        self.assertEqual([r["code"] for r in second["rows"]], ["6182"])
        self.assertEqual(len(calls), n)                              # 3 分鐘內不再打證交所
        self.assertEqual(second["refreshSeconds"], module.LIVE_TTL_SECONDS)

    def test_falls_back_to_latest_daily_bars_when_mis_has_too_few(self) -> None:
        with patch.object(module, "_now", return_value=NOW):
            payload = module.turnover_rank(20, now=NOW, fetcher=self.mis({"2368": (121.0, 110.0, 8000)}))
        self.assertEqual(payload["source"], "bars")
        self.assertEqual(payload["date"], "2026-11-18")
        self.assertIsNone(payload["asOf"])
        self.assertEqual([r["code"] for r in payload["rows"]], ["6182", "2368"])   # 8%、5%
        self.assertEqual(payload["rows"][0]["chg"], -10.0)

    def test_mis_error_also_falls_back(self) -> None:
        def broken(url: str):
            raise OSError("blocked")
        with patch.object(module, "_now", return_value=NOW), patch("grail_radar.time.sleep"):
            payload = module.turnover_rank(20, now=NOW, fetcher=broken)
        self.assertEqual(payload["source"], "bars")
        self.assertEqual(len(payload["rows"]), 2)

    def test_no_shares_yet(self) -> None:
        with database.get_connection() as connection:
            connection.execute("DELETE FROM stock_shares")
        payload = module.turnover_rank(20, now=NOW, fetcher=self.mis({}))
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["rows"], [])


if __name__ == "__main__":
    unittest.main()
