from datetime import datetime, timezone

from shioaji_client import shioaji_session


with shioaji_session() as api:
    contract = api.Contracts.Stocks["2330"]

    if contract is None:
        raise RuntimeError("找不到股票代號 2330。")

    kbars = api.kbars(contract=contract)

    if not kbars.ts:
        raise RuntimeError("沒有取得一分鐘K線資料。")

    bars_5m = {}

    for i in range(len(kbars.ts)):
        dt = datetime.fromtimestamp(
            kbars.ts[i] / 1_000_000_000,
            tz=timezone.utc,
        )

        five_minute = (dt.minute // 5) * 5
        bucket_time = dt.replace(
            minute=five_minute,
            second=0,
            microsecond=0,
        )

        if bucket_time not in bars_5m:
            bars_5m[bucket_time] = {
                "open": kbars.Open[i],
                "high": kbars.High[i],
                "low": kbars.Low[i],
                "close": kbars.Close[i],
                "volume": kbars.Volume[i],
            }
        else:
            bar = bars_5m[bucket_time]
            bar["high"] = max(bar["high"], kbars.High[i])
            bar["low"] = min(bar["low"], kbars.Low[i])
            bar["close"] = kbars.Close[i]
            bar["volume"] += kbars.Volume[i]

    recent_bars = list(bars_5m.items())[-10:]

    print("＝＝＝＝ 2330 最近10筆五分鐘K線 ＝＝＝＝")

    for dt, bar in recent_bars:
        print(
            f"{dt:%Y-%m-%d %H:%M}｜"
            f"開 {bar['open']}｜"
            f"高 {bar['high']}｜"
            f"低 {bar['low']}｜"
            f"收 {bar['close']}｜"
            f"量 {bar['volume']}"
        )