from __future__ import annotations

import ma_alignment_score as module


def test_perfect_bullish_alignment_scores_full_marks(monkeypatch):
    closes = [{"close": 100.0 + i * 0.5} for i in range(240)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=260: closes[-limit:])
    assert module.compute_ma_alignment_score("2330") == 15


def test_bearish_alignment_scores_zero(monkeypatch):
    closes = [{"close": 300.0 - i * 0.5} for i in range(240)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=260: closes[-limit:])
    assert module.compute_ma_alignment_score("2330") == 0


def test_insufficient_local_history_without_finmind_token_returns_none(monkeypatch):
    closes = [{"close": 100.0 + i * 0.5} for i in range(50)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=260: closes[-limit:])
    monkeypatch.setattr(module, "_token", lambda: "")
    assert module.compute_ma_alignment_score("2330") is None


def test_insufficient_local_history_falls_back_to_finmind(monkeypatch):
    closes = [{"close": 100.0 + i * 0.5} for i in range(50)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=260: closes[-limit:])
    monkeypatch.setattr(module, "_token", lambda: "dummy-token")

    def fake_fetcher(url, params):
        assert params["dataset"] == "TaiwanStockPrice"
        assert params["data_id"] == "2330"
        return {"data": [
            {"date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "close": 100.0 + i * 0.5}
            for i in range(240)
        ]}

    score = module.compute_ma_alignment_score("2330", fetcher=fake_fetcher)
    assert score == 15


def test_finmind_fetch_failure_is_caught_and_treated_as_no_data(monkeypatch):
    closes = [{"close": 100.0} for _ in range(50)]
    monkeypatch.setattr(module, "load_daily_bars", lambda code, limit=260: closes[-limit:])
    monkeypatch.setattr(module, "_token", lambda: "dummy-token")

    def failing_fetcher(url, params):
        raise RuntimeError("網路逾時")

    assert module.compute_ma_alignment_score("2330", fetcher=failing_fetcher) is None
