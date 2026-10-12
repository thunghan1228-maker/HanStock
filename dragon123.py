"""123 黑龍戰法（2026-10-12 使用者貼的教學）：一筆資金分三次，在創高後的黑K分批進場。

  「買三次都還沒破，代表有支撐，通常也容易往上。第一根通常黑豹，後面會變黑飛舞或黑龍（量縮黑龍）。
   選在區 1：均線分數都很高、都有創高，通常勝率高，可積極加碼；選在區 2：還在整理、壓力多，不一定會成功，
   建議慢慢等它突破再加碼。」

怎麼算（跟創高黑龍選股同一套條件，三個地方——問診日線圖、K線圖日線、盤中觀察股／訊號中心——看到的是同一個數字）：
- 進場點＝創高黑龍：均線分數（官網那套，滿分 15）≥ 10、收黑K（收盤 < 開盤）、當天漲跌 −10%～+3%。
- 1／2／3：每次最高價創 20 日新高就重新數，之後第 1、2、3 個進場點標 1、2、3（三次買滿，第 4 個以後不標，等下一次創高）。
- 區 1：均線分數 ≥ 13，而且創 20 日新高還在 5 個交易日內 → 可積極加碼；其他是區 2（整理中，等突破再加碼）。
"""

from __future__ import annotations

from typing import Any, Sequence

NEW_HIGH_DAYS = 20       # 創高＝最高價是近 20 個交易日（含當天）最高
ENTRY_SCORE = 10         # 創高黑龍：均線分數 ≥10
ENTRY_PCT = (-10.0, 3.0)  # 創高黑龍：漲跌 −10%～+3%
REGION1_SCORE = 13
REGION1_WITHIN = 5       # 創高後 5 個交易日內
MAX_COUNT = 3

# bars：(日期, 開, 高, 低, 收, 量) 舊到新
Bar = Sequence[Any]


def is_entry(bar: Bar, prev_close: float | None, score: int | None) -> bool:
    if score is None or score < ENTRY_SCORE or not prev_close or prev_close <= 0:
        return False
    pct = (bar[4] / prev_close - 1) * 100
    return bar[4] < bar[1] and ENTRY_PCT[0] <= pct <= ENTRY_PCT[1]


def region(score: int | None, since_high: int | None) -> int:
    return 1 if score is not None and score >= REGION1_SCORE and since_high is not None and since_high <= REGION1_WITHIN else 2


def scan(bars: list[Bar], scores: list[int | None]) -> dict[str, Any]:
    """scores[i]＝bars[i] 那天收盤的均線分數（None＝算不出來）。回每個進場點與最後一天收盤後的狀態：
    {"marks": [{date, n, region, score, sinceHigh}], "state": {count, sinceHigh, high19}}
    state 給盤中用：今天如果收黑K又符合條件，就是第 count+1 根；今天最高價 ≥ high19（前 19 天最高）就算創新高、重新數。"""
    marks: list[dict[str, Any]] = []
    last_high: int | None = None
    count = 0
    highs = [float(b[2]) for b in bars]
    for i, bar in enumerate(bars):
        if i >= NEW_HIGH_DAYS - 1 and highs[i] >= max(highs[i - NEW_HIGH_DAYS + 1:i]):
            last_high, count = i, 0
        prev_close = float(bars[i - 1][4]) if i > 0 else None
        if last_high is not None and is_entry(bar, prev_close, scores[i]):
            count += 1
            if count <= MAX_COUNT:
                since = i - last_high
                marks.append({"date": str(bar[0])[:10], "n": count, "region": region(scores[i], since), "score": scores[i], "sinceHigh": since})
    n = len(bars)
    state = {
        "count": count,
        "sinceHigh": (n - 1 - last_high) if last_high is not None else None,
        "high19": max(highs[-(NEW_HIGH_DAYS - 1):]) if n >= NEW_HIGH_DAYS - 1 else None,
    }
    return {"marks": marks, "state": state}


def daily_scores(closes: list[float], days: int) -> list[int | None]:
    """最後 days 天每天收盤的均線分數（官網那套）；前面不算的補 None，長度跟 closes 一樣。"""
    from heilong_backtest import official_score

    n = len(closes)
    out: list[int | None] = [None] * n
    for i in range(max(0, n - days), n):
        out[i] = official_score(closes[:i + 1])
    return out


def for_bars(bars: list[Bar], days: int) -> dict[str, Any]:
    """bars 要多給 240 根以上（均線分數要 240 日線）；只算最後 days 天的分數與進場點。"""
    scores = daily_scores([float(b[4]) for b in bars], days + NEW_HIGH_DAYS)
    out = scan(bars, scores)
    cutoff = str(bars[-days][0])[:10] if len(bars) >= days else ""
    out["marks"] = [m for m in out["marks"] if m["date"] >= cutoff]
    return out


def rules() -> dict[str, Any]:
    return {
        "entryScore": ENTRY_SCORE, "entryPct": list(ENTRY_PCT), "newHighDays": NEW_HIGH_DAYS,
        "region1Score": REGION1_SCORE, "region1Within": REGION1_WITHIN, "maxCount": MAX_COUNT,
        "text": (f"進場點＝創高黑龍（均線分數 ≥{ENTRY_SCORE}、收黑K、漲跌 {ENTRY_PCT[0]:g}%～+{ENTRY_PCT[1]:g}%）；"
                 f"每次創 {NEW_HIGH_DAYS} 日新高重新數，第 1、2、3 個進場點標 1、2、3（一筆資金分三次，第 4 個以後不標）；"
                 f"區 1＝均線分數 ≥{REGION1_SCORE} 且創高 {REGION1_WITHIN} 個交易日內，可積極加碼；區 2＝整理中，等突破再加碼"),
    }
