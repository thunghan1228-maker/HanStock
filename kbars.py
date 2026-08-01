from market_data import get_one_minute_bars
from shioaji_client import shioaji_session


with shioaji_session() as api:
    bars = get_one_minute_bars(api, "2330")

    print("＝＝＝＝ 2330 最近10筆一分鐘K線 ＝＝＝＝")

    for bar in bars[-10:]:
        print(
            f"{bar['time']:%Y-%m-%d %H:%M}｜"
            f"開 {bar['open']}｜"
            f"高 {bar['high']}｜"
            f"低 {bar['low']}｜"
            f"收 {bar['close']}｜"
            f"量 {bar['volume']}"
        )