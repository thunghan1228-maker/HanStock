from datetime import datetime, timedelta

from triangle_screener import evaluate_triangle


def _bars(count=50, breakout=False, volume_ratio=1.0):
    start = datetime(2026, 1, 1)
    rows = []
    for index in range(count):
        upper = 120 - index * 0.30
        lower = 80 + index * 0.30
        phase = (index % 8) / 7
        close = lower + (upper - lower) * (0.18 + 0.64 * phase)
        rows.append({
            "bar_time": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.2,
            "high": min(upper, close + 0.8),
            "low": max(lower, close - 0.8),
            "close": close,
            "volume": 1000,
        })
    # 製造可辨識、逐步下降與上升的轉折。
    for index in (8, 20, 32, 44):
        rows[index]["high"] = 120 - index * 0.30
    for index in (12, 24, 36, 45):
        rows[index]["low"] = 80 + index * 0.30
    if breakout:
        upper = 120 - (count - 1) * 0.30
        rows[-1]["close"] = upper * 1.02
        rows[-1]["high"] = rows[-1]["close"] * 1.01
        rows[-1]["volume"] = int(1000 * volume_ratio)
    return rows


def test_detects_symmetric_triangle():
    result = evaluate_triangle("TEST", _bars())
    assert result["passed"] is True
    assert result["conditions"]["上緣下降"] is True
    assert result["conditions"]["下緣上升"] is True


def test_marks_volume_breakout():
    result = evaluate_triangle("TEST", _bars(breakout=True, volume_ratio=2.0))
    assert result["passed"] is True
    assert result["status"] == "放量突破"


def test_rejects_flat_channel():
    bars = _bars()
    for index, row in enumerate(bars):
        row["high"] = 110 + (1 if index % 8 == 0 else 0)
        row["low"] = 90 - (1 if index % 8 == 4 else 0)
        row["close"] = 100
    result = evaluate_triangle("TEST", bars)
    assert result["passed"] is False
