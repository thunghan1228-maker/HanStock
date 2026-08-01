from datetime import date, datetime, timezone
from typing import Any


def get_stock_contract(api: Any, stock_code: str):
    """取得股票合約。"""
    contract = api.Contracts.Stocks[stock_code]

    if contract is None:
        raise RuntimeError(f"找不到股票代號 {stock_code}。")

    return contract


def get_snapshot(api: Any, stock_code: str):
    """取得單一股票行情快照。"""
    contract = get_stock_contract(api, stock_code)
    snapshots = api.snapshots([contract])

    if not snapshots:
        raise RuntimeError(f"沒有取得 {stock_code} 的行情資料。")

    return snapshots[0]


def get_one_minute_bars(
    api: Any,
    stock_code: str,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """取得一分鐘K線並轉成統一格式。"""
    contract = get_stock_contract(api, stock_code)

    parameters: dict[str, Any] = {
        "contract": contract,
    }

    if start is not None:
        parameters["start"] = start

    if end is not None:
        parameters["end"] = end

    kbars = api.kbars(**parameters)

    if not kbars.ts:
        raise RuntimeError(f"沒有取得 {stock_code} 的K線資料。")

    bars = []

    for i in range(len(kbars.ts)):
        bar_time = datetime.fromtimestamp(
            kbars.ts[i] / 1_000_000_000,
            tz=timezone.utc,
        )

        bars.append(
            {
                "time": bar_time,
                "open": kbars.Open[i],
                "high": kbars.High[i],
                "low": kbars.Low[i],
                "close": kbars.Close[i],
                "volume": kbars.Volume[i],
            }
        )

    return bars


def convert_to_five_minute_bars(
    one_minute_bars: list[dict],
) -> list[dict]:
    """將一分鐘K線合成五分鐘K線。"""
    grouped_bars: dict[datetime, dict] = {}

    for bar in one_minute_bars:
        bar_time = bar["time"]
        five_minute = (bar_time.minute // 5) * 5

        bucket_time = bar_time.replace(
            minute=five_minute,
            second=0,
            microsecond=0,
        )

        if bucket_time not in grouped_bars:
            grouped_bars[bucket_time] = {
                "time": bucket_time,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            }
        else:
            grouped = grouped_bars[bucket_time]
            grouped["high"] = max(grouped["high"], bar["high"])
            grouped["low"] = min(grouped["low"], bar["low"])
            grouped["close"] = bar["close"]
            grouped["volume"] += bar["volume"]

    return list(grouped_bars.values())


def convert_to_daily_bars(
    one_minute_bars: list[dict],
) -> list[dict]:
    """將一分鐘K線合成日K線。"""
    grouped_bars: dict[date, dict] = {}

    for bar in one_minute_bars:
        bar_time = bar["time"]
        trading_day = bar_time.date()

        day_time = bar_time.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

        if trading_day not in grouped_bars:
            grouped_bars[trading_day] = {
                "time": day_time,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            }
        else:
            grouped = grouped_bars[trading_day]
            grouped["high"] = max(grouped["high"], bar["high"])
            grouped["low"] = min(grouped["low"], bar["low"])
            grouped["close"] = bar["close"]
            grouped["volume"] += bar["volume"]

    return list(grouped_bars.values())