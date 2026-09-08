import ast
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import database
from history_quota import HistoryQuotaGate, QUOTA_EXHAUSTED
from main_force_backfill_jobs import request_main_force_backfill, process_main_force_backfill_job
from main_force_store import save_main_force_bars, load_main_force_bars
from stock_bar_bootstrap import _HistoryEntry, _store_entry, _cached_entry, clear_stock_bar_bootstrap_cache
from otc_index import TW_TZ


class HistoryQuotaTests(unittest.TestCase):
    def test_exhausted_usage_is_cached_and_recovery_reopens_history(self):
        api = SimpleNamespace(usage=Mock(side_effect=[
            SimpleNamespace(limit_bytes=500, remaining_bytes=-100),
            SimpleNamespace(limit_bytes=500, remaining_bytes=450),
        ]))
        gate = HistoryQuotaGate()
        self.assertEqual(gate.check(api, now=0), QUOTA_EXHAUSTED)
        self.assertEqual(gate.check(api, now=299), QUOTA_EXHAUSTED)
        self.assertEqual(api.usage.call_count, 1)
        self.assertIsNone(gate.check(api, now=300))
        self.assertEqual(api.usage.call_count, 2)

    def test_failed_usage_does_not_clear_known_exhaustion(self):
        api = SimpleNamespace(usage=Mock(side_effect=[
            {"limit_bytes": 500, "remaining_bytes": 0}, RuntimeError("network"),
        ]))
        gate = HistoryQuotaGate()
        self.assertEqual(gate.check(api, now=0), QUOTA_EXHAUSTED)
        self.assertEqual(gate.check(api, now=300), QUOTA_EXHAUSTED)

    def test_positive_usage_and_unsupported_api_preserve_existing_behavior(self):
        gate = HistoryQuotaGate()
        self.assertIsNone(gate.check(object(), now=0))
        api = SimpleNamespace(usage=Mock(return_value={"limit_bytes": 500, "remaining_bytes": 1}))
        self.assertIsNone(gate.check(api, now=0))
        self.assertIsNone(gate.check(api, now=59))
        self.assertEqual(api.usage.call_count, 1)

    def test_new_session_does_not_inherit_previous_session_usage(self):
        gate = HistoryQuotaGate()
        self.assertEqual(gate.check(SimpleNamespace(usage=lambda: {"limit_bytes": 500, "remaining_bytes": 0}), now=0), QUOTA_EXHAUSTED)
        self.assertIsNone(gate.check(SimpleNamespace(usage=lambda: {"limit_bytes": 500, "remaining_bytes": 100}), now=1))

    def test_known_quota_exhaustion_stops_kbars_and_ticks(self):
        from test_stock_bar_bootstrap import FakeApi
        from stock_bar_bootstrap import _bootstrap_history
        api = FakeApi()
        api.usage = lambda: {"limit_bytes": 500, "remaining_bytes": -1}
        service = SimpleNamespace(api=api, state=SimpleNamespace(logged_in=True))
        with patch("stock_bar_bootstrap.history_quota", HistoryQuotaGate()):
            entry = _bootstrap_history("2455", "2026-09-08", service=service,
                                       now_ms=1788876000000, monotonic_fn=lambda: 0)
        self.assertFalse(entry.ok)
        self.assertIn("history_quota_exhausted", entry.error)
        self.assertEqual((api.kbars_calls, api.ticks_calls), (0, 0))


class MainForceBackfillJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp.name) / "test.db")
        self.db_patch.start()
        self.now = datetime(2026, 9, 8, 23, 0, tzinfo=TW_TZ).timestamp()
        self.success = {"history_ok": True, "main_force_ok": True, "saved_1m": 40, "saved_5m": 40}

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_partial_day_is_queued_and_duplicate_request_does_not_reset_retry(self):
        save_main_force_bars("2455", "5m", [{"ts": 1788832800000, "main_buy_volume": 67,
            "main_sell_volume": 56, "main_force_available": True}])
        self.assertTrue(request_main_force_backfill("2455", "2026-09-08", now=self.now)["queued"])
        fail = Mock(return_value={"history_ok": False, "main_force_ok": False, "error": QUOTA_EXHAUSTED})
        process_main_force_backfill_job(now=self.now, backfill=fail)
        requested = request_main_force_backfill("2455", "2026-09-08", now=self.now + 1)
        self.assertEqual(requested["attempts"], 1)
        self.assertEqual(requested["nextAttemptAt"], self.now + 300)
        self.assertIsNone(process_main_force_backfill_job(now=self.now + 1, backfill=fail))
        rows = load_main_force_bars("2455", "5m", trade_date="2026-09-08")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["main_net_volume"], 11)

    def test_failed_job_survives_new_connection_and_date_rollover_then_completes(self):
        request_main_force_backfill("2455", "2026-09-08", now=self.now)
        process_main_force_backfill_job(now=self.now, backfill=lambda *a, **kw: {"error": QUOTA_EXHAUSTED})
        repair = Mock(return_value=self.success)
        tomorrow = self.now + 9 * 3600
        result = process_main_force_backfill_job(now=tomorrow, backfill=repair)
        self.assertEqual(result["status"], "complete")
        repair.assert_called_once_with("2455", "2026-09-08", service=None, now_ms=int(tomorrow * 1000))
        self.assertIsNone(process_main_force_backfill_job(now=tomorrow + 900, backfill=repair))

    def test_exception_and_empty_save_remain_pending(self):
        request_main_force_backfill("2455", "2026-09-08", now=self.now)
        result = process_main_force_backfill_job(now=self.now, backfill=Mock(side_effect=RuntimeError("offline")))
        self.assertEqual(result["status"], "pending")
        result = process_main_force_backfill_job(now=self.now + 900, backfill=lambda *a, **kw:
            {"history_ok": True, "main_force_ok": True, "saved_1m": 0, "saved_5m": 0})
        self.assertEqual(result["status"], "pending")

    def test_morning_success_must_be_rechecked_after_close(self):
        now = datetime(2026, 9, 8, 10, 0, tzinfo=TW_TZ).timestamp()
        request_main_force_backfill("2455", "2026-09-08", now=now)
        self.assertEqual(process_main_force_backfill_job(now=now, backfill=lambda *a, **kw: self.success)["status"], "pending")
        self.assertEqual(request_main_force_backfill("2455", "2026-09-08", now=now)["nextAttemptAt"],
                         datetime(2026, 9, 8, 13, 35, tzinfo=TW_TZ).timestamp())

    def test_job_lease_prevents_duplicate_broker_calls(self):
        request_main_force_backfill("2455", "2026-09-08", now=self.now)
        def repair(*args, **kwargs):
            self.assertIsNone(process_main_force_backfill_job(now=self.now, backfill=Mock()))
            return self.success
        self.assertEqual(process_main_force_backfill_job(now=self.now, backfill=repair)["status"], "complete")

    def test_older_repair_does_not_replace_current_day_cache(self):
        clear_stock_bar_bootstrap_cache()
        today = _HistoryEntry("2026-09-09", [{"ts": 1}], [], 0, True, None)
        older = _HistoryEntry("2026-09-08", [{"ts": 0}], [], 0, True, None)
        _store_entry("2455", today)
        self.assertIs(_store_entry("2455", older), older)
        self.assertIs(_cached_entry("2455", "2026-09-09", 0), today)
        clear_stock_bar_bootstrap_cache()

    def test_rejects_future_and_out_of_range_jobs(self):
        for date in ("2026-09-09", "2020-01-01"):
            with self.assertRaises(ValueError):
                request_main_force_backfill("2455", date, now=self.now)

    def test_endpoint_queues_partial_day_but_readonly_never_queues(self):
        # Execute the actual endpoint without starting the unrelated quote services.
        module = ast.parse(Path("persistent_app.py").read_text(encoding="utf-8-sig"))
        fn = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                  and node.name == "get_persisted_main_force_bars")
        fn.decorator_list = []
        writer = Mock(return_value={"queued": True})
        env = {"Any": object, "Query": lambda value, **kw: value,
               "_normalize_stock_code": lambda code: code, "_validate_trade_date": lambda date: date,
               "load_main_force_bars": lambda *a, **kw: [{"ts": 1788832800000}],
               "request_main_force_backfill": writer}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "persistent_app.py", "exec"), env)
        endpoint = env[fn.name]
        data = endpoint("2455", trade_date="2026-09-08", backfill=True)
        self.assertEqual(data["bar_count"], 1)
        self.assertTrue(data["backfill"]["queued"])
        writer.assert_called_once_with("2455", "2026-09-08")
        writer.reset_mock()
        self.assertIsNone(endpoint("2455", trade_date="2026-09-08", backfill=False)["backfill"])
        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
