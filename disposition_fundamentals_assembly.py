"""把disposition_fundamentals_store.py存的原始FinMind資料(本益比/淨值比/市值/融資
融券餘額)，換算成disposition_rules.ClauseInputs要的Phase 2欄位(週轉率/券資比/融資
融券使用率)，並算出這524檔範圍內的橫斷面平均值。

官方原文的日期偏移要注意：第七款的券資比/融資使用率/融券使用率用的是「前一營業日」
的數字，不是當日；第一款~第十款的週轉率則是「當日」——兩種都做，且都從
load_margin_short_range()同一份「由舊到新」的歷史裡取，不用分別查詢，避免兩處各自
判斷「前一營業日是哪天」而兜不起來。

全體平均值只涵蓋這524檔（43個官方族群），不是官方定義的全市場~1900檔——disposition_
market_stats.py用bars_1d算的價格類橫斷面統計才是真全市場，這裡的本益比/週轉率/
券資比平均是Phase 2資料收集範圍限制下的近似值，已經在finmind_disposition_
fundamentals_collector.py的docstring寫清楚。第六款要的同類股淨值比平均(pbr_
industry_avg)也是同樣的近似——只在這524檔範圍內依產業分類分組，同類<MIN_INDUSTRY_
PEERS檔的產業不計入，門檻沿用disposition_market_stats.py同一個常數。
"""

from __future__ import annotations

from dataclasses import dataclass

from daily_bars_store import load_daily_bars
from disposition_fundamentals_store import load_fundamentals_day, load_margin_short_range
from disposition_market_stats import MIN_INDUSTRY_PEERS


@dataclass
class StockFundamentals:
    code: str
    pe_ratio: float | None = None
    pbr: float | None = None
    turnover_pct: float | None = None  # 當日週轉率%
    cum_turnover_6d_pct: float | None = None  # 最近6營業日累積週轉率%
    turnover_amount: float | None = None  # 當日成交金額(元)
    shares_outstanding: float | None = None  # 發行股數(市值/收盤價近似)，供disposition_gap_prediction.py第十款反推複用，避免重算
    short_margin_ratio_pct: float | None = None  # 前一營業日券資比%
    margin_usage_pct: float | None = None  # 前一營業日融資使用率%
    short_usage_pct: float | None = None  # 前一營業日融券使用率%
    short_margin_ratio_min_6d_pct: float | None = None  # 最近6營業日(從前一營業日起)最低券資比%


def _shares_outstanding(market_value: float | None, close: float | None) -> float | None:
    if not market_value or not close or close <= 0:
        return None
    return market_value / close


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100


def compute_price_based_fundamentals(
    code: str, *, fundamentals_today: dict | None, daily_bars: list[dict],
) -> StockFundamentals:
    """本益比/淨值比/週轉率(當日+6日累積)——只需要「今天」的基本面快照跟bars_1d的
    收盤/成交量歷史，不涉及日期偏移。daily_bars由load_daily_bars(code)取得，假設
    最後一筆就是要算的trade_date那天(呼叫端負責確保這件事)。"""
    result = StockFundamentals(code=code)
    if not fundamentals_today or not daily_bars:
        return result
    close = daily_bars[-1]["close"]
    volume_lots = daily_bars[-1]["volume"]
    result.pe_ratio = fundamentals_today.get("peRatio")
    result.pbr = fundamentals_today.get("pbr")
    shares = _shares_outstanding(fundamentals_today.get("marketValue"), close)
    if shares is None:
        return result
    result.shares_outstanding = shares
    volume_shares = volume_lots * 1000
    result.turnover_pct = volume_shares / shares * 100
    result.turnover_amount = volume_shares * close
    # 6日累積週轉率：用今天的shares_outstanding近似套用到最近6天的量(股本短期內很少
    # 變動，可接受的近似，不是官方逐日各自的發行股數重算)。
    recent = daily_bars[-6:]
    if len(recent) == 6:
        total_volume_shares = sum(b["volume"] for b in recent) * 1000
        result.cum_turnover_6d_pct = total_volume_shares / shares * 100
    return result


def compute_margin_based_fundamentals(margin_history: list[dict], *, includes_today: bool) -> tuple[
    float | None, float | None, float | None, float | None,
]:
    """回傳(short_margin_ratio_pct, margin_usage_pct, short_usage_pct,
    short_margin_ratio_min_6d_pct)，全部用「前一營業日」起算。margin_history是
    load_margin_short_range()由舊到新的結果；includes_today=True代表history最後
    一筆是今天(要跳過才拿得到前一營業日)，False代表history本身就已經到前一營業日
    為止(呼叫端沒有今天的資料，例如今天還沒收集)。"""
    history_up_to_yesterday = margin_history[:-1] if includes_today else margin_history
    if not history_up_to_yesterday:
        return None, None, None, None
    yesterday = history_up_to_yesterday[-1]
    short_margin_ratio_pct = _ratio(yesterday.get("short_today_balance"), yesterday.get("margin_today_balance"))
    margin_usage_pct = _ratio(yesterday.get("margin_today_balance"), yesterday.get("margin_limit"))
    short_usage_pct = _ratio(yesterday.get("short_today_balance"), yesterday.get("short_limit"))
    ratios_6d = [
        r for row in history_up_to_yesterday
        if (r := _ratio(row.get("short_today_balance"), row.get("margin_today_balance"))) is not None
    ]
    short_margin_ratio_min_6d_pct = min(ratios_6d) if ratios_6d else None
    return short_margin_ratio_pct, margin_usage_pct, short_usage_pct, short_margin_ratio_min_6d_pct


def build_fundamentals_by_code(
    trade_date: str, codes: set[str], *, industry_by_code: dict[str, str] | None = None,
) -> dict[str, dict[str, float | None]]:
    """組出disposition_prediction.build_clause_inputs()要的fundamentals_by_code
    格式：{代號: {欄位: 值}}。industry_by_code是{代號: 產業分類}(來自FinMind
    TaiwanStockInfo，見finmind_broker_branch_collector.fetch_industry_by_code)，
    有給才會算出第六款要的pbr_industry_avg；不給就跟之前一樣全部是None。"""
    fundamentals_today = load_fundamentals_day(trade_date, codes)
    stock_fundamentals: dict[str, StockFundamentals] = {}
    for code in codes:
        daily_bars = load_daily_bars(code, limit=6)
        price_based = compute_price_based_fundamentals(
            code, fundamentals_today=fundamentals_today.get(code), daily_bars=daily_bars,
        )
        margin_history = load_margin_short_range(code, trade_date, days=7)
        includes_today = bool(margin_history) and margin_history[-1].get("trade_date") == trade_date
        short_ratio, margin_usage, short_usage, min_6d = compute_margin_based_fundamentals(
            margin_history, includes_today=includes_today,
        )
        price_based.short_margin_ratio_pct = short_ratio
        price_based.margin_usage_pct = margin_usage
        price_based.short_usage_pct = short_usage
        price_based.short_margin_ratio_min_6d_pct = min_6d
        stock_fundamentals[code] = price_based

    def _peer_avg(values: list[float | None]) -> float | None:
        filtered = [v for v in values if v is not None]
        return sum(filtered) / len(filtered) if filtered else None

    pe_avg = _peer_avg([f.pe_ratio for f in stock_fundamentals.values() if f.pe_ratio is not None and f.pe_ratio > 0])
    pbr_avg = _peer_avg([f.pbr for f in stock_fundamentals.values()])
    turnover_avg = _peer_avg([f.turnover_pct for f in stock_fundamentals.values()])
    cum_turnover_avg = _peer_avg([f.cum_turnover_6d_pct for f in stock_fundamentals.values()])

    pbr_industry_avg_by_industry: dict[str, float] = {}
    if industry_by_code:
        pbr_by_industry: dict[str, list[float]] = {}
        for code, f in stock_fundamentals.items():
            industry = industry_by_code.get(code)
            if industry and f.pbr is not None:
                pbr_by_industry.setdefault(industry, []).append(f.pbr)
        for industry, values in pbr_by_industry.items():
            if len(values) >= MIN_INDUSTRY_PEERS:
                pbr_industry_avg_by_industry[industry] = sum(values) / len(values)

    output: dict[str, dict[str, float | None]] = {}
    for code, f in stock_fundamentals.items():
        industry = (industry_by_code or {}).get(code)
        output[code] = {
            "pe_ratio": f.pe_ratio, "pe_ratio_peer_avg": pe_avg,
            "pbr": f.pbr, "pbr_peer_avg": pbr_avg,
            "pbr_industry_avg": pbr_industry_avg_by_industry.get(industry) if industry else None,
            "turnover_pct": f.turnover_pct, "turnover_pct_peer_avg": turnover_avg,
            "cum_turnover_6d_pct": f.cum_turnover_6d_pct, "cum_turnover_6d_peer_avg_pct": cum_turnover_avg,
            "turnover_amount": f.turnover_amount, "shares_outstanding": f.shares_outstanding,
            "short_margin_ratio_pct": f.short_margin_ratio_pct,
            "margin_usage_pct": f.margin_usage_pct, "short_usage_pct": f.short_usage_pct,
            "short_margin_ratio_min_6d_pct": f.short_margin_ratio_min_6d_pct,
        }
    return output
