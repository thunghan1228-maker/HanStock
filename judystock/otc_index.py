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
    if compact in {"櫃買指數", "上櫃指數"}:
        return 9_000
    if "櫃買" in compact and "發行量加權" in compact and "指數" in compact:
        return 8_500
    if "發行量加權股價指數" in compact:
        return 8_000

    # 避免誤選「櫃買薪酬指數」「櫃買半導體指數」等主題/產業指數。
    return 0


def _numeric_time_to_ms(number: int) -> Optional[int]:
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


def timestamp_to_ms(value: Any) -> Optional[int]:
    """把一般 datetime / Unix timestamp 轉成真正 UTC epoch ms。

    此函式給即時 Quote 使用；Shioaji 歷史 Kbars 的 ts 是「台灣本地牆鐘時間」
    的奈秒值，必須改用 shioaji_kbar_close_to_start_ms()。
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return int(dt.timestamp() * 1000)

    if isinstance(value, date):
        dt = datetime.combine(value, dt_time.min, tzinfo=TW_TZ)
        return int(dt.timestamp() * 1000)

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

    return _numeric_time_to_ms(number)


def shioaji_kbar_close_to_start_ms(value: Any) -> Optional[int]:
    """把 Shioaji Python KBars.ts 轉為 HanStock 的「1 分 K 起始時間」。

    Shioaji 官方 1.7 範例：1779094860000000000 以 Datetime(ns) 顯示為
    2026-05-18 09:01:00；這不是 UTC 17:01，而是台灣市場的本地牆鐘時間，
    且 09:01 是 09:00~09:01 這根 K 的收棒標記。

    因此：
      1. 數值型 ts 先當成「無時區的台灣牆鐘時間」解讀。
      2. 再減 1 分鐘，統一成 HanStock 所用的 bar-start timestamp。
    ISO/datetime 輸入同樣視為 Kbar close time，再減 1 分鐘。
    """
    if value is None:
        return None

    close_ms: Optional[int]
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        close_ms = int(dt.timestamp() * 1000)
    elif isinstance(value, date):
        dt = datetime.combine(value, dt_time.min, tzinfo=TW_TZ)
        close_ms = int(dt.timestamp() * 1000)
    else:
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
            close_ms = int(dt.timestamp() * 1000)
        else:
            wall_ms = _numeric_time_to_ms(number)
            if wall_ms is None:
                return None
            # 將「Unix epoch 起算的數字」只當作牆鐘年月日時分來解碼，
            # 再把同一組年月日時分指定成 Asia/Taipei (+08:00)。台灣無 DST。
            wall_dt = datetime.fromtimestamp(wall_ms / 1000, timezone.utc).replace(tzinfo=None)
            local_dt = wall_dt.replace(tzinfo=TW_TZ)
            close_ms = int(local_dt.timestamp() * 1000)

    return close_ms - ONE_MIN_MS if close_ms is not None else None


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
    """把 Shioaji KBars 物件正規化成正式交易時段、bar-start 1 分 K。

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
        minute_ts = shioaji_kbar_close_to_start_ms(timestamps[i])
        if minute_ts is None or not is_regular_otc_session(minute_ts):
            continue
        if trade_date and taipei_trade_date(minute_ts) != trade_date:
            continue
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
    """將 bar-start 正式 1 分 K 聚合成 bar-start 5 分 K。"""
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

        aggregated = {
            "ts": bucket,
            "open": opens,
            "high": max(v for v in highs if v is not None),
            "low": min(v for v in lows if v is not None),
            "close": closes,
            "volume": sum(_safe_int(row.get("volume")) for row in rows),
            "tick_count": sum(max(1, _safe_int(row.get("tick_count"))) for row in rows),
        }
        # 個股歷史逐筆回補會在 1 分 K 附加下列欄位；聚合 5 分 K 時必須一併
        # 加總，否則前端只能看到 K 棒而看不到主力進出柱狀體。一般 Kbars／
        # 櫃買指數沒有這些欄位時維持原本 payload，不會誤標為有主力資料。
        if any("main_net_volume" in row for row in rows):
            aggregated.update({
                "buy_volume": sum(_safe_int(row.get("buy_volume")) for row in rows),
                "sell_volume": sum(_safe_int(row.get("sell_volume")) for row in rows),
                "neutral_volume": sum(_safe_int(row.get("neutral_volume")) for row in rows),
                "main_buy_volume": sum(_safe_int(row.get("main_buy_volume")) for row in rows),
                "main_sell_volume": sum(_safe_int(row.get("main_sell_volume")) for row in rows),
                "main_buy_amount": round(sum(float(row.get("main_buy_amount") or 0) for row in rows)),
                "main_sell_amount": round(sum(float(row.get("main_sell_amount") or 0) for row in rows)),
                "main_tick_count": sum(_safe_int(row.get("main_tick_count")) for row in rows),
                "main_force_available": any(bool(row.get("main_force_available")) for row in rows),
            })
            aggregated["main_net_volume"] = (
                aggregated["main_buy_volume"] - aggregated["main_sell_volume"]
            )
            aggregated["main_net_amount"] = (
                aggregated["main_buy_amount"] - aggregated["main_sell_amount"]
            )
        result.append(aggregated)

    return result
