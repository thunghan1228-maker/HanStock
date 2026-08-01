import sys

from database import get_connection


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

    return [dict(row) for row in reversed(rows)]


def get_stock_name(stock_code: str) -> str:
    """從資料庫取得股票名稱。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT stock_name
            FROM stocks
            WHERE stock_code = ?
            """,
            (stock_code,),
        ).fetchone()

    if row is None:
        return "名稱尚未建立"

    return row["stock_name"]


def average(values: list[float]) -> float:
    """計算平均值。"""
    return sum(values) / len(values)


def evaluate_rule1(
    stock_code: str,
    daily_bars: list[dict],
) -> dict:
    """執行 Rule1 日線多方策略判斷。"""
    if len(daily_bars) < 11:
        raise RuntimeError(
            f"{stock_code} 日K資料不足，"
            f"目前只有 {len(daily_bars)} 個交易日，至少需要11日。"
        )

    closes = [
        float(bar["close"])
        for bar in daily_bars
    ]

    today = daily_bars[-1]
    yesterday = daily_bars[-2]

    today_close = float(today["close"])
    yesterday_close = float(yesterday["close"])

    ma5_today = average(closes[-5:])
    ma5_yesterday = average(closes[-6:-1])

    # 以昨天為最後一天的近10日收盤價
    ten_day_window = closes[-11:-1]
    ten_day_high = max(ten_day_window)

    price_change = today_close - yesterday_close

    if yesterday_close == 0:
        change_rate = 0.0
    else:
        change_rate = (
            price_change / yesterday_close
        ) * 100

    conditions = {
        "五日均線向上": ma5_today > ma5_yesterday,
        "昨天收盤為近10日收盤新高": (
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
        "stock_code": stock_code,
        "today_close": today_close,
        "yesterday_close": yesterday_close,
        "ma5_today": ma5_today,
        "ma5_yesterday": ma5_yesterday,
        "ten_day_high": ten_day_high,
        "price_change": price_change,
        "change_rate": change_rate,
        "conditions": conditions,
        "passed": all(conditions.values()),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("請輸入股票代號。")
        print("使用方式：py rule1.py 2330")
        raise SystemExit(1)

    stock_code = sys.argv[1].strip()
    stock_name = get_stock_name(stock_code)
    daily_bars = load_daily_bars(stock_code)

    if not daily_bars:
        print(f"資料庫目前沒有 {stock_code} 的日K資料。")
        print("請先下載並儲存這檔股票的日K資料。")
        raise SystemExit(1)

    result = evaluate_rule1(
        stock_code=stock_code,
        daily_bars=daily_bars,
    )

    print("＝＝＝＝ Rule1 日線策略測試 ＝＝＝＝")
    print(f"股票：{stock_code} {stock_name}")
    print(f"今天收盤：{result['today_close']}")
    print(f"昨天收盤：{result['yesterday_close']}")
    print(f"漲跌價：{result['price_change']:+.2f}")
    print(f"漲跌幅：{result['change_rate']:+.2f}%")
    print(f"今天五日均線：{result['ma5_today']:.2f}")
    print(f"昨天五日均線：{result['ma5_yesterday']:.2f}")
    print(f"近10日最高收盤：{result['ten_day_high']}")

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


if __name__ == "__main__":
    main()