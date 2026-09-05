from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from daytrade_flow_store import (
    begin_daytrade_scan,
    finish_daytrade_scan,
    load_daytrade_rows,
    load_daytrade_scan_status,
)


class DaytradeFlowStoreTests(unittest.TestCase):
    def test_existing_database_is_migrated_and_data_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hanstock.db"
            with sqlite3.connect(path, factory=database.ClosingConnection) as connection:
                connection.executescript(
                    """
                    CREATE TABLE daytrade_flow_daily_v2 (
                        ticker TEXT NOT NULL, trade_date TEXT NOT NULL, name TEXT NOT NULL,
                        market TEXT NOT NULL, category TEXT NOT NULL, open_price REAL NOT NULL DEFAULT 0,
                        high_price REAL NOT NULL DEFAULT 0, close_price REAL NOT NULL DEFAULT 0,
                        reference_price REAL NOT NULL DEFAULT 0, limit_up_price REAL NOT NULL DEFAULT 0,
                        day_change_pct REAL NOT NULL DEFAULT 0, large_buy_amount REAL NOT NULL DEFAULT 0,
                        large_sell_amount REAL NOT NULL DEFAULT 0, total_turnover_amount REAL NOT NULL DEFAULT 0,
                        late_large_buy_amount REAL NOT NULL DEFAULT 0, price_impact_pct REAL NOT NULL DEFAULT 0,
                        previous_large_buy_amount REAL NOT NULL DEFAULT 0,
                        next_day_large_sell_amount REAL NOT NULL DEFAULT 0,
                        suspicion_score REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, trade_date)
                    );
                    CREATE TABLE daytrade_flow_scan_jobs (
                        trade_date TEXT PRIMARY KEY, status TEXT NOT NULL,
                        requested_count INTEGER NOT NULL DEFAULT 0,
                        processed_count INTEGER NOT NULL DEFAULT 0,
                        match_count INTEGER NOT NULL DEFAULT 0,
                        error_count INTEGER NOT NULL DEFAULT 0,
                        errors_json TEXT NOT NULL DEFAULT '[]', started_at TEXT,
                        completed_at TEXT, updated_at TEXT NOT NULL
                    );
                    """
                )
            row = {
                "ticker": "3037", "trade_date": "2026-08-17", "name": "欣興",
                "market": "上市", "category": "漲停鎖定", "close_price": 110,
                "reference_price": 100, "limit_up_price": 110, "day_change_pct": 10,
                "total_turnover_amount": 4_000_000_000,
                "main_force_data_status": "pending_backfill",
                "main_force_data_available": False,
            }
            with patch.object(database, "DATABASE_PATH", path):
                begin_daytrade_scan("2026-08-17", 1)
                finish_daytrade_scan("2026-08-17", [row], processed_count=1, incomplete_count=1)
                saved = load_daytrade_rows("2026-08-17")
                status = load_daytrade_scan_status("2026-08-17")
            self.assertEqual(saved[0]["main_force_data_status"], "pending_backfill")
            self.assertEqual(saved[0]["main_force_data_available"], 0)
            self.assertEqual(status["status"], "partial")
            self.assertEqual(status["data_missing_count"], 1)


if __name__ == "__main__":
    unittest.main()
