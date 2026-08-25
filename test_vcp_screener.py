from datetime import datetime, timedelta

from vcp_screener import evaluate_vcp


def _vcp_bars(breakout=False, volume_ratio=0.55):
    rows = []
    start = datetime(2026, 1, 1)
    for index in range(100):
        trend = 70 + index * 0.30
        rows.append({"bar_time": (start + timedelta(days=index)).isoformat(), "open": trend, "high": trend + 1, "low": trend - 1, "close": trend, "volume": 2000 if index < 50 else 1100})
    pivot = 105.0
    rows[55].update(high=pivot, close=103.0)
    for index, depth in ((62, 18), (74, 11), (85, 6)):
        rows[index].update(low=pivot * (1 - depth / 100), close=pivot * (1 - depth / 100) + 1)
        rows[index - 2]["low"] = rows[index]["low"] + 3
        rows[index - 1]["low"] = rows[index]["low"] + 2
        rows[index + 1]["low"] = rows[index]["low"] + 2
        rows[index + 2]["low"] = rows[index]["low"] + 3
    for index in range(56, 100):
        rows[index]["high"] = min(rows[index]["high"], 104.5)
        rows[index]["close"] = min(rows[index]["close"], 103.5)
        if index >= 75:
            rows[index]["volume"] = 650
    rows[-1].update(close=106.0 if breakout else 102.0, high=107.0 if breakout else 103.0, low=101.0, volume=3000 if breakout else int(1100 * volume_ratio))
    return rows


def test_detects_vcp_contractions():
    result = evaluate_vcp("TEST", _vcp_bars())
    assert result["passed"] is True
    assert result["status"] in {"VCP形成中", "接近突破"}
    assert len(result["contractions_pct"]) >= 2


def test_marks_volume_breakout():
    result = evaluate_vcp("TEST", _vcp_bars(breakout=True))
    assert result["passed"] is True
    assert result["status"] == "今日帶量突破"


def test_rejects_non_shrinking_base():
    bars = _vcp_bars()
    for index in (62, 74, 85):
        bars[index]["low"] = 82.0
        bars[index - 2]["low"] = 85.0
        bars[index - 1]["low"] = 84.0
        bars[index + 1]["low"] = 84.0
        bars[index + 2]["low"] = 85.0
    assert evaluate_vcp("TEST", bars)["passed"] is False
