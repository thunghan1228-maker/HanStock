import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import database
import otc_gap_backfill as module
from otc_gap_backfill import (
    _row_to_otc_bar,
    backfill_otc_gap,
    backfill_state,
    fetch_finmind_price_day,
)


FINMIND_ROW_OTC = {
    "date": "2026-07-21", "stock_id": "6488", "Trading_Volume": 1234000,
    "open": 490.0, "max": 510.0, "min": 485.0, "close": 500.0,
}
FINMIND_ROW_TSE = {
    "date": "2026-07-21", "stock_id": "2330", "Trading_Volume": 5000000,
    "open": 900.0, "max": 910.0, "min": 895.0, "close": 905.0,
}


class OtcGapBackfillDbTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        self.token_patch = patch.dict(os.environ, {"FINMIND_TOKEN": "dummy-token"})
        self.token_patch.start()
        module.initialize_database()
        with database.get_connection() as connection:
            connection.execute(
                "INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, ?)",
                ("6488", "環球晶", "OTC", "2026-01-01T00:00:00"),
            )
            connection.execute(
                "INSERT INTO stocks (stock_code, stock_name, market, updated_at) VALUES (?, ?, ?, ?)",
                ("2330", "台積電", "TSE", "2026-01-01T00:00:00"),
            )

    def tearDown(self):
        self.token_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_backfill_only_inserts_known_otc_codes_and_never_touches_tse(self):
        def fake_fetcher(url, params):
            return {"data": [FINMIND_ROW_OTC, FINMIND_ROW_TSE]}

        result = backfill_otc_gap(
            date(2026, 7, 21), date(2026, 7, 21), delay=0, fetcher=fake_fetcher
        )
        self.assertEqual(result["insertedBars"], 1)
        self.assertEqual(result["daysWithData"], 1)
        with database.get_connection() as connection:
            otc_bar = connection.execute(
                "SELECT close FROM bars_1d WHERE stock_code = '6488'"
            ).fetchone()
            tse_bar = connection.execute(
                "SELECT close FROM bars_1d WHERE stock_code = '2330'"
            ).fetchone()
            tse_market = connection.execute(
                "SELECT market FROM stocks WHERE stock_code = '2330'"
            ).fetchone()[0]
        self.assertEqual(otc_bar[0], 500.0)
        self.assertIsNone(tse_bar)  # TSE行情早就有官方TWSE資料，這裡完全不該碰
        self.assertEqual(tse_market, "TSE")  # market欄位沒被FinMind混進來的資料誤蓋成OTC

    def test_backfill_is_idempotent_across_repeated_calls(self):
        def fake_fetcher(url, params):
            return {"data": [FINMIND_ROW_OTC]}

        first = backfill_otc_gap(date(2026, 7, 21), date(2026, 7, 21), delay=0, fetcher=fake_fetcher)
        second = backfill_otc_gap(date(2026, 7, 21), date(2026, 7, 21), delay=0, fetcher=fake_fetcher)
        self.assertEqual(first["insertedBars"], 1)
        self.assertEqual(second["insertedBars"], 0)  # 已存在的bar不會被重複計入或覆蓋
        with database.get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM bars_1d WHERE stock_code = '6488'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_backfill_skips_day_with_no_finmind_data_without_error(self):
        def fake_fetcher(url, params):
            return {"data": []}

        result = backfill_otc_gap(date(2026, 7, 21), date(2026, 7, 21), delay=0, fetcher=fake_fetcher)
        self.assertEqual(result["insertedBars"], 0)
        self.assertEqual(result["daysWithData"], 0)
        self.assertEqual(result["failures"], [])

    def test_backfill_records_failure_but_keeps_going(self):
        def broken_fetcher(url, params):
            raise RuntimeError("network down")

        result = backfill_otc_gap(date(2026, 7, 20), date(2026, 7, 21), delay=0, fetcher=broken_fetcher)
        self.assertEqual(len(result["failures"]), 2)
        self.assertEqual(result["insertedBars"], 0)

    def test_backfill_state_roundtrip(self):
        self.assertFalse(backfill_state()["done"])
        module._mark_state(True, {"insertedBars": 42})
        state = backfill_state()
        self.assertTrue(state["done"])
        self.assertEqual(state["result"]["insertedBars"], 42)

    def test_run_once_skips_when_already_done(self):
        module._mark_state(True, {"insertedBars": 1})
        with patch.object(module, "backfill_otc_gap") as mock_backfill:
            module._run_once()
        mock_backfill.assert_not_called()

    def test_run_once_marks_done_only_when_no_failures(self):
        with patch.object(module, "backfill_otc_gap", return_value={"failures": [], "insertedBars": 3}):
            module._run_once()
        self.assertTrue(backfill_state()["done"])

    def test_run_once_leaves_undone_when_failures_present(self):
        with patch.object(
            module, "backfill_otc_gap",
            return_value={"failures": [{"date": "2026-07-21", "error": "x"}], "insertedBars": 0},
        ):
            module._run_once()
        self.assertFalse(backfill_state()["done"])


class OtcGapBackfillUnitTests(unittest.TestCase):
    def test_row_to_otc_bar_accepts_known_code(self):
        bar = _row_to_otc_bar(FINMIND_ROW_OTC, date(2026, 7, 21), {"6488"}, {"6488": "環球晶"})
        self.assertIsNotNone(bar)
        self.assertEqual(bar["stock_code"], "6488")
        self.assertEqual(bar["market"], "OTC")
        self.assertEqual(bar["close"], 500.0)
        self.assertEqual(bar["volume"], 1234)  # 1,234,000股換算成張

    def test_row_to_otc_bar_rejects_code_not_in_known_otc_set(self):
        # 2330是上市股票；即使FinMind回傳了資料，不在known_codes(OTC)裡就不採用。
        bar = _row_to_otc_bar(FINMIND_ROW_TSE, date(2026, 7, 21), {"6488"}, {})
        self.assertIsNone(bar)

    def test_row_to_otc_bar_rejects_missing_or_zero_price(self):
        broken = {**FINMIND_ROW_OTC, "close": 0}
        self.assertIsNone(_row_to_otc_bar(broken, date(2026, 7, 21), {"6488"}, {}))
        missing = {"stock_id": "6488", "open": 1, "max": 1, "min": 1}
        self.assertIsNone(_row_to_otc_bar(missing, date(2026, 7, 21), {"6488"}, {}))

    def test_fetch_finmind_price_day_returns_empty_without_token(self):
        with patch.dict(os.environ, {"FINMIND_TOKEN": ""}):
            rows = fetch_finmind_price_day(date(2026, 7, 21), fetcher=lambda url, params: {"data": [FINMIND_ROW_OTC]})
        self.assertEqual(rows, [])

    def test_fetch_finmind_price_day_returns_data_list(self):
        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy-token"}):
            rows = fetch_finmind_price_day(date(2026, 7, 21), fetcher=lambda url, params: {"data": [FINMIND_ROW_OTC]})
        self.assertEqual(rows, [FINMIND_ROW_OTC])

    def test_fetch_finmind_price_day_propagates_fetcher_exception(self):
        # 讓例外往外拋，backfill_otc_gap才能把這天記錄成failures而不是「沒交易」，
        # 否則_run_once會把單純網路失敗誤標記成整段回補已完成、不再重試。
        def broken(url, params):
            raise RuntimeError("network down")

        with patch.dict(os.environ, {"FINMIND_TOKEN": "dummy-token"}):
            with self.assertRaises(RuntimeError):
                fetch_finmind_price_day(date(2026, 7, 21), fetcher=broken)


if __name__ == "__main__":
    unittest.main()
