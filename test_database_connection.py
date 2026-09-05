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
