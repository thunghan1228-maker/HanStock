import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from after_hours_fixed_price import (
    load_after_hours_day,
    load_latest_after_hours_day,
    parse_after_hours_payload,
    save_after_hours_day,
)


class ParseAfterHoursPayloadTests(unittest.TestCase):
    def test_parses_table_with_matching_field_aliases(self):
        payload = {
            "tables": [
                {
                    "fields": ["證券代號", "證券名稱", "成交價", "成交股數"],
                    "data": [
                        ["2330", "台積電", "1,105.00", "1,234,567"],
                        ["2317", "鴻海", "205.50", "890,000"],
                    ],
                }
            ]
        }

        entries = parse_after_hours_payload(payload)

        self.assertEqual(len(entries), 2)
        by_code = {e["stock_code"]: e for e in entries}
        self.assertEqual(by_code["2330"]["stock_name"], "台積電")
        self.assertEqual(by_code["2330"]["price"], 1105.0)
        self.assertEqual(by_code["2330"]["volume"], 1_234_567)
        self.assertEqual(by_code["2317"]["price"], 205.5)

    def test_ignores_rows_with_zero_price_or_volume_or_ineligible_code(self):
        payload = {
            "tables": [
                {
                    "fields": ["證券代號", "證券名稱", "成交價", "成交股數"],
                    "data": [
                        ["2330", "台積電", "0", "1,000"],
                        ["2317", "鴻海", "205.50", "0"],
                        ["ABC", "非股票代號", "50.00", "1,000"],
                        ["1101", "台泥", "--", "1,000"],
                    ],
                }
            ]
        }

        entries = parse_after_hours_payload(payload)

        self.assertEqual(entries, [])

    def test_returns_empty_list_when_no_recognisable_table(self):
        payload = {"stat": "查無資料", "date": "20260101"}

        entries = parse_after_hours_payload(payload)

        self.assertEqual(entries, [])

    def test_supports_legacy_indexed_fields_and_data_keys(self):
        payload = {
            "fields1": ["股票代號", "股票名稱", "盤後定價", "成交數量"],
            "data1": [["0050", "元大台灣50", "45.30", "500,000"]],
        }

        entries = parse_after_hours_payload(payload)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["stock_code"], "0050")
        self.assertEqual(entries[0]["price"], 45.3)


class AfterHoursFixedPriceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_save_and_load_round_trip_ordered_by_volume_desc(self):
        entries = [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ]

        saved = save_after_hours_day("2026-09-18", entries)

        self.assertEqual(saved, 2)
        rows = load_after_hours_day("2026-09-18")
        self.assertEqual([r["code"] for r in rows], ["2317", "2330"])
        self.assertEqual(rows[0]["name"], "鴻海")
        self.assertEqual(rows[0]["volume"], 5000)
        self.assertEqual(rows[0]["price"], 205.5)

    def test_load_returns_empty_list_for_date_with_no_data(self):
        self.assertEqual(load_after_hours_day("2000-01-01"), [])

    def test_save_upserts_existing_entry_for_same_trade_date_and_code(self):
        save_after_hours_day("2026-09-18", [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1000.0, "volume": 1000},
        ])
        save_after_hours_day("2026-09-18", [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 2000},
        ])

        rows = load_after_hours_day("2026-09-18")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 1105.0)
        self.assertEqual(rows[0]["volume"], 2000)

    def test_save_with_empty_entries_is_noop(self):
        self.assertEqual(save_after_hours_day("2026-09-18", []), 0)
        self.assertEqual(load_after_hours_day("2026-09-18"), [])

    def test_load_latest_returns_newest_collected_day(self):
        # 14:30前盤後定價分頁要顯示「最近一個交易日」的資料，不是空的也不是大戶力排行。
        save_after_hours_day("2026-09-18", [
            {"stock_code": "2330", "stock_name": "台積電", "price": 1105.0, "volume": 1000},
        ])
        save_after_hours_day("2026-09-21", [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])

        trade_date, rows = load_latest_after_hours_day()

        self.assertEqual(trade_date, "2026-09-21")
        self.assertEqual([r["code"] for r in rows], ["2317"])

    def test_load_latest_respects_on_or_before_and_empty_store(self):
        self.assertEqual(load_latest_after_hours_day(), (None, []))
        save_after_hours_day("2026-09-21", [
            {"stock_code": "2317", "stock_name": "鴻海", "price": 205.5, "volume": 5000},
        ])

        self.assertEqual(load_latest_after_hours_day(on_or_before="2026-09-20"), (None, []))
        self.assertEqual(load_latest_after_hours_day(on_or_before="2026-09-21")[0], "2026-09-21")


if __name__ == "__main__":
    unittest.main()
