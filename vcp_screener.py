"""VCP（波動收縮型態）辨識與上市櫃全市場盤後掃描。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

MIN_BARS = 80
MAX_BARS = 180
TARGET_STATUSES = ("VCP形成中", "接近突破", "今日帶量突破", "突破後過熱")


def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def _pivot_lows(values: list[float], radius: int = 2) -> list[int]:
    result: list[int] = []
    for index in range(radius, len(values) - radius):
        window = values[index - radius:index + radius + 1]
        if values[index] == min(window) and window.count(values[index]) == 1:
            result.append(index)
    return result


def evaluate_vcp(stock_code: str, daily_bars: list[dict[str, Any]]) -> dict[str, Any]:
    """辨識第二階段上升趨勢中的 2～4 次波動與量能收縮。"""
    bars = daily_bars[-MAX_BARS:]
    if len(bars) < MIN_BARS:
        raise RuntimeError(f"{stock_code} 日K資料不足，至少需要 {MIN_BARS} 日。")

    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    closes = [float(bar["close"]) for bar in bars]
    volumes = [max(0.0, float(bar.get("volume", 0) or 0)) for bar in bars]
    close = closes[-1]
    ma20, ma50 = _sma(closes, 20), _sma(closes, 50)
    ma50_month_ago = sum(closes[-70:-20]) / 50
    ma200 = _sma(closes, 200) if len(closes) >= 200 else min(closes)

    # Pivot 以近 100 日為主；最後一根不參與樞紐高點，避免突破日改寫買點。
    base_start = max(0, len(bars) - 100)
    pivot = max(highs[base_start:-1])
    pivot_index = base_start + highs[base_start:-1].index(pivot)
    low_indexes = [i for i in _pivot_lows(lows[pivot_index:]) if i + pivot_index < len(bars) - 1]
    low_indexes = [i + pivot_index for i in low_indexes][-4:]
    contractions = [round((pivot - lows[i]) / pivot * 100, 2) for i in low_indexes]
    # 保留由大到小的有效收縮序列，允許 15% 雜訊。
    shrinking: list[float] = []
    for value in contractions:
        if not shrinking or value <= shrinking[-1] * 1.15:
            shrinking.append(value)

    avg_volume_20 = sum(volumes[-21:-1]) / min(20, len(volumes) - 1)
    avg_volume_50 = sum(volumes[-51:-1]) / min(50, len(volumes) - 1)
    volume_ratio = volumes[-1] / avg_volume_20 if avg_volume_20 else 0.0
    dry_up_ratio = avg_volume_20 / avg_volume_50 if avg_volume_50 else 0.0
    distance_to_pivot = (pivot - close) / pivot * 100 if pivot else 999.0
    breakout_pct = (close - pivot) / pivot * 100 if pivot else -999.0
    prior_52w_high = max(highs[-min(252, len(highs)):])

    conditions = {
        "第二階段趨勢": close > ma50 and ma20 >= ma50 and ma50 > ma50_month_ago and close > ma200,
        "接近年度高點": close >= prior_52w_high * 0.75,
        "至少兩次收縮": len(shrinking) >= 2,
        "回檔逐次縮小": len(shrinking) >= 2 and shrinking[-1] < shrinking[0] * 0.75,
        "末次收縮不過寬": bool(shrinking) and shrinking[-1] <= 15.0,
        "量能收斂": dry_up_ratio <= 0.90 or volume_ratio <= 0.80,
    }
    passed = all(conditions.values())
    status = "不符合"
    if passed:
        status = "VCP形成中"
        if -1.0 <= distance_to_pivot <= 5.0:
            status = "接近突破"
        if close > pivot * 1.005 and volume_ratio >= 1.5:
            status = "今日帶量突破"
        if breakout_pct >= 8.0:
            status = "突破後過熱"

    contraction_score = min(1.0, len(shrinking) / 4) * 25
    shrink_score = min(1.0, (shrinking[0] / max(shrinking[-1], 0.1) - 1) / 2) * 30 if len(shrinking) >= 2 else 0
    proximity_score = max(0.0, 1.0 - abs(distance_to_pivot) / 10) * 25
    volume_score = max(0.0, min(1.0, 1.0 - dry_up_ratio)) * 20
    return {
        "stock_code": stock_code,
        "passed": passed,
        "status": status,
        "score": round(contraction_score + shrink_score + proximity_score + volume_score, 1),
        "close": close,
        "pivot_price": round(pivot, 2),
        "distance_to_pivot_pct": round(distance_to_pivot, 2),
        "volume_ratio_20d": round(volume_ratio, 2),
        "volume_dry_up_ratio": round(dry_up_ratio, 2),
        "contractions_pct": shrinking,
        "conditions": conditions,
    }


def _unique_stocks() -> Iterable[tuple[str, str]]:
    from database import get_connection

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT stock_code, stock_name FROM stocks ORDER BY stock_code"
        ).fetchall()
    # 普通股代號為四位數；自然排除期貨、ETF、權證及 ETN。
    return [(str(row["stock_code"]), str(row["stock_name"])) for row in rows if str(row["stock_code"]).isdigit() and len(str(row["stock_code"])) == 4]


def scan_all_vcp() -> dict[str, Any]:
    from paths import DATA_DIR
    from rule1 import load_daily_bars

    rows: list[dict[str, Any]] = []
    unavailable: list[dict[str, str]] = []
    requested = 0
    for code, name in _unique_stocks():
        requested += 1
        try:
            result = evaluate_vcp(code, load_daily_bars(code, limit=MAX_BARS))
            if result["passed"]:
                rows.append({"stock_name": name, **result})
        except Exception as error:  # noqa: BLE001
            unavailable.append({"stock_code": code, "stock_name": name, "error": str(error)})

    order = {status: index for index, status in enumerate(("今日帶量突破", "接近突破", "VCP形成中", "突破後過熱"))}
    rows.sort(key=lambda row: (order.get(row["status"], 9), -row["score"]))
    output = {
        "strategy": "VCP波動收縮",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {"requested_count": requested, "matched_count": len(rows), "unavailable_count": len(unavailable)},
        "rows": rows,
        "unavailable": unavailable,
    }
    DATA_DIR.mkdir(exist_ok=True)
    result_path = DATA_DIR / "vcp_screener_latest.json"
    temporary = result_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(result_path)
    return output


def load_vcp_results() -> dict[str, Any]:
    from paths import DATA_DIR

    result_path = DATA_DIR / "vcp_screener_latest.json"
    if not result_path.exists():
        raise RuntimeError("尚無 VCP 掃描結果，請先執行盤後掃描。")
    return json.loads(result_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    result = scan_all_vcp()
    print(f"掃描 {result['summary']['requested_count']} 檔，符合 {result['summary']['matched_count']} 檔。")
