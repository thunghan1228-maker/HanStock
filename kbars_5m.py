import os
from datetime import datetime, timezone

import shioaji as sj
from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv("SHIOAJI_API_KEY")
secret_key = os.getenv("SHIOAJI_SECRET_KEY")

if not api_key or not secret_key:
    raise RuntimeError("找不到 API Key 或 Secret Key，請檢查 .env。")

api = sj.Shioaji()

try:
    api.login(
        api_key=api_key,
        secret_key=secret_key,
    )

    print("永豐 API 登入成功！")

    contract = api.Contracts.Stocks["2330"]
    kbars = api.kbars(contract=contract)

    if not kbars.ts:
        raise RuntimeError("沒有取得一分鐘K線資料。")

    # 儲存合成後的五分鐘K線
    bars_5m = {}

    for i in range(len(kbars.ts)):
        dt = datetime.fromtimestamp(
            kbars.ts[i] / 1_000_000_000,
            tz=timezone.utc,
        )

        # 將分鐘歸類到 00、05、10、15……
        five_minute = (dt.minute // 5) * 5
        bucket_time = dt.replace(
            minute=five_minute,
            second=0,
            microsecond=0,
        )

        open_price = kbars.Open[i]
        high_price = kbars.High[i]
        low_price = kbars.Low[i]
        close_price = kbars.Close[i]
        volume = kbars.Volume[i]

        if bucket_time not in bars_5m:
            bars_5m[bucket_time] = {
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }
        else:
            bar = bars_5m[bucket_time]
            bar["high"] = max(bar["high"], high_price)
            bar["low"] = min(bar["low"], low_price)
            bar["close"] = close_price
            bar["volume"] += volume

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

except Exception as error:
    print(f"五分鐘K線讀取失敗：{error}")

finally:
    api.logout()
    print("已安全登出。")