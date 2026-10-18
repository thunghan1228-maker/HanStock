from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import big_holder
import database
from big_holder import avg_turnover, summarize
from main_force_store import load_main_force_bars, save_main_force_bars
from market_data_hub import BarAggregator
from otc_index import TW_TZ
from stock_bar_bootstrap import _historical_tick_metrics


def _ms(h, m, s, ms=0):
    return int(datetime(2026, 10, 16, h, m, s, ms * 1000, tzinfo=TW_TZ).timestamp() * 1000)


# (時間, 價, 張, 方向)：開盤 10 張外盤 → 算賣；09:00:05 同秒兩筆內盤 4+4 → 併成 8 張大戶賣；
# 同秒外盤 3 張不夠；09:04:59 外盤 7 張是那根最後一秒，換棒時要算進去；09:05:01 外盤 6+1 同秒併成 7 張。
TICKS = [
    (_ms(9, 0, 2), 100.0, 10, "buy"),
    (_ms(9, 0, 5, 100), 100.0, 4, "sell"),
    (_ms(9, 0, 5, 900), 100.0, 4, "sell"),
    (_ms(9, 0, 5, 950), 100.0, 3, "buy"),
    (_ms(9, 0, 6), 100.0, 6, "buy"),
    (_ms(9, 4, 59, 500), 100.0, 7, "buy"),
    (_ms(9, 5, 1, 100), 100.0, 6, "buy"),
    (_ms(9, 5, 1, 200), 100.0, 1, "buy"),
]


def _feed(agg: BarAggregator, ticks=TICKS, code="2478"):
    with patch("market_data_hub._now_tw", return_value=datetime(2026, 10, 16, 9, 30, tzinfo=TW_TZ)):
        agg._trade_date = "2026-10-16"
        for ts, price, lots, side in ticks:
            agg.on_tick(code, price, lots, ts, side, False, price * lots * 1000)
        return agg.get_bars(code)


def test_live_bars_merge_same_second_and_count_opening_as_sell():
    bars = _feed(BarAggregator())
    assert [b["big_buy_amount"] for b in bars] == [700_000, 700_000]
    assert [b["big_sell_amount"] for b in bars] == [1_000_000 + 800_000, 0]


def test_first_tick_after_0905_is_not_treated_as_opening():
    late = [(_ms(10, 0, 0), 100.0, 10, "buy")]
    bars = _feed(BarAggregator(), late)
    assert bars[0]["big_buy_amount"] == 1_000_000
    assert bars[0]["big_sell_amount"] == 0


def test_futures_aggregator_does_not_track_big_holder():
    bars = _feed(BarAggregator(track_big=False))
    assert sum(b["big_buy_amount"] + b["big_sell_amount"] for b in bars) == 0


def test_historical_ticks_give_same_big_holder_as_live():
    ticks = {
        "ts": [datetime.fromtimestamp(ts / 1000, TW_TZ) for ts, *_ in TICKS],
        "close": [p for _, p, _, _ in TICKS],
        "volume": [v for _, _, v, _ in TICKS],
        "tick_type": [1 if s == "buy" else 2 for *_, s in TICKS],
        "amount": [p * v * 1000 for _, p, v, _ in TICKS],
        "simtrade": [0] * len(TICKS),
    }
    metrics = _historical_tick_metrics(ticks, trade_date="2026-10-16")
    assert sum(row["big_buy_amount"] for row in metrics.values()) == 1_400_000
    assert sum(row["big_sell_amount"] for row in metrics.values()) == 1_800_000
    assert metrics[_ms(9, 0, 0)]["big_sell_amount"] == 1_800_000


def test_store_keeps_big_holder_and_marks_old_rows_unavailable():
    with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DATABASE_PATH", Path(tmp) / "t.db"):
        base = {"main_force_available": True, "main_buy_amount": 0, "main_sell_amount": 0}
        save_main_force_bars("2478", "5m", [
            {**base, "ts": _ms(9, 0, 0), "big_buy_amount": 700_000, "big_sell_amount": 1_800_000},
            {**base, "ts": _ms(9, 5, 0)},
        ])
        # 舊路徑（沒有老師版欄位）再寫一次同一根，不能把已經存的蓋成 0
        save_main_force_bars("2478", "5m", [{**base, "ts": _ms(9, 0, 0)}])
        rows = load_main_force_bars("2478", "5m", trade_date="2026-10-16")
    assert rows[0]["big_buy_amount"] == 700_000 and rows[0]["big_sell_amount"] == 1_800_000
    assert "big_buy_amount" not in rows[1]


def test_summarize_divides_by_five_day_average_and_flags_missing_bars():
    bars = [
        {"ts": _ms(9, 0, 0), "big_buy_amount": 0, "big_sell_amount": 2_000_000},
        {"ts": _ms(9, 5, 0), "big_buy_amount": 500_000, "big_sell_amount": 0},
    ]
    out = summarize(bars, 10_000_000)
    assert out["pct"] == -15.0
    assert out["series"] == [["09:05", -20.0], ["09:10", -15.0]]
    assert out["complete"] is True
    assert summarize(bars[1:], 10_000_000)["complete"] is False
    assert summarize(bars, None)["pct"] is None
    assert out["high"] is None
    live = [{**bars[0], "high": 103.0, "low": 99.5}, {**bars[1], "high": 101.0, "low": 98.0}]
    assert (summarize(live, 10_000_000)["high"], summarize(live, 10_000_000)["low"]) == (103.0, 98.0)


def test_avg_turnover_uses_five_days_before_trade_date():
    big_holder._avg_cache.clear()
    days = [f"2026-10-{d:02d}" for d in (8, 12, 13, 14, 15, 16)]
    rows = [{"ts": d, "high": 11, "low": 9, "close": 10, "volume": 100 * (i + 1)} for i, d in enumerate(days)]
    value = avg_turnover("2478", "2026-10-16", loader=lambda code, limit: rows)
    assert value == 10 * (100 + 200 + 300 + 400 + 500) * 1000 / 5
    big_holder._avg_cache.clear()
    assert avg_turnover("2478", "2026-10-16", loader=lambda code, limit: rows[:3]) is None


def test_backfill_day_writes_big_fields_into_existing_bars_only(monkeypatch):
    import stock_bar_bootstrap
    from types import SimpleNamespace
    from big_holder import backfill_day

    ticks = {
        "ts": [datetime.fromtimestamp(ts / 1000, TW_TZ) for ts, *_ in TICKS],
        "close": [p for _, p, _, _ in TICKS],
        "volume": [v for _, _, v, _ in TICKS],
        "tick_type": [1 if s == "buy" else 2 for *_, s in TICKS],
        "amount": [p * v * 1000 for _, p, v, _ in TICKS],
        "simtrade": [0] * len(TICKS),
    }
    monkeypatch.setattr(stock_bar_bootstrap, "_fetch_historical_ticks", lambda api, contract, date: ticks)
    monkeypatch.setattr(stock_bar_bootstrap, "_resolve_stock_contract", lambda service, code: code)
    service = SimpleNamespace(api=object(), state=SimpleNamespace(logged_in=True))
    with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DATABASE_PATH", Path(tmp) / "t.db"):
        base = {"main_force_available": True, "main_buy_amount": 5, "main_sell_amount": 0}
        save_main_force_bars("2478", "5m", [{**base, "ts": _ms(9, 0, 0)}])   # 只有 09:00 那根，09:05 那根沒存
        out = backfill_day(service, ["2478"], "2026-10-16")
        rows = load_main_force_bars("2478", "5m", trade_date="2026-10-16")
    assert out["updated5m"] == {"2478": 1}
    assert len(rows) == 1
    assert rows[0]["big_buy_amount"] == 700_000 and rows[0]["big_sell_amount"] == 1_800_000
    assert rows[0]["main_buy_amount"] == 5


def test_ranking_and_timeline_switch_to_teacher_formula_when_day_has_big_fields(monkeypatch):
    from main_force_store import load_holder_timeline, load_main_force_ranking

    monkeypatch.setattr(big_holder, "avg_turnover", lambda code, date, loader=None: 100_000_000)
    with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DATABASE_PATH", Path(tmp) / "t.db"):
        database.initialize_database()
        base = {"main_force_available": True, "main_buy_volume": 600, "main_buy_amount": 60_000_000, "main_sell_amount": 0, "total_amount": 300_000_000}
        # 老師版：大戶淨額 -1,200 萬 → -12%（÷ 近 5 日均成交值 1 億）；舊算法這檔是 +20%、淨量 +600 張
        save_main_force_bars("2478", "5m", [
            {**base, "ts": _ms(9, 0, 0), "big_buy_amount": 0, "big_sell_amount": 4_000_000},
            {**base, "ts": _ms(9, 5, 0), "main_buy_volume": 0, "main_buy_amount": 0, "big_buy_amount": 0, "big_sell_amount": 8_000_000},
        ])
        # 沒有老師版欄位的（這功能上線前的資料）照舊算法
        save_main_force_bars("1303", "5m", [{**base, "ts": _ms(9, 0, 0)}])
        ranking = {r["code"]: r for r in load_main_force_ranking("2026-10-16", codes=["2478", "1303"])}
        timeline = load_holder_timeline("2026-10-16", codes=["2478", "1303"])
    assert ranking["2478"]["holderAlgo"] == "teacher" and ranking["2478"]["strengthPct"] == -12.0
    assert ranking["2478"]["netAmount"] == -12_000_000 and ranking["2478"]["avgAmount"] == 100_000_000
    assert ranking["1303"]["holderAlgo"] == "single20" and ranking["1303"]["strengthPct"] == 20.0
    assert "bull" not in timeline["2478"] and timeline["2478"]["bear"]["firstTs"] == _ms(9, 5, 0)
    assert timeline["2478"]["bear"]["still"] is True and timeline["2478"]["bear"]["peakPct"] == -12.0
    assert timeline["1303"]["bull"]["firstTs"] == _ms(9, 0, 0)
