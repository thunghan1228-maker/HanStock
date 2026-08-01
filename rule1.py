from database import get_connection


STOCK_CODE = "2330"


def load_daily_bars(
    stock_code: str,
    limit: int = 30,
) -> list[dict]:
    """從資料庫讀取最近的日K資料。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                bar_time,
                open,
                high,
                low,
                close,
                volume
            FROM bars_1d
            WHERE stock_code = ?
            ORDER BY bar_time DESC
            LIMIT ?
            """,
            (
                stock_code,
                limit,
            ),
        ).fetchall()

    bars = [dict(row) for row in reversed(rows)]

    return bars


def average(values: list[float]) -> float:
    """計算平均值。"""
    return sum(values) / len(values)


def evaluate_rule1(
    daily_bars: list[dict],
) -> dict:
    """執行 Rule1 日線多方策略判斷。"""
    if len(daily_bars) < 11:
        raise RuntimeError(
            "日K資料不足，至少需要11個交易日。"
        )

    closes = [
        float(bar["close"])
        for bar in daily_bars
    ]

    today = daily_bars[-1]
    yesterday = daily_bars[-2]

    today_close = float(today["close"])
    yesterday_close = float(yesterday["close"])

    # 今天的五日均線
    ma5_today = average(closes[-5:])

    # 昨天的五日均線
    ma5_yesterday = average(closes[-6:-1])

    # 以昨天為結尾的近10日收盤價
    yesterday_ten_day_closes = closes[-11:-1]
    ten_day_high = max(yesterday_ten_day_closes)

    conditions = {
        "五日均線向上": ma5_today > ma5_yesterday,
        "昨天收盤為近10日新高": (
            yesterday_close >= ten_day_high
        ),
        "今天收盤大於五日均線": (
            today_close > ma5_today
        ),
        "今天收盤小於昨天收盤": (
            today_close < yesterday_close
        ),
    }

    return {
        "stock_code": STOCK_CODE,
        "today": today,
        "yesterday": yesterday,
        "today_close": today_close,
        "yesterday_close": yesterday_close,
        "ma5_today": ma5_today,
        "ma5_yesterday": ma5_yesterday,
        "ten_day_high": ten_day_high,
        "conditions": conditions,
        "passed": all(conditions.values()),
    }


daily_bars = load_daily_bars(STOCK_CODE)
result = evaluate_rule1(daily_bars)

print("＝＝＝＝ Rule1 日線策略測試 ＝＝＝＝")
print(f"股票代號：{result['stock_code']}")
print(f"今天收盤：{result['today_close']}")
print(f"昨天收盤：{result['yesterday_close']}")
print(f"今天五日均線：{result['ma5_today']:.2f}")
print(f"昨天五日均線：{result['ma5_yesterday']:.2f}")
print(f"昨天以前近10日最高收盤：{result['ten_day_high']}")

print()
print("＝＝＝＝ 條件判斷 ＝＝＝＝")

for condition_name, passed in result["conditions"].items():
    symbol = "✅" if passed else "❌"
    print(f"{symbol} {condition_name}")

print()

if result["passed"]:
    print("🎯 這檔股票符合 Rule1。")
else:
    print("目前不符合 Rule1。")