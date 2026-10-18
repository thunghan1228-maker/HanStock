from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import holder_force_recalc
from holder_force_recalc import normalize_ticks, parse_cutoff, recalc, summarize_until
from otc_index import TW_TZ


def _ts(h, m, s, us=0):
    return datetime(2026, 10, 16, h, m, s, us, tzinfo=TW_TZ)


def _ticks():
    return {
        "ts": [
            _ts(8, 59, 59),          # 盤前，不算
            _ts(9, 0, 0),            # 開盤 10 張外盤
            _ts(9, 0, 5, 100),       # 同秒同方向：4+4 張內盤 → 併單 8 張
            _ts(9, 0, 5, 900),
            _ts(9, 0, 5, 950),       # 同秒反方向 3 張外盤，不併進內盤
            _ts(9, 21, 0),           # 09:20 之後
        ],
        "close": [100, 100, 100, 100, 100, 100],
        "volume": [50, 10, 4, 4, 3, 30],
        "tick_type": [1, 1, 2, 2, 1, 2],
        "amount": [0, 1_000_000, 400_000, 400_000, 300_000, 3_000_000],
        "simtrade": [0, 0, 0, 0, 0, 0],
    }


def test_parse_cutoff():
    assert parse_cutoff("09:20") == 9 * 3600 + 20 * 60
    assert parse_cutoff("13:30:05") == 13 * 3600 + 30 * 60 + 5


def test_same_second_same_side_ticks_are_merged_before_threshold():
    rows = normalize_ticks(_ticks(), trade_date="2026-10-16")
    assert len(rows) == 5
    summary = summarize_until(rows, parse_cutoff("09:20"))
    assert summary["totalAmount"] == 2_100_000
    single7 = summary["variants"]["t7"]
    merged7 = summary["variants"]["s7"]
    # 單筆 ≥7 張只有開盤 10 張外盤；併單後內盤 8 張也算大戶賣。
    assert single7["buy"] == 1_000_000 and single7["sell"] == 0
    assert merged7["buy"] == 1_000_000 and merged7["sell"] == 800_000
    assert merged7["pct"] == round(200_000 / 2_100_000 * 100, 2)
    assert summary["variants"]["s20"]["pct"] == 0


def test_recalc_fetches_each_code_once_and_reports_cutoffs(monkeypatch):
    calls = []

    def fake_fetch(api, contract, trade_date):
        calls.append((contract, trade_date))
        return _ticks()

    monkeypatch.setattr(holder_force_recalc, "_fetch_historical_ticks", fake_fetch)
    monkeypatch.setattr(holder_force_recalc, "_resolve_stock_contract", lambda service, code: f"C{code}")
    holder_force_recalc._cache.clear()
    service = SimpleNamespace(api=object(), state=SimpleNamespace(logged_in=True))
    cutoffs = [parse_cutoff("09:20"), parse_cutoff("13:30")]
    first = recalc(service, ["1303"], "2026-10-16", cutoffs)
    again = recalc(service, ["1303"], "2026-10-16", cutoffs)
    assert calls == [("C1303", "2026-10-16")]
    assert first == again
    item = first["items"]["1303"]
    assert item["opening"]["time"] == "09:00:00"
    assert item["cutoffs"]["09:20"]["ticks"] == 4
    assert item["cutoffs"]["13:30"]["ticks"] == 5
    assert item["cutoffs"]["13:30"]["variants"]["t20"]["sell"] == 3_000_000


def test_recalc_skips_codes_when_quota_is_exhausted(monkeypatch):
    monkeypatch.setattr(holder_force_recalc, "_fetch_historical_ticks", lambda *a: _ticks())
    monkeypatch.setattr(holder_force_recalc, "_resolve_stock_contract", lambda service, code: code)
    holder_force_recalc._cache.clear()
    service = SimpleNamespace(api=object(), state=SimpleNamespace(logged_in=True))
    data = recalc(service, ["2330"], "2026-10-16", [], quota_check=lambda api: "額度用完")
    assert data["items"] == {}
    assert data["errors"] == {"2330": "額度用完"}
