import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from intraday_signal_store import load_latest_signals_by_kind, save_intraday_signals


class ConcurrentDatabaseReadsTests(unittest.TestCase):
    def test_signal_reader_survives_exclusive_writer_and_sees_committed_rows(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            database, "DATABASE_PATH", Path(folder) / "signals.db",
        ):
            signal = {
                "tradeDate": "2026-09-07", "ticker": "2330", "name": "台積電",
                "kind": "instantLargeBuy", "barTs": 1788743400000,
                "price": 100, "note": "test",
            }
            save_intraday_signals([signal])
            writer = database.get_connection()
            try:
                self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                writer.execute("BEGIN EXCLUSIVE")
                writer.execute("UPDATE intraday_signals SET price = 101")
                original_connect = database.get_connection

                def short_timeout_connection():
                    connection = original_connect()
                    connection.execute("PRAGMA busy_timeout = 100")
                    return connection

                with patch("intraday_signal_store.get_connection", short_timeout_connection):
                    rows = load_latest_signals_by_kind("2026-09-07", "instantLargeBuy")
                self.assertEqual(rows[0]["price"], 100, "Uncommitted changes must not leak to readers")
                writer.commit()
                self.assertEqual(load_latest_signals_by_kind("2026-09-07", "instantLargeBuy")[0]["price"], 101)
            finally:
                writer.close()


if __name__ == "__main__":
    unittest.main()
