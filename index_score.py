"""指數均線分數（2026-10-24 使用者：老師均線分數排行頁右上角的「櫃買分數」小卡；加權、櫃買兩個指數都做，首頁的櫃買、加權指數分析也要放）。

算法跟個股完全一樣（heilong_backtest.official_parts）：站上 5／10／20／60／120／240 日均線各 1 分＋創 5／10／20／60／120／360 日新高各 1 分＋
排列（20 日在 60 日上、60 在 120 上、120 在 350 上，也就是黃金交叉之後的多頭排列）各 1 分，滿分 15。
小卡上的分數是「近 3 個交易日日分數的平均」，分水嶺 6 分（≥6 多、<6 空；當天分數、3 日平均各自標）。

用 2026-10-22 的櫃買指數對老師頁驗證過：日分數 10/20＝3、10/21＝4、10/22＝3，近 3 日平均 10/22＝3.33、10/21＝3.33，
跟老師小卡「今天 3.33、明天 3.33」一模一樣。
- 明天：最新一個收盤（資料日）算出來的 3 日平均（明天盤中看的訊號）；
- 今天：前一個交易日收盤算出來的 3 日平均（今天盤中看的訊號）。
資料用資金保護傘每天晚上抓的加權（TAIEX）、櫃買（TPEx）日K收盤（fund_umbrella_series），從 2020 年起。
"""

from __future__ import annotations

from typing import Any

import fund_umbrella
from heilong_backtest import MA_PERIODS, OFFICIAL_HI_PERIODS, official_parts

AVG_DAYS = 3
THRESHOLD = 6
HIGH_DAYS = 120          # 距高點：近半年（120 個交易日）的最高點
DAILY_SHOWN = 5
INDEXES = (("otc", "tpex", "櫃買"), ("taiex", "taiex", "加權"))


def label(avg: float) -> str:
    """分水嶺 6 分：≥6 多、<6 空（2026-10-24 使用者：加權 3 日平均 6.00 標多；當天 5 分標空）。每個分數各自標：當天分數、3 日平均各一個。"""
    return "多" if avg >= THRESHOLD else "空"


def daily_scores(dates: list[str], closes: list[float], count: int) -> list[dict[str, Any]]:
    """最後 count 個交易日每天的分數（含各部分）；不足 240 根的回空。"""
    need = max(MA_PERIODS)
    out = []
    for i in range(max(len(closes) - count, need - 1), len(closes)):
        parts = official_parts(closes[:i + 1])
        if parts is None:
            continue
        ma_bits, hi_bits, align = parts
        ma, hi = bin(ma_bits).count("1"), bin(hi_bits).count("1")
        out.append({"date": dates[i], "close": closes[i], "score": ma + hi + align, "label": label(ma + hi + align), "ma": ma, "hi": hi, "align": align,
                    "maOf": len(MA_PERIODS), "hiOf": len(OFFICIAL_HI_PERIODS)})
    return out


def _avg(rows: list[dict[str, Any]], end: int) -> dict[str, Any] | None:
    window = rows[end - AVG_DAYS + 1:end + 1] if end >= AVG_DAYS - 1 else []
    if len(window) < AVG_DAYS:
        return None
    avg = round(sum(r["score"] for r in window) / AVG_DAYS, 2)
    return {"basis": window[-1]["date"], "avg": avg, "label": label(avg)}


def table_block(dates: list[str], closes: list[float], highs: dict[str, float] | None = None) -> dict[str, Any]:
    """指數對照表（2026-10-24 使用者：老師影片「10/22 收盤 vs 月／季／半年線」）：收盤、20／60／120 日線、近半年高點與距高點 %。
    高點用當天最高點（盤中最高，抓得到才用；老師加權 10/6 高點 49,969 就是盤中最高），沒有就用收盤。"""
    close = closes[-1]
    window = list(zip(dates, closes))[-HIGH_DAYS:]
    high, high_date = max(((highs or {}).get(d) or c, d) for d, c in window)
    out: dict[str, Any] = {"close": close, "high": round(high, 2), "highDate": high_date, "fromHigh": round((close / high - 1) * 100, 1),
                           "highIntraday": bool(highs and any(highs.get(d) for d, _c in window))}
    for period in (20, 60, 120):
        out[f"ma{period}"] = round(sum(closes[-period:]) / period, 2) if len(closes) >= period else None
    return out


def index_payload(dates: list[str], closes: list[float], highs: dict[str, float] | None = None) -> dict[str, Any] | None:
    rows = daily_scores(dates, closes, AVG_DAYS + 3)
    if len(rows) < AVG_DAYS + 1:
        return None
    nxt, today = _avg(rows, len(rows) - 1), _avg(rows, len(rows) - 2)
    return {"asOf": rows[-1]["date"], "close": rows[-1]["close"], "daily": rows[-DAILY_SHOWN:], "today": today, "next": nxt,
            "table": table_block(dates, closes, highs)}


def payload() -> dict[str, Any]:
    data = fund_umbrella._load()
    out: dict[str, Any] = {}
    for key, series, name in INDEXES:
        values = data.get(series) or {}
        dates = sorted(values)
        highs = data.get("taiex_high" if series == "taiex" else "tpex_high") or {}
        got = index_payload(dates, [float(values[d]) for d in dates], highs) if dates else None
        if got:
            out[key] = {"name": name, **got}
    if not out:
        return {"status": "missing", "message": "指數日K還沒抓齊（資金保護傘每天晚上 21:40 後更新）"}
    return {"status": "ok", "threshold": THRESHOLD, "avgDays": AVG_DAYS, "indexes": out}
