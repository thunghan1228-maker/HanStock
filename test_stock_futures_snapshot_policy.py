from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import stock_futures_service as service
import stock_futures_snapshot_policy as policy

TW = timezone(timedelta(hours=8))
UTC = timezone.utc


def ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def test_standard_epoch_keeps_taipei_day_session_time():
    # 正常 Unix epoch：13:44 +08 -> 05:44 UTC，轉回台北應保持 13:44。
    snap = SimpleNamespace(ts=ns(datetime(2026, 8, 7, 13, 44, tzinfo=TW)))
    got = policy.normalized_snapshot_datetime(snap)
    assert got.isoformat().startswith("2026-08-07T13:44:00")


def test_wall_clock_encoded_as_utc_does_not_add_another_eight_hours():
    # 生產 Snapshot 觀察到的型態：13:44 台灣牆鐘被包成 13:44 UTC epoch。
    # 舊程式會顯示 21:44；policy 應還原成 13:44 +08。
    snap = SimpleNamespace(ts=ns(datetime(2026, 8, 7, 13, 44, tzinfo=UTC)))
    got = policy.normalized_snapshot_datetime(snap)
    assert got.isoformat().startswith("2026-08-07T13:44:00")


def test_invalid_ts_fallback_uses_previous_weekday_on_weekend():
    sunday = datetime(2026, 8, 9, 12, 0, tzinfo=TW)
    got = policy._recent_session_fallback(sunday)
    assert got.isoformat().startswith("2026-08-07T13:45:00")


def test_weekend_non_session_timestamp_is_not_kept_as_market_time():
    # 生產曾看到 2330/2303 解碼後落在週六 04:59/12:59；兩種都不是合法日盤。
    snap = SimpleNamespace(ts=ns(datetime(2026, 8, 8, 4, 59, tzinfo=UTC)))
    got = policy.normalized_snapshot_datetime(snap)
    assert got.weekday() < 5
    assert (got.hour, got.minute) == (13, 45)


def test_policy_is_installed_on_stock_futures_service():
    assert service._snapshot_datetime is policy.normalized_snapshot_datetime
