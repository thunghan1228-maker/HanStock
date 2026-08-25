from datetime import datetime

import triangle_daily_collector as collector


def _reset_status():
    collector._status.update(
        status="not_started",
        targetDate=None,
        lastAttemptAt=None,
        lastSuccessAt=None,
        insertedBars=0,
        matchedCount=0,
        vcpMatchedCount=0,
        twseRowCount=0,
        tpexRowCount=0,
        error=None,
    )


def _row(code):
    return {"stock_code": code}


def test_waits_and_does_not_scan_when_tpex_raises():
    _reset_status()
    saved = []
    scanned = []

    result = collector.collect_once(
        now=datetime(2026, 8, 21, 15, 0, tzinfo=collector.TW_TZ),
        twse_loader=lambda _: [_row("2330")],
        tpex_loader=lambda _: (_ for _ in ()).throw(RuntimeError("temporary failure")),
        save_day=lambda rows: saved.append(rows) or len(rows),
        scanner=lambda: scanned.append(True) or {"summary": {"matched_count": 1}},
    )

    assert result["status"] == "waiting_official_data"
    assert result["twseRowCount"] == 1
    assert result["tpexRowCount"] == 0
    assert "櫃買" in result["error"]
    assert saved == []
    assert scanned == []


def test_waits_and_does_not_scan_when_tpex_is_empty():
    _reset_status()

    result = collector.collect_once(
        now=datetime(2026, 8, 21, 15, 0, tzinfo=collector.TW_TZ),
        twse_loader=lambda _: [_row("2330")],
        tpex_loader=lambda _: [],
        save_day=lambda rows: len(rows),
        scanner=lambda: {"summary": {"matched_count": 1}},
    )

    assert result["status"] == "waiting_official_data"
    assert result["twseRowCount"] == 1
    assert result["tpexRowCount"] == 0


def test_completes_only_after_both_markets_are_available():
    _reset_status()
    saved = []

    result = collector.collect_once(
        now=datetime(2026, 8, 21, 15, 0, tzinfo=collector.TW_TZ),
        twse_loader=lambda _: [_row("2330")],
        tpex_loader=lambda _: [_row("1815")],
        save_day=lambda rows: saved.extend(rows) or len(rows),
        scanner=lambda: {"summary": {"matched_count": 7}},
    )

    assert result["status"] == "completed"
    assert result["insertedBars"] == 2
    assert result["matchedCount"] == 7
    assert result["twseRowCount"] == 1
    assert result["tpexRowCount"] == 1
    assert [row["stock_code"] for row in saved] == ["2330", "1815"]
