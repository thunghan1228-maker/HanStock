"""把disposition_phase3_store.py存的原始FinMind資料(當日沖銷成交量/借券賣出成交量)，
換算成disposition_rules.ClauseInputs要的第十二款(借券賣出比例)/第十三款(當沖比例)
欄位。跟disposition_fundamentals_assembly.py的Phase 2欄位一樣，只用「真的存過的天數」
算，缺的天數不補0——這兩個表是這次(Phase 3)才開始收集，剛上線的前60個營業日還沒有
滿60天歷史，第十二款「5倍放大」那個子條件(需要60日均量)會回傳None(不會誤判)，直到
收滿60天為止；兩款都要用到的6日累積比例window則大約一週內就會補齊。

第十二款/第十三款的官方原文都是「最近6營業日(從前一營業日起)⋯占總成交量比率」，這裡
採用跟第十款累積週轉率同一種算法：分子分母各自加總6天再相除一次(不是先算6個每日比率
再平均)——跟第七款「逐日比率取最小值」是不同的計算方式，第七款本來就沒有可加總的分子
分母。

「從前一營業日起」的日期偏移邏輯跟disposition_fundamentals_assembly.py的第七款一樣：
store撈出來的歷史如果最後一筆剛好是trade_date(收集器收盤後跑，當天的當沖/借券賣出
資料通常已經可以拿到)，要跳過那筆才拿得到「前一營業日」；如果store本身只到前一營業日
為止(還沒收集到今天)，就不用跳過。
"""

from __future__ import annotations

from typing import Any

from daily_bars_store import load_daily_bars
from disposition_phase3_store import load_day_trading_range, load_sbl_short_sale_range

REQUIRED_6D_ROWS = 6
REQUIRED_60D_ROWS = 60


def _bars_volume_by_date(code: str, limit: int) -> dict[str, float]:
    return {bar["ts"][:10]: float(bar["volume"]) for bar in load_daily_bars(code, limit=limit)}


def _history_excluding_today(rows: list[dict[str, Any]], trade_date: str) -> list[dict[str, Any]]:
    if rows and rows[-1]["trade_date"] == trade_date:
        return rows[:-1]
    return rows


def _prev_day_and_cum_ratio(
    rows: list[dict[str, Any]], value_key: str, trade_date: str, total_volume_by_date: dict[str, float],
) -> tuple[float | None, float | None, float | None]:
    """回傳(前一營業日的原始量, 前一營業日比率%, 6日累積比率%)。rows是由舊到新的
    store歷史(load_day_trading_range/load_sbl_short_sale_range的結果)。"""
    history = _history_excluding_today(rows, trade_date)
    if not history:
        return None, None, None
    prev_row = history[-1]
    prev_volume = float(prev_row[value_key])
    prev_total = total_volume_by_date.get(prev_row["trade_date"])
    prev_ratio = (prev_volume / prev_total * 100) if prev_total else None

    window = history[-REQUIRED_6D_ROWS:]
    cum_ratio: float | None = None
    if len(window) == REQUIRED_6D_ROWS:
        total_target = 0.0
        total_all = 0.0
        complete = True
        for row in window:
            day_total = total_volume_by_date.get(row["trade_date"])
            if day_total is None:
                complete = False
                break
            total_target += float(row[value_key])
            total_all += day_total
        if complete and total_all > 0:
            cum_ratio = total_target / total_all * 100
    return prev_volume, prev_ratio, cum_ratio


def _avg_60d_from_previous_day(rows: list[dict[str, Any]], value_key: str, trade_date: str) -> float | None:
    history = _history_excluding_today(rows, trade_date)
    window = history[-REQUIRED_60D_ROWS:]
    if len(window) < REQUIRED_60D_ROWS:
        return None
    return sum(float(row[value_key]) for row in window) / REQUIRED_60D_ROWS


def build_phase3_by_code(trade_date: str, codes: set[str]) -> dict[str, dict[str, float | None]]:
    """組出跟disposition_fundamentals_assembly.build_fundamentals_by_code()同樣格式
    的{代號: {欄位: 值}}，對應disposition_rules.ClauseInputs的第十二/十三款欄位——
    呼叫端(disposition_prediction_collector.py)會把這個結果merge進同一份
    fundamentals_by_code再傳給disposition_prediction.build_clause_inputs()。"""
    output: dict[str, dict[str, float | None]] = {}
    for code in codes:
        total_volume_by_date = _bars_volume_by_date(code, limit=REQUIRED_6D_ROWS + 2)
        day_trading_rows = load_day_trading_range(code, trade_date, days=REQUIRED_6D_ROWS + 1)
        sbl_rows = load_sbl_short_sale_range(code, trade_date, days=REQUIRED_60D_ROWS + 1)

        dt_prev_volume, dt_prev_ratio, dt_cum_ratio = _prev_day_and_cum_ratio(
            day_trading_rows, "day_trading_volume", trade_date, total_volume_by_date,
        )
        sbl_prev_volume, _sbl_prev_ratio, sbl_cum_ratio = _prev_day_and_cum_ratio(
            sbl_rows, "sbl_short_sale_volume", trade_date, total_volume_by_date,
        )
        sbl_avg_60d = _avg_60d_from_previous_day(sbl_rows, "sbl_short_sale_volume", trade_date)

        output[code] = {
            "day_trading_prev_day_ratio_pct": dt_prev_ratio,
            "day_trading_cum_6d_ratio_pct": dt_cum_ratio,
            "day_trading_prev_day_volume": dt_prev_volume,
            "sbl_short_sale_cum_6d_ratio_pct": sbl_cum_ratio,
            "sbl_short_sale_prev_day_volume": sbl_prev_volume,
            "sbl_short_sale_avg_60d_volume": sbl_avg_60d,
        }
    return output
