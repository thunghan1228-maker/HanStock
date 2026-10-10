"""記憶體診斷：取樣、暴增紀錄、請求紀錄、端點。"""

from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

import memory_diag


class MemoryDiagTests(unittest.TestCase):
    def setUp(self):
        memory_diag._history.clear()
        memory_diag._events.clear()

    def test_process_memory_reads_proc(self):
        mem = memory_diag.process_memory()
        if os.path.exists("/proc/self/status"):
            self.assertGreater(mem["rssMb"], 0)
            self.assertGreaterEqual(mem["peakRssMb"], mem["rssMb"])

    def test_thread_activity_names_project_function(self):
        started, release = threading.Event(), threading.Event()

        def busy_worker():
            started.set()
            release.wait(5)

        t = threading.Thread(target=busy_worker, name="diag-test-worker", daemon=True)
        t.start()
        started.wait(5)
        try:
            row = next(x for x in memory_diag.thread_activity() if x["thread"] == "diag-test-worker")
            self.assertIn("test_memory_diag.py", row["at"])
            self.assertIn("busy_worker", row["at"])
        finally:
            release.set()
            t.join(5)

    def test_spike_records_event(self):
        with patch.object(memory_diag, "process_memory", return_value={"rssMb": 900, "peakRssMb": 900}):
            self.assertEqual(memory_diag._sample_once(100), 900)
        self.assertEqual(len(memory_diag._history), 1)
        self.assertEqual(memory_diag._events[-1]["kind"], "spike")
        self.assertEqual((memory_diag._events[-1]["fromMb"], memory_diag._events[-1]["toMb"]), (100, 900))

    def test_small_change_is_not_spike(self):
        with patch.object(memory_diag, "process_memory", return_value={"rssMb": 150, "peakRssMb": 150}):
            memory_diag._sample_once(100)
        self.assertEqual(len(memory_diag._events), 0)

    def test_request_only_logged_when_big(self):
        memory_diag.note_request("/api/small", 100, 120, 0.1)
        memory_diag.note_request("/api/big", 100, 100 + memory_diag.REQUEST_SPIKE_MB, 2.0)
        self.assertEqual([e["path"] for e in memory_diag._events], ["/api/big"])

    def test_payload_shape(self):
        data = memory_diag.payload(10)
        for key in ("process", "container", "threads", "events", "history", "settings"):
            self.assertIn(key, data)
        self.assertIsNone(data["tracemalloc"])


if __name__ == "__main__":
    unittest.main()


class TableSizesTests(unittest.TestCase):
    def test_table_sizes_in_background(self):
        import tempfile
        import time
        from pathlib import Path

        import database

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(database, "DATABASE_PATH", Path(temp_dir) / "t.db"):
            database.initialize_database()
            memory_diag._table_sizes.update({"at": 0.0, "running": False, "tables": None, "error": None})
            first = memory_diag.table_sizes()
            self.assertTrue(first["running"])
            self.assertIn("t.db", first["files"]["filesMb"])
            for _ in range(50):
                if not memory_diag._table_sizes["running"]:
                    break
                time.sleep(0.05)
            second = memory_diag.table_sizes()
            self.assertFalse(second["running"])
            self.assertIsNone(second["error"])
            names = {row["table"] for row in second["tables"]}
            self.assertIn("bars_1d", names)
            self.assertFalse(any(n.startswith(("sqlite_autoindex", "idx_")) for n in names))   # 索引算在它的表底下
