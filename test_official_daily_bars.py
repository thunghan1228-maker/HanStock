from datetime import date, datetime, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

import official_daily_bars as official_module

from official_daily_bars import (
    TPEX_DAILY_URL,
    TPEX_LEGACY_URL,
    TPEX_OPENAPI_URL,
    TWSE_DAILY_URL,
    fetch_tpex_day,
    fetch_tpex_openapi_snapshot,
    fetch_twse_day,
    fetch_official_day,
    parse_market_payload,
    _save_day,
)


TRADE_DATE = date(2026, 8, 14)


def test_parses_twse_modern_table_and_filters_warrants():
    payload = {
        "tables": [{
            "fields": [
                "證券代號", "證券名稱", "成交股數", "開盤價", "最高價", "最低價", "收盤價"
            ],
            "data": [
                ["2330", "台積電", "12,345", "100", "105", "99", "104"],
                ["00403A", "主動式ETF", "8,000", "10", "10.5", "9.8", "10.3"],
                ["082345", "權證", "999", "1", "1.1", "0.9", "1"],
            ],
        }]
    }
    rows = parse_market_payload(payload, TRADE_DATE, "TSE")
    assert [row["stock_code"] for row in rows] == ["00403A", "2330"]
    assert rows[1]["volume"] == 12  # 12,345股換算成張(1張=1000股)
    assert rows[1]["time"].isoformat() == "2026-08-14T00:00:00+00:00"


def test_parses_tpex_legacy_aadata():
    payload = {
        "aaData": [
            ["6488", "環球晶", "500", "+5", "490", "510", "485", "499", "1,234", "0", "10"]
        ]
    }
    rows = parse_market_payload(payload, TRADE_DATE, "OTC")
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "6488"
    assert rows[0]["close"] == 500.0
    assert rows[0]["volume"] == 1  # 1,234股換算成張


def test_skips_rows_without_published_prices():
    payload = {
        "fields": ["代號", "名稱", "成交量", "開盤", "最高", "最低", "收盤"],
        "data": [["1234", "停牌股", "0", "--", "--", "--", "--"]],
    }
    assert parse_market_payload(payload, TRADE_DATE, "TSE") == []


def test_twse_uses_one_market_request_per_date():
    calls = []

    def fake_fetcher(url, params):
        calls.append((url, params))
        return {"fields": ["代號", "名稱", "成交量", "開盤", "最高", "最低", "收盤"], "data": []}

    assert fetch_twse_day(TRADE_DATE, fetcher=fake_fetcher) == []
    assert calls == [(TWSE_DAILY_URL, {"date": "20260814", "type": "ALLBUT0999", "response": "json"})]


def test_openapi_snapshot_parses_roc_date_and_english_fields():
    payload = [
        {
            "Date": "1150814", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
            "OpeningPrice": "490", "HighestPrice": "510", "LowestPrice": "485",
            "ClosingPrice": "500", "TradingShares": "1,234,000",
        },
        {
            "Date": "1150814", "SecuritiesCompanyCode": "082345", "CompanyName": "權證",
            "OpeningPrice": "1", "HighestPrice": "1.1", "LowestPrice": "0.9",
            "ClosingPrice": "1", "TradingShares": "999",
        },
    ]
    trade_date, rows = fetch_tpex_openapi_snapshot(fetcher=lambda url, params: payload)
    assert trade_date == date(2026, 8, 14)
    assert [row["stock_code"] for row in rows] == ["6488"]  # 權證代號被排除
    assert rows[0]["close"] == 500.0
    assert rows[0]["volume"] == 1234  # 1,234,000股換算成張
    assert rows[0]["market"] == "OTC"


def test_openapi_snapshot_returns_none_when_not_a_list():
    trade_date, rows = fetch_tpex_openapi_snapshot(fetcher=lambda url, params: {"aaData": []})
    assert trade_date is None
    assert rows == []


def test_openapi_snapshot_returns_none_when_date_unparseable():
    payload = [{"Date": "not-a-date", "SecuritiesCompanyCode": "6488", "ClosingPrice": "500"}]
    trade_date, rows = fetch_tpex_openapi_snapshot(fetcher=lambda url, params: payload)
    assert trade_date is None
    assert rows == []


def test_openapi_snapshot_survives_fetcher_exception():
    def broken_fetcher(url, params):
        raise RuntimeError("network down")

    trade_date, rows = fetch_tpex_openapi_snapshot(fetcher=broken_fetcher)
    assert trade_date is None
    assert rows == []


def test_tpex_day_uses_openapi_snapshot_when_date_matches():
    calls = []

    def fake_fetcher(url, params):
        calls.append(url)
        assert url == TPEX_OPENAPI_URL, "日期匹配時不該再嘗試舊端點"
        return [{
            "Date": "1150814", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
            "OpeningPrice": "490", "HighestPrice": "510", "LowestPrice": "485",
            "ClosingPrice": "500", "TradingShares": "1234000",
        }]

    rows = fetch_tpex_day(TRADE_DATE, fetcher=fake_fetcher)
    assert calls == [TPEX_OPENAPI_URL]
    assert rows[0]["stock_code"] == "6488"


def test_tpex_falls_back_to_legacy_endpoint_when_modern_is_empty():
    calls = []

    def fake_fetcher(url, params):
        calls.append((url, params))
        if url == TPEX_OPENAPI_URL:
            return []  # 沒有快照資料可用（不是list-of-dict），直接往下走舊端點
        if url == TPEX_DAILY_URL:
            return {"tables": []}
        return {
            "aaData": [["6488", "環球晶", "500", "+5", "490", "510", "485", "499", "1,234", "0", "10"]]
        }

    rows = fetch_tpex_day(TRADE_DATE, fetcher=fake_fetcher)
    assert rows[0]["stock_code"] == "6488"
    assert calls[0][0] == TPEX_OPENAPI_URL
    assert calls[1][0] == TPEX_DAILY_URL
    assert calls[2][0] == TPEX_LEGACY_URL


def test_closed_market_day_never_accepts_stale_tpex_rows():
    tpex_called = False

    def empty_twse(_trade_date):
        return []

    def stale_tpex(_trade_date):
        nonlocal tpex_called
        tpex_called = True
        return [{"stock_code": "6488"}]

    rows = fetch_official_day(
        TRADE_DATE,
        twse_loader=empty_twse,
        tpex_loader=stale_tpex,
    )
    assert rows == []
    assert tpex_called is False


def test_save_day_fills_gaps_without_overwriting_existing_bar():
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "test.db"

        def connection_factory():
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            return connection

        with connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE stocks (
                    stock_code TEXT PRIMARY KEY, stock_name TEXT NOT NULL,
                    market TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE bars_1d (
                    stock_code TEXT NOT NULL, bar_time TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume INTEGER NOT NULL,
                    PRIMARY KEY (stock_code, bar_time)
                );
                """
            )

        original_get_connection = official_module.get_connection
        official_module.get_connection = connection_factory
        try:
            row = {
                "stock_code": "2330", "stock_name": "台積電", "market": "TSE",
                "time": datetime(2026, 8, 14, tzinfo=timezone.utc),
                "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
                "volume": 1000,
            }
            assert _save_day([row]) == 1
            assert _save_day([{**row, "close": 999.0}]) == 0
            with connection_factory() as connection:
                saved = connection.execute(
                    "SELECT close FROM bars_1d WHERE stock_code = '2330'"
                ).fetchone()[0]
            assert saved == 104.0
        finally:
            official_module.get_connection = original_get_connection


def load_tests(loader, tests, pattern):  # noqa: ARG001
    suite = unittest.TestSuite()
    for function in (
        test_parses_twse_modern_table_and_filters_warrants,
        test_parses_tpex_legacy_aadata,
        test_skips_rows_without_published_prices,
        test_twse_uses_one_market_request_per_date,
        test_tpex_falls_back_to_legacy_endpoint_when_modern_is_empty,
        test_closed_market_day_never_accepts_stale_tpex_rows,
        test_save_day_fills_gaps_without_overwriting_existing_bar,
        test_tpex_failure_falls_back_to_finmind_for_otc_rows,
    ):
        suite.addTest(unittest.FunctionTestCase(function))
    return suite


def test_tpex_failure_falls_back_to_finmind_for_otc_rows():
    # 櫃買中心從 Railway 出去被擋（2026-09-22 起 403／連線重置）：上櫃日K改用 FinMind 補，
    # 上市照常來自證交所；有補到才寫入，失敗原因跟補了幾檔都記在 source_failures。
    trade_date = date(2026, 9, 22)
    saved: list[list[dict]] = []
    twse_row = {
        "stock_code": "2330", "stock_name": "台積電", "market": "TSE",
        "time": datetime(2026, 9, 22, tzinfo=timezone.utc),
        "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 1000,
    }
    otc_row = {**twse_row, "stock_code": "6218", "stock_name": "豪勉", "market": "OTC", "high": 64.0, "close": 61.0}
    originals = {
        name: getattr(official_module, name)
        for name in ("fetch_twse_day", "fetch_tpex_day", "_finmind_otc_day", "_otc_bars_exist", "_save_day", "get_connection")
    }

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args):
            class _Row:
                def fetchone(self):
                    return [0]
            return _Row()

    def _raise(_trade_date):
        raise RuntimeError("官方盤後資料取得失敗：Connection reset by peer")

    official_module.fetch_twse_day = lambda _trade_date: [twse_row]
    official_module.fetch_tpex_day = _raise
    official_module._finmind_otc_day = lambda _trade_date: ([otc_row], "FinMind 補上櫃 1 檔")
    official_module._otc_bars_exist = lambda _trade_date, minimum=100: False
    official_module._save_day = lambda rows: saved.append(rows) or len(rows)
    official_module.get_connection = lambda: _Conn()
    try:
        result = official_module.download_official_daily_bars(days=60, delay=0, end_date=trade_date, run_triangle_scan=False)
    finally:
        for name, value in originals.items():
            setattr(official_module, name, value)

    assert any(rows and {row["stock_code"] for row in rows} == {"2330", "6218"} for rows in saved)
    failure = [f for f in result["source_failures"] if f["date"] == "2026-09-22" and f["source"] == "TPEx"][0]
    assert "Connection reset" in failure["error"]
    assert failure["finmind"] == "FinMind 補上櫃 1 檔"
