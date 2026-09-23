"""處置股「差距預測」：對已經連續2個營業日命中第一款(6日累積漲跌%)的股票，反推明天
收盤價要達到多少才會補上第3次命中、觸發第六條路徑一(連續3個營業日依第一款發布注意)。

跟參考的第三方工具（盤中即時、用還沒收盤的價格持續重算「今天」會不會觸發）不一樣：
本模組是收盤後才跑一次，用官方已經定案的收盤資料，算的是「明天」的門檻，不是「今天」。
這是本模組跟本系統其餘部分（disposition_prediction.py整套都是收盤後批次執行）一致的
設計，不是偷懶——盤中即時重算需要另外接上tick級即時資料，是完全不同的架構。

只做第一款：第一款是唯一一個「6日累積漲跌%」單一數字就能反推收盤價門檻的款，且不需要
發行股數/基本面等額外資料源。其他款(二/三/四/六/七/九/十/十一)要嘛涉及成交量門檻(可以
反推，但價格方向不影響)、要嘛涉及基本面(本益比/淨值比不會因單日股價變動而大幅改變，
反推意義不大)，之後如果需要可以再擴充。

同類/全體平均差幅：明天的同類/全體平均值無法預先得知，用今天已經算好的peer_avg/
industry_avg近似（差幅需要的欄位短期內變動通常不大，是可接受的近似，不是精確預測）。
"""

from __future__ import annotations

from dataclasses import dataclass

from disposition_market_stats import build_market_snapshot, load_market_series
from disposition_prediction import _recent_clause_log
from disposition_rules import _diff_ok

PATH1_CLAUSE = "一"


@dataclass(frozen=True)
class GapPrediction:
    code: str
    clause: str  # 目前只會是"一"
    direction: str  # "up" 或 "down"，跟今天change_6d_pct同號
    threshold_close: float  # 明天收盤價要達到(direction="up"則≥、"down"則≤)這個價才會補中
    change_pct_from_today: float  # 門檻相對今天收盤價的漲跌%，方便前端顯示
    easy: bool  # 門檻是否已經很接近今天收盤價(近似"收平盤/收紅就達標")
    detail: str  # 人類可讀說明


def find_path1_near_miss(codes: set[str], trade_date: str) -> set[str]:
    """回傳「連續2個營業日(含今天)都命中第一款」的股票代號——如果明天也中，就會滿足
    第六條路徑一(連續3個營業日)。連續3天(含)以上的已經真的觸發了，不算「還差1次」，
    這裡只抓剛好2天的。"""
    result: set[str] = set()
    for code in codes:
        history = _recent_clause_log(code, trade_date, lookback_dates=5)
        consecutive = 0
        for _trade, clauses in history:
            if PATH1_CLAUSE in clauses:
                consecutive += 1
            else:
                break
        if consecutive == 2:
            result.add(code)
    return result


def _clause_1_threshold(
    closes: list[float], today_change_6d_pct: float, peer_avg_6d_pct: float | None,
) -> tuple[float, str, float] | None:
    """回傳(明天收盤價門檻, 方向, 明天窗口起點收盤價)，取子條件一(32%)跟子條件二
    (25%+6日價差≥50元)裡門檻價格較容易達成的那個；差幅條件用今天的peer_avg近似。
    closes是bars_1d由舊到新排序、最後一筆是今天。"""
    if len(closes) < 5:
        return None
    ref = closes[-5]  # 明天的6日窗口起點：今天往前推4個營業日那天的收盤價
    if ref <= 0:
        return None
    up = today_change_6d_pct >= 0
    candidates: list[float] = []
    if peer_avg_6d_pct is None or _diff_ok(32.0 if up else -32.0, peer_avg_6d_pct, 20.0):
        candidates.append(ref * 1.32 if up else ref * 0.68)
    if peer_avg_6d_pct is None or _diff_ok(25.0 if up else -25.0, peer_avg_6d_pct, 20.0):
        price_by_pct = ref * 1.25 if up else ref * 0.75
        price_by_diff = ref + 50 if up else ref - 50
        candidates.append(max(price_by_pct, price_by_diff) if up else min(price_by_pct, price_by_diff))
    if not candidates:
        return None
    threshold = min(candidates) if up else max(candidates)
    return threshold, ("up" if up else "down"), ref


def _build_prediction(code: str, closes: list[float], today_close: float, today_change_6d_pct: float, peer_avg_6d_pct: float | None) -> GapPrediction | None:
    result = _clause_1_threshold(closes, today_change_6d_pct, peer_avg_6d_pct)
    if result is None:
        return None
    threshold, direction, _ref = result
    if today_close <= 0:
        return None
    change_pct = (threshold - today_close) / today_close * 100
    easy = change_pct <= 0.5 if direction == "up" else change_pct >= -0.5
    if easy:
        detail = "收平盤或收紅就可能達標" if direction == "up" else "收平盤或收黑就可能達標"
    else:
        detail = f"{'漲幅' if direction == 'up' else '跌幅'} {change_pct:+.2f}% 以上才會達標"
    return GapPrediction(
        code=code, clause=PATH1_CLAUSE, direction=direction, threshold_close=round(threshold, 2),
        change_pct_from_today=round(change_pct, 2), easy=easy, detail=detail,
    )


def build_gap_predictions(trade_date: str, codes: set[str]) -> list[GapPrediction]:
    """對codes裡「連續2天中第一款」的股票，算出明天收盤價門檻，回傳list(門檻越容易
    達成的排越前面)。用不到的股票(沒有連續2天命中、或歷史不足5天)不會出現在結果裡。"""
    near_miss = find_path1_near_miss(codes, trade_date)
    if not near_miss:
        return []
    snapshot = build_market_snapshot(trade_date)
    peer_avg_6d_pct = snapshot.peer_avg.get("change_6d_pct")
    series_by_code = load_market_series(trade_date)
    predictions: list[GapPrediction] = []
    for code in near_miss:
        metrics = snapshot.metrics_by_code.get(code)
        series = series_by_code.get(code)
        if metrics is None or metrics.change_6d_pct is None or series is None:
            continue
        prediction = _build_prediction(code, series.closes, metrics.close, metrics.change_6d_pct, peer_avg_6d_pct)
        if prediction is not None:
            predictions.append(prediction)
    predictions.sort(key=lambda p: (not p.easy, abs(p.change_pct_from_today)))
    return predictions
