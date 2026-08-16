"""日線三角收斂辨識與全市場盤後掃描。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

MIN_BARS = 30
MAX_BARS = 90


def _linear_fit(points: list[tuple[int, float]]) -> tuple[float, float, float]:
    """回傳最小平方法斜率、截距、R²。"""
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
        if denominator
        else 0.0
    )
    intercept = mean_y - slope * mean_x
    total = sum((y - mean_y) ** 2 for _, y in points)
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r_squared = 1.0 - residual / total if total else 1.0
    return slope, intercept, r_squared


def _pivots(values: list[float], radius: int, high: bool) -> list[tuple[int, float]]:
    result: list[tuple[int, float]] = []
    for index in range(radius, len(values) - radius):
        window = values[index - radius:index + radius + 1]
        target = max(window) if high else min(window)
        if values[index] == target and window.count(target) == 1:
            result.append((index, values[index]))
    return result


def evaluate_triangle(
    stock_code: str,
    daily_bars: list[dict[str, Any]],
    *,
    pivot_radius: int = 2,
) -> dict[str, Any]:
    """辨識對稱三角收斂，並判斷接近上緣或放量突破。"""
    bars = daily_bars[-MAX_BARS:]
    if len(bars) < MIN_BARS:
        raise RuntimeError(f"{stock_code} 日K資料不足，至少需要 {MIN_BARS} 日。")

    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    closes = [float(bar["close"]) for bar in bars]
    volumes = [max(0.0, float(bar.get("volume", 0) or 0)) for bar in bars]
    # 最後一根可能正是突破長紅，不可讓它反過來改寫原收斂趨勢線。
    high_points = _pivots(highs[:-1], pivot_radius, high=True)[-4:]
    low_points = _pivots(lows[:-1], pivot_radius, high=False)[-4:]
    if len(high_points) < 2 or len(low_points) < 2:
        return {"stock_code": stock_code, "passed": False, "reason": "轉折點不足"}

    upper_slope, upper_intercept, upper_r2 = _linear_fit(high_points)
    lower_slope, lower_intercept, lower_r2 = _linear_fit(low_points)
    first_x = max(min(x for x, _ in high_points), min(x for x, _ in low_points))
    last_x = len(bars) - 1
    upper_first = upper_slope * first_x + upper_intercept
    lower_first = lower_slope * first_x + lower_intercept
    upper_last = upper_slope * last_x + upper_intercept
    lower_last = lower_slope * last_x + lower_intercept
    initial_width = upper_first - lower_first
    current_width = upper_last - lower_last
    width_ratio = current_width / initial_width if initial_width > 0 else 999.0
    close = closes[-1]
    distance_to_upper_pct = (upper_last - close) / upper_last * 100 if upper_last else 999.0
    avg_volume_20 = sum(volumes[-21:-1]) / min(20, len(volumes) - 1)
    volume_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 else 0.0
    breakout = close > upper_last * 1.005
    near_breakout = -0.5 <= distance_to_upper_pct <= 3.0

    inside_count = 0
    sample_count = 0
    for index in range(first_x, len(bars) - (1 if breakout else 0)):
        upper = upper_slope * index + upper_intercept
        lower = lower_slope * index + lower_intercept
        tolerance = max(close * 0.015, (upper - lower) * 0.15)
        sample_count += 1
        if highs[index] <= upper + tolerance and lows[index] >= lower - tolerance:
            inside_count += 1
    inside_ratio = inside_count / sample_count if sample_count else 0.0

    conditions = {
        "上緣下降": upper_slope < 0,
        "下緣上升": lower_slope > 0,
        "上下緣尚未交叉": current_width > 0,
        "區間明顯收窄": 0 < width_ratio <= 0.75,
        "趨勢線可信": upper_r2 >= 0.28 and lower_r2 >= 0.28,
        "K棒大多位於三角內": inside_ratio >= 0.70,
    }
    passed = all(conditions.values())
    status = "形成中"
    if passed and breakout:
        status = "放量突破" if volume_ratio >= 1.5 else "突破待量"
    elif passed and near_breakout:
        status = "接近突破"

    score = round(100 * (
        0.25 * max(0.0, min(1.0, 1.0 - width_ratio))
        + 0.25 * max(0.0, min(1.0, (upper_r2 + lower_r2) / 2))
        + 0.30 * inside_ratio
        + 0.20 * (1.0 if upper_slope < 0 < lower_slope else 0.0)
    ), 1)
    return {
        "stock_code": stock_code,
        "passed": passed,
        "status": status if passed else "不符合",
        "score": score,
        "close": close,
        "distance_to_upper_pct": round(distance_to_upper_pct, 2),
        "volume_ratio_20d": round(volume_ratio, 2),
        "width_ratio": round(width_ratio, 3),
        "inside_ratio": round(inside_ratio, 3),
        "upper_r2": round(upper_r2, 3),
        "lower_r2": round(lower_r2, 3),
        "conditions": conditions,
        "lines": {
            "start_index": first_x,
            "end_index": last_x,
            "upper_start": round(upper_first, 4),
            "upper_end": round(upper_last, 4),
            "lower_start": round(lower_first, 4),
            "lower_end": round(lower_last, 4),
        },
    }


def _unique_stocks() -> Iterable[tuple[str, str]]:
    from stock_groups import STOCK_GROUPS

    stocks: dict[str, str] = {}
    for members in STOCK_GROUPS.values():
        for code, name in members:
            stocks.setdefault(str(code), name)
    return sorted(stocks.items())


def scan_all_triangles() -> dict[str, Any]:
    from paths import DATA_DIR
    from rule1 import load_daily_bars

    rows: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    requested = 0
    for code, name in _unique_stocks():
        requested += 1
        try:
            result = evaluate_triangle(code, load_daily_bars(code, limit=MAX_BARS))
            if result["passed"]:
                rows.append({"stock_name": name, **result})
        except Exception as error:  # noqa: BLE001
            unavailable.append({"stock_code": code, "stock_name": name, "error": str(error)})

    status_order = {"放量突破": 0, "突破待量": 1, "接近突破": 2, "形成中": 3}
    rows.sort(key=lambda row: (status_order.get(row["status"], 9), -row["score"]))
    output = {
        "strategy": "日線三角收斂",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "requested_count": requested,
            "matched_count": len(rows),
            "unavailable_count": len(unavailable),
        },
        "rows": rows,
        "unavailable": unavailable,
    }
    DATA_DIR.mkdir(exist_ok=True)
    result_path = DATA_DIR / "triangle_screener_latest.json"
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(result_path)
    return output


def load_triangle_results() -> dict[str, Any]:
    from paths import DATA_DIR

    result_path = DATA_DIR / "triangle_screener_latest.json"
    if not result_path.exists():
        raise RuntimeError("尚無三角收斂掃描結果，請先執行盤後掃描。")
    return json.loads(result_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    result = scan_all_triangles()
    print(f"掃描 {result['summary']['requested_count']} 檔，符合 {result['summary']['matched_count']} 檔。")
