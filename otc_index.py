"""HanStock 櫃買指數（TPEx）行情工具。

本模組刻意不 import shioaji，讓合約名稱判斷、timestamp 正規化與
1 分 K → 5 分 K 聚合可以獨立測試。
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

TW_TZ = timezone(timedelta(hours=8))
OTC_INDEX_HUB_CODE = "OTC_INDEX"
OTC_INDEX_DISPLAY_NAME = "上櫃指數"
OTC_INDEX_OFFICIAL_NAME = "櫃檯買賣發行量加權股價指數"
ONE_MIN_MS = 60_000
FIVE_MIN_MS = 5 * ONE_MIN_MS


def exchange_text(value: Any) -> str:
    """把 Shioaji Exchange enum / 字串正規化成 OTC/TSE 等文字。"""
    raw = getattr(value, "value", None) or str(value)
    return raw.split(".")[-1].strip().upper()


def index_name_score(name: str, exchange: Any = "OTC") -> int:
    """評估一個 IndexInfo 是否為「櫃買發行量加權指數」。

    不硬編碼 1.7 的交易所新代碼；登入後列出 IND 合約並以 exchange + name
    找到正確指數。分數 <= 0 代表不可接受。
    """
    if exchange_text(exchange) != "OTC":
        return -10_000

    text = str(name or "").strip()
    compact = text.replace(" ", "")
    if not compact:
        return 0

    if OTC_INDEX_OFFICIAL_NAME in compact:
        return 10_000
    if "櫃檯買賣" in compact and "發行量加權股價指數" in compact:
        return 9_500
    if "櫃買指數" == compact:
        return 9_000
    if "櫃買" in compact and "發行量加權" in compact and "指數" in compact:
        return 8_500
    if "發行量加權股價指數" in compact:
        return 8_000

    # 避免誤選「櫃買薪酬指數」「櫃買半導體指數」等主題/產業指數。
    return 0


def timestamp_to_ms(value: Any) -> Optional[int]:
    """把 Shioaji / Python / numpy 常見 timestamp 轉成 Unix ms。

    Shioaji KBars 的 ts 在不同介面可能是 datetime、秒、毫秒、微秒或奈秒整數。
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return int(dt.timestamp() * 1000)

    # datetime.date 但不是 datetime：視為台北當日 00:00。
    if isinstance(value, date):
        dt = datetime.combine(value, dt_time.min, tzinfo=TW_TZ)
        return int(dt.timestamp() * 1000)

    # numpy.datetime64 等通常可轉 int（多半是 ns）。
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return int(dt.timestamp() * 1000)

    magnitude = abs(number)
    if magnitude >= 100_000_000_000_000_000:  # ns
        return number // 1_000_000
    if magnitude >= 100_000_000_000_000:      # us
        return number // 1_000
    if magnitude >= 100_000_000_000:          # ms
        return number
    if magnitude >= 1_000_000_000:            # seconds
        return number * 1000
    return None


def taipei_trade_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, TW_TZ).strftime("%Y-%m-%d")


def taipei_minute_of_day(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000, TW_TZ)
    return dt.hour * 60 + dt.minute


def is_regular_otc_session(ts_ms: int) -> bool:
    minute = taipei_minute_of_day(ts_ms)
    return 9 * 60 <= minute < 13 * 60 + 30


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result:  # NaN
        return None
    return result


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def normalize_kbars_1m(
    kbars: Any,
    *,
    trade_date: Optional[str] = None,
    include_current: bool = False,
    now_ms: Optional[int] = None,
) -> list[dict[str, Any]]:
    """把 Shioaji KBars 物件正規化成正式交易時段 1 分 K。

    支援物件屬性（kbars.ts / Open ...）或 Mapping。
    """
    if kbars is None:
        return []

    def get_field(*names: str) -> list[Any]:
        for name in names:
            value = kbars.get(name) if isinstance(kbars, Mapping) else getattr(kbars, name, None)
            if value is not None:
                try:
                    return list(value)
                except TypeError:
                    return []
        return []

    timestamps = get_field("ts", "datetime")
    opens = get_field("Open", "open")
    highs = get_field("High", "high")
    lows = get_field("Low", "low")
    closes = get_field("Close", "close")
    volumes = get_field("Volume", "volume")

    size = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
    if size <= 0:
        return []

    now_value = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    current_minute_start = now_value - (now_value % ONE_MIN_MS)
    rows: dict[int, dict[str, Any]] = {}

    for i in range(size):
        ts_ms = timestamp_to_ms(timestamps[i])
        if ts_ms is None or not is_regular_otc_session(ts_ms):
            continue
        if trade_date and taipei_trade_date(ts_ms) != trade_date:
            continue

        minute_ts = ts_ms - (ts_ms % ONE_MIN_MS)
        if not include_current and minute_ts >= current_minute_start:
            continue

        open_ = _safe_float(opens[i])
        high = _safe_float(highs[i])
        low = _safe_float(lows[i])
        close = _safe_float(closes[i])
        if None in (open_, high, low, close):
            continue
        if min(open_, high, low, close) <= 0:
            continue

        volume = _safe_int(volumes[i] if i < len(volumes) else 0)
        rows[minute_ts] = {
            "ts": minute_ts,
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": volume,
            "tick_count": 1,
        }

    return [rows[key] for key in sorted(rows)]


def aggregate_1m_to_5m(
    bars_1m: Iterable[Mapping[str, Any]],
    *,
    include_current: bool = False,
    now_ms: Optional[int] = None,
) -> list[dict[str, Any]]:
    """將正式 1 分 K 聚合成 5 分 K。"""
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for raw in bars_1m:
        try:
            ts = int(raw["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if not is_regular_otc_session(ts):
            continue
        bucket = ts - (ts % FIVE_MIN_MS)
        grouped.setdefault(bucket, []).append(raw)

    now_value = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    current_5m_start = now_value - (now_value % FIVE_MIN_MS)
    result: list[dict[str, Any]] = []

    for bucket in sorted(grouped):
        if not include_current and bucket >= current_5m_start:
            continue
        rows = sorted(grouped[bucket], key=lambda item: int(item["ts"]))
        if not rows:
            continue
        opens = _safe_float(rows[0].get("open"))
        closes = _safe_float(rows[-1].get("close"))
        highs = [_safe_float(row.get("high")) for row in rows]
        lows = [_safe_float(row.get("low")) for row in rows]
        if opens is None or closes is None or any(v is None for v in highs) or any(v is None for v in lows):
            continue

        result.append({
            "ts": bucket,
            "open": opens,
            "high": max(v for v in highs if v is not None),
            "low": min(v for v in lows if v is not None),
            "close": closes,
            "volume": sum(_safe_int(row.get("volume")) for row in rows),
            "tick_count": sum(max(1, _safe_int(row.get("tick_count"))) for row in rows),
        })

    return result
