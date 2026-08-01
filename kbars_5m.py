from market_data import (
    convert_to_five_minute_bars,
    get_one_minute_bars,
)
from shioaji_client import shioaji_session


with shioaji_session() as api:
    one_minute_bars = get_one_minute_bars(api, "2330")
    five_minute_bars = convert_to_five_minute_bars(one_minute_bars)

    print("＝＝＝＝ 2330 最近10筆五分鐘K線 ＝＝＝＝")

    for bar in five_minute_bars[-10:]:
        print(
            f"{bar['time']:%Y-%m-%d %H:%M}｜"
            f"開 {bar['open']}｜"
            f"高 {bar['high']}｜"
            f"低 {bar['low']}｜"
            f"收 {bar['close']}｜"
            f"量 {bar['volume']}"
        )