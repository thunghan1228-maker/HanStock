"""HanStock 股票期貨休市 Snapshot 時間正規化 policy。

Shioaji Snapshot 的 ``ts`` 在不同商品/版本可能出現兩種語意：
1. 正常 Unix epoch（轉成 Asia/Taipei 後即為 08:45~13:45）；
2. 台灣本地牆鐘時間被包成 UTC epoch（若再轉 +08 會變成 17:xx~21:xx）。

本 policy 只修休市 Snapshot 的市場時間，不修改盤中 QuoteFOPv1 即時行情。
若 ts 缺失/無效，不再用「現在時間」冒充成交時間；改用最近可能的日盤日期 13:45。
網站端仍會以整個族群的主流交易日做第二層驗證，因此遇到交易所休假日也不會
讓少數 fallback 日期混入正式排名。
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Optional

import stock_futures_service as service

TW_TZ = timezone(timedelta(hours=8))
UTC = timezone.utc


def _in_stock_futures_session(dt: datetime) -> bool:
    local = dt.astimezone(TW_TZ)
    if local.weekday() >= 5:
        return False
    clock = local.time().replace(tzinfo=None)
    return dt_time(8, 45) <= clock <= dt_time(13, 45)


def _coerce_epoch_seconds(raw: Any) -> Optional[float]:
    try:
        ts = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if ts <= 0:
        return None
    # Shioaji Snapshot 常見 nanoseconds；兼容 microseconds / milliseconds / seconds。
    if ts > 1e17:
        ts /= 1e9
    elif ts > 1e14:
        ts /= 1e6
    elif ts > 1e11:
        ts /= 1e3
    if ts <= 0:
        return None
    return ts


def _recent_session_fallback(now: Optional[datetime] = None) -> datetime:
    """ts 無效時給出最近可能的日盤日期；網站端還會再做族群日期一致性檢查。"""
    current = (now or datetime.now(TW_TZ)).astimezone(TW_TZ)
    candidate = current
    clock = current.time().replace(tzinfo=None)
    if current.weekday() >= 5 or clock < dt_time(8, 45):
        candidate = current - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.replace(hour=13, minute=45, second=0, microsecond=0)


def normalized_snapshot_datetime(snapshot: Any) -> datetime:
    raw = getattr(snapshot, "ts", None)
    ts = _coerce_epoch_seconds(raw)
    if ts is None:
        return _recent_session_fallback()

    try:
        # A: 標準 Unix epoch -> 轉 Asia/Taipei。
        converted = datetime.fromtimestamp(ts, TW_TZ)
        # B: 部分 Snapshot 的 ts 實際已是台灣牆鐘值；先按 UTC 解碼，再只重貼 +08 tz。
        wall_clock = datetime.fromtimestamp(ts, UTC).replace(tzinfo=TW_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return _recent_session_fallback()

    converted_ok = _in_stock_futures_session(converted)
    wall_ok = _in_stock_futures_session(wall_clock)
    if converted_ok and not wall_ok:
        return converted
    if wall_ok and not converted_ok:
        return wall_clock
    if converted_ok and wall_ok:
        # 兩者都合理時保留標準 epoch 語意。
        return converted

    # 兩者都不在日盤時，不把「查詢現在時間」當成市場時間。
    # 優先保留牆鐘解碼的原始日期，讓網站端主流交易日規則判斷是否應納入。
    return wall_clock


def install() -> None:
    if getattr(service, "_hanstock_snapshot_time_policy_v1", False):
        return
    service._snapshot_datetime = normalized_snapshot_datetime
    service._hanstock_snapshot_time_policy_v1 = True


install()
