"""處置股「差距預測」：反推股票要達到什麼條件才會觸發特定款別。

第一款(價格類)：對已經連續2個營業日命中第一款(6日累積漲跌%)的股票，反推明天收盤價
要達到多少才會補上第3次命中、觸發第六條路徑一(連續3個營業日依第一款發布注意)。這個
反推依賴「連續天數」，本質上是「明天」的門檻。

第九/十款(成交量類，不需要連續天數，單一天量能達標就觸發)：算出的門檻不依賴當天(即
呼叫時最新一個尚未收盤的交易日)的任何資料——avg_60d_volume(第九款)、發行股數/前5個
營業日累積量(第十款)都只用「呼叫時已經收盤定案」的歷史算，所以門檻算出來後在下一個
交易日整天都有效，不用等下一個交易日收盤才重算。這代表這兩款的門檻數字本身雖然是
收盤後批次算出來的，但可以拿去跟「盤中即時成交量」比對，讓使用者在盤中就看到「還差
多少量」——真正即時的部分是「目前成交量」，不是這個門檻本身；門檻本身收盤後算一次
就夠用到下一個交易日收盤為止。

差幅門檻(跟全體平均比)不是在「最低倍數/百分比」這一點測一次就決定有沒有解：官方原文
檢查的是「當天實際達成的倍數/百分比」跟peer_avg的差幅，不是固定用5倍或10%去檢查。
只要把倍數/百分比推得夠高，差幅條件對任何有限的peer_avg幾乎都找得到解，所以peer_avg
剛好落在「最低倍數」附近±差幅門檻這個「死區」時，正確的反推門檻是peer_avg+差幅門檻
（比最低倍數更高），不是直接視為無解——見_min_ratio_satisfying_diff_gate()。第九款
另外還有「6日均量/60日均量>=5倍」這個獨立子條件(跟「當日量/60日均量>=5倍」是OR的
關係，兩個子條件各自反推門檻、取較容易達成的)，用「明天」的6日均量(前5個已知交易日
+明天的量)反推，跟clause_10的累積週轉率子條件是同樣的線性反推結構。

跟參考的第三方工具（盤中即時、用還沒收盤的價格持續重算「今天」會不會觸發）不一樣：
本模組收盤後才跑一次算門檻，不是自己另外接tick級即時資料重算全部邏輯——這是刻意的
架構選擇，門檻計算維持跟disposition_prediction.py整套收盤後批次一致，即時性交給
「拿門檻去跟盤中即時量比對」這一步，不是每次都重新反推門檻。

不做的款：二/三/四/六/七要嘛涉及基本面(本益比/淨值比不會因單日大幅改變，反推意義
不大)、要嘛跟第一款一樣需要連續天數但计算更複雜，之後如果需要可以再擴充。

同類/全體平均差幅：用最近一次已經算好的peer_avg近似(差幅需要的欄位短期內變動通常
不大，是可接受的近似，不是精確預測)。
"""

from __future__ import annotations

from dataclasses import dataclass

from disposition_fundamentals_assembly import build_fundamentals_by_code
from disposition_market_stats import build_market_snapshot, load_market_series
from disposition_prediction import _recent_clause_log
from disposition_rules import _diff_ok

PATH1_CLAUSE = "一"
VOLUME_ONLY_CLAUSE_9 = "九"
VOLUME_ONLY_CLAUSE_10 = "十"


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


@dataclass(frozen=True)
class VolumeGapPrediction:
    code: str
    clause: str  # "九" 或 "十"
    threshold_volume: float  # 觸發門檻的成交量（張）；下一個交易日整天有效，不用等收盤重算
    reference_volume: float  # trade_date當天(算門檻那天)收盤時的成交量，當作沒有即時資料時的退回值
    detail: str  # 人類可讀說明


def _min_ratio_satisfying_diff_gate(min_ratio: float, peer_avg: float | None, min_diff: float) -> float:
    """「數值本身要>=min_ratio」且「差幅|數值-peer_avg|>=min_diff」兩個條件都要滿足時，
    回傳最小的合格數值。peer_avg是None代表沒有同類/全體資料可比，直接用min_ratio。
    差幅條件不是在min_ratio這一點測一次就決定有沒有解——只要把數值推得夠高，差幅
    條件對任何有限的peer_avg都找得到解，所以min_ratio附近的差幅不夠只代表門檻要
    墊高到peer_avg+min_diff，不是無解（不會發生：官方原文檢查的是「實際達成值」
    跟peer_avg的差幅，不是固定在min_ratio這一點檢查）。"""
    if peer_avg is None or abs(min_ratio - peer_avg) >= min_diff:
        return min_ratio
    return peer_avg + min_diff


def clause_9_threshold_volume(
    avg_60d_volume: float | None,
    peer_avg_ratio_60d: float | None,
    *,
    cum_volume_prior_5d_lots: float | None = None,
    peer_avg_ratio_6d_60d: float | None = None,
) -> float | None:
    """算出第九款成交量門檻(張)：官方原文兩個子條件是OR關係(符合其一即觸發)，各自
    反推門檻後取較容易達成(較小)的那個。子條件一：當日量/60日均量>=5倍(差幅>=4，
    用_min_ratio_satisfying_diff_gate找出最小合格倍數，peer_avg卡在死區時門檻會
    墊高，不是直接無解)。子條件二：明天的6日均量/60日均量>=5倍(差幅>=4)——明天
    6日均量=(前5個已知交易日累積量cum_volume_prior_5d_lots + 明天的量)/6，一樣
    反推明天的量。avg_60d_volume是最近60個營業日(含trade_date，近似明天的60日
    均量)日均量。cum_volume_prior_5d_lots沒給時只算子條件一(呼叫端沒有這個資料
    時的退回，例如歷史不足5天)。"""
    if not avg_60d_volume or avg_60d_volume <= 0:
        return None
    ratio1 = _min_ratio_satisfying_diff_gate(5.0, peer_avg_ratio_60d, 4.0)
    candidates = [ratio1 * avg_60d_volume]
    if cum_volume_prior_5d_lots is not None:
        ratio2 = _min_ratio_satisfying_diff_gate(5.0, peer_avg_ratio_6d_60d, 4.0)
        candidates.append(6.0 * ratio2 * avg_60d_volume - cum_volume_prior_5d_lots)
    return min(candidates)


def clause_10_threshold_volume(
    shares_outstanding_lots: float | None, cum_volume_prior_5d_lots: float,
    turnover_peer_avg_pct: float | None, cum_turnover_peer_avg_pct: float | None,
) -> tuple[float, str] | None:
    """算出成交量(張)要多少才會同時滿足第十款兩個子條件(AND，缺一不可)：當日週轉率
    >=10%(差幅>=5)、6日累積週轉率(前5個營業日已知量+當天量)>50%(差幅>=40)——回傳
    (門檻張數,是哪個子條件卡關)，取兩個各自反推出來的門檻量較大(較嚴格)的那個。
    差幅門檻用_min_ratio_satisfying_diff_gate找最小合格百分比，peer_avg卡在死區
    時門檻百分比會墊高，不是直接無解。shares_outstanding_lots=發行股數換算成張
    (市值/收盤價/1000近似，股本短期內視為常數)；cum_volume_prior_5d_lots=不含
    當天的前5個營業日累積成交量(張)。"""
    if not shares_outstanding_lots or shares_outstanding_lots <= 0:
        return None
    today_turnover_ratio = _min_ratio_satisfying_diff_gate(10.0, turnover_peer_avg_pct, 5.0)
    cum_turnover_ratio = _min_ratio_satisfying_diff_gate(50.0, cum_turnover_peer_avg_pct, 40.0)
    volume_for_today_turnover = shares_outstanding_lots * today_turnover_ratio / 100.0
    volume_for_cum_turnover = shares_outstanding_lots * cum_turnover_ratio / 100.0 - cum_volume_prior_5d_lots
    if volume_for_cum_turnover > volume_for_today_turnover:
        return volume_for_cum_turnover, "6日累積週轉率"
    return volume_for_today_turnover, "當日週轉率"


VOLUME_GAP_INCLUDE_RATIO = 0.5  # 量已經到門檻一半以上才列入，避免524檔全部出現變雜訊


def build_volume_gap_predictions(trade_date: str, codes: set[str]) -> list[VolumeGapPrediction]:
    """算出codes裡每一檔第九/十款的成交量門檻——門檻只用trade_date(含)為止已經定案
    的歷史算，不依賴trade_date之後任何一天的資料，所以算出來的門檻在下一個交易日
    整天都有效，可以直接拿去跟盤中即時成交量比較，不用等下一個交易日收盤才重算。
    只回傳trade_date當天收盤量已經到門檻一半以上的股票，避免把524檔全部列出來變成
    雜訊。reference_volume是trade_date當天收盤時的量，呼叫端(persistent_app.py)會
    盡量用即時Hub資料覆蓋，沒有即時資料時才退回用這個。"""
    snapshot = build_market_snapshot(trade_date)
    peer_avg_ratio_60d = snapshot.peer_avg.get("volume_ratio_60d")
    peer_avg_ratio_6d_60d = snapshot.peer_avg.get("avg_volume_ratio_6d_60d")
    series_by_code = load_market_series(trade_date)
    fundamentals_by_code = build_fundamentals_by_code(trade_date, codes)
    turnover_peer_avg = next(
        (f["turnover_pct_peer_avg"] for f in fundamentals_by_code.values() if f.get("turnover_pct_peer_avg") is not None),
        None,
    )
    cum_turnover_peer_avg = next(
        (f["cum_turnover_6d_peer_avg_pct"] for f in fundamentals_by_code.values() if f.get("cum_turnover_6d_peer_avg_pct") is not None),
        None,
    )

    predictions: list[VolumeGapPrediction] = []
    for code in codes:
        metrics = snapshot.metrics_by_code.get(code)
        series = series_by_code.get(code)
        if metrics is None or metrics.volume is None or series is None:
            continue
        reference_volume = metrics.volume
        # 前5個已知交易日累積量：第九款子條件二(明天6日均量)、第十款(明天6日累積
        # 週轉率)都要用到，這裡算一次共用，不用各自重算。
        cum_volume_prior_5d_lots = sum(series.volumes[-5:]) if len(series.volumes) >= 5 else None

        if len(series.volumes) >= 60:
            avg60 = sum(series.volumes[-60:]) / 60
            threshold9 = clause_9_threshold_volume(
                avg60, peer_avg_ratio_60d,
                cum_volume_prior_5d_lots=cum_volume_prior_5d_lots, peer_avg_ratio_6d_60d=peer_avg_ratio_6d_60d,
            )
            if threshold9 is not None and reference_volume >= threshold9 * VOLUME_GAP_INCLUDE_RATIO:
                gap = threshold9 - reference_volume
                detail = "量已達門檻" if gap <= 0 else f"還差約{gap:.0f}張（門檻{threshold9:.0f}張）"
                predictions.append(VolumeGapPrediction(
                    code=code, clause=VOLUME_ONLY_CLAUSE_9, threshold_volume=round(max(0.0, threshold9), 0),
                    reference_volume=reference_volume, detail=detail,
                ))

        fundamentals = fundamentals_by_code.get(code) or {}
        shares_outstanding = fundamentals.get("shares_outstanding")
        shares_outstanding_lots = shares_outstanding / 1000 if shares_outstanding else None
        if shares_outstanding_lots is not None and cum_volume_prior_5d_lots is not None:
            result10 = clause_10_threshold_volume(
                shares_outstanding_lots, cum_volume_prior_5d_lots, turnover_peer_avg, cum_turnover_peer_avg,
            )
            if result10 is not None:
                threshold10, binding = result10
                if reference_volume >= threshold10 * VOLUME_GAP_INCLUDE_RATIO:
                    gap = threshold10 - reference_volume
                    detail = (
                        "量已達門檻" if gap <= 0
                        else f"還差約{gap:.0f}張（門檻{threshold10:.0f}張，卡在{binding}）"
                    )
                    predictions.append(VolumeGapPrediction(
                        code=code, clause=VOLUME_ONLY_CLAUSE_10, threshold_volume=round(threshold10, 0),
                        reference_volume=reference_volume, detail=detail,
                    ))

    predictions.sort(key=lambda p: p.threshold_volume - p.reference_volume)
    return predictions
