import io
import json
import threading
import unittest
import ast
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import Mock
from datetime import datetime
from daily_pick_collector import DailyPickCollector, next_refresh_at


def date(value):
    return datetime.fromisoformat(value + "+08:00")


def response(value):
    return io.BytesIO(json.dumps(value).encode())


class DailyPickCollectorTests(unittest.TestCase):
    def test_next_slot_and_weekend(self):
        for now, expected in [
            ("2026-09-09T09:00:00", "2026-09-09T14:30:00"),
            ("2026-09-09T14:29:59", "2026-09-09T14:30:00"),
            ("2026-09-09T14:30:21", "2026-09-09T14:40:00"),
            ("2026-09-09T23:50:20", "2026-09-10T14:30:00"),
            ("2026-09-11T23:50:20", "2026-09-14T14:30:00"),
            ("2026-09-12T15:00:00", "2026-09-14T14:30:00"),
        ]:
            self.assertEqual(next_refresh_at(date(now)), date(expected))

    def test_retry_once_a_minute_only_inside_window(self):
        self.assertEqual(next_refresh_at(date("2026-09-09T14:30:21"), retry=True), date("2026-09-09T14:31:21"))
        self.assertEqual(next_refresh_at(date("2026-09-09T23:59:21"), retry=True), date("2026-09-10T14:30:00"))
        self.assertEqual(next_refresh_at(date("2026-09-09T09:00:00"), retry=True), date("2026-09-09T14:30:00"))

    def test_cloud_request_saves_complete_snapshot_without_browser(self):
        calls = []
        snapshot = {"tradeDate": "2026-09-09", "computedAt": 1000, "bull": [{}] * 19,
                    "bear": [{}] * 28, "bullQualifiedCount": 19, "bearQualifiedCount": 28}
        def open_request(request, timeout):
            calls.append((request.full_url, timeout))
            return response({"ok": True, "snapshot": snapshot})
        collector = DailyPickCollector(clock=lambda: date("2026-09-09T14:30:00"), opener=open_request)
        self.assertTrue(collector.collect_once())
        self.assertEqual(calls, [(collector.url, 60)])
        self.assertTrue(collector.url.endswith("?scheduled=1"))
        self.assertEqual(collector.status()["bullCount"], 19)
        self.assertEqual(collector.status()["bearCount"], 28)
        self.assertIsNotNone(collector.status()["lastSuccessAt"])

    def test_server_holiday_skip_does_not_claim_successful_recalculation(self):
        collector = DailyPickCollector(opener=lambda *a, **k: response({"ok": True, "skipped": True}))
        self.assertTrue(collector.collect_once())
        self.assertEqual(collector.status()["state"], "waiting_for_session")
        self.assertIsNone(collector.status()["lastSuccessAt"])

    def test_failure_or_incomplete_snapshot_is_retried(self):
        for payload in [{"ok": False}, {"ok": True, "refreshing": True}, {"ok": True, "snapshot": {"tradeDate": "2026-09-08"}}, {"ok": True}]:
            collector = DailyPickCollector(opener=lambda *a, **k: response(payload))
            self.assertFalse(collector.collect_once())
            self.assertEqual(collector.status()["state"], "retrying")
        collector = DailyPickCollector(opener=lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")))
        self.assertFalse(collector.collect_once())
        self.assertIsNone(collector.status()["lastSuccessAt"])

    def test_background_thread_starts_once_and_shutdown_interrupts_wait(self):
        done = threading.Event()
        def open_request(*a, **k):
            done.set()
            return response({"ok": True, "skipped": True})
        collector = DailyPickCollector(clock=lambda: date("2026-09-09T09:00:00"), opener=open_request)
        self.assertTrue(collector.start())
        self.assertTrue(done.wait(2))
        self.assertFalse(collector.start())
        collector.stop()
        self.assertFalse(collector.status()["running"])
        self.assertEqual(collector.status()["state"], "stopped")

    def test_real_lifespan_wires_start_and_stop_even_if_context_raises(self):
        tree = ast.parse(Path(__file__).with_name('persistent_app.py').read_text(encoding='utf-8-sig'))
        fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_persistent_lifespan')
        @asynccontextmanager
        async def parent(app):
            yield 'parent-state'
        scope = {'asynccontextmanager': asynccontextmanager, '_market_data_lifespan': parent}
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and node.id.startswith(('start_', 'stop_')):
                scope[node.id] = Mock()
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'persistent_app.py', 'exec'), scope)
        async def exercise():
            with self.assertRaisesRegex(RuntimeError, 'exit'):
                async with scope['_persistent_lifespan'](None) as state:
                    self.assertEqual(state, 'parent-state')
                    raise RuntimeError('exit')
        asyncio.run(exercise())
        scope['start_daily_pick_collector'].assert_called_once_with()
        scope['stop_daily_pick_collector'].assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
