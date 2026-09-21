import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database


class DatabaseConnectionTests(unittest.TestCase):
    def test_commit_and_rollback_both_close_connection_without_gc(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, 'DATABASE_PATH', Path(directory) / 'test.db'):
            with database.get_connection() as connection:
                connection.execute('CREATE TABLE sample (value INTEGER)')
                connection.execute('INSERT INTO sample VALUES (1)')
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
            with self.assertRaisesRegex(ValueError, 'abort'):
                with database.get_connection() as failed:
                    failed.execute('INSERT INTO sample VALUES (2)')
                    raise ValueError('abort')
            with self.assertRaises(sqlite3.ProgrammingError):
                failed.execute('SELECT 1')
            with database.get_connection() as reopened:
                rows = reopened.execute('SELECT value FROM sample').fetchall()
            self.assertEqual([row['value'] for row in rows], [1])
            # Windows requires the handles to be closed for this cleanup to work.
            database.DATABASE_PATH.unlink()


class DailyBarsVolumeMigrationTests(unittest.TestCase):
    def test_initialize_database_converts_legacy_share_volume_once(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, 'DATABASE_PATH', Path(directory) / 'test.db'):
            database.initialize_database()
            with database.get_connection() as connection:
                # 模擬遷移前寫入的舊資料：volume還是股數，尚未換算成張。
                connection.execute('PRAGMA user_version = 0')
                connection.execute(
                    "INSERT INTO bars_1d (stock_code, bar_time, open, high, low, close, volume) "
                    "VALUES ('2382', '2026-09-18T00:00:00+00:00', 340, 345, 338, 344, 12345000)"
                )
            database.initialize_database()
            with database.get_connection() as connection:
                row = connection.execute("SELECT volume FROM bars_1d WHERE stock_code = '2382'").fetchone()
                version = connection.execute('PRAGMA user_version').fetchone()[0]
            self.assertEqual(row['volume'], 12345)
            self.assertEqual(version, 1)
            # 再呼叫一次不應該重複除以1000。
            database.initialize_database()
            with database.get_connection() as connection:
                row_again = connection.execute("SELECT volume FROM bars_1d WHERE stock_code = '2382'").fetchone()
            self.assertEqual(row_again['volume'], 12345)
            database.DATABASE_PATH.unlink()
