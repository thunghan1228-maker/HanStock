"""收盤後從bars_1d(全市場日K，見official_daily_bars.py)算出disposition_rules.py各款
判定需要的「個股自己的數字」跟「全體/同類有價證券平均值」橫斷面統計。

只算得出價格/成交量相關的欄位（第一/二/三/九款的核心條件、第十一款）；週轉率/本益比/
淨值比/券資比等需要發行股數、基本面、融資融券資料的欄位留給呼叫端另外合併（見
disposition_prediction.py），這裡完全不碰那些資料源。

「起迄兩個營業日」是區間頭尾兩個交易日的收盤價（不是區間內的兩日平均），對照官方原文：
「最近三十個營業日（含當日）起迄兩個營業日之收盤價漲跌百分比」＝ (今天收盤－30個營業日
前那天收盤) / 30個營業日前那天收盤。「最近六十個營業日（含當日）之日平均成交量」的平均
本身就含當日，所以volume_ratio_60d是拿當日量除以「含當日在內」那60天的均量，不是除以
前59天不含當日的均量——這是官方原文字面的意思，不是我方便才這樣做。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from database import get_connection, initialize_database

# 90個營業日回溯，日曆天數要留假日/國定假日的餘裕；150天在任何情況下都夠涵蓋90個交易日。
LOOKBACK_CALENDAR_DAYS = 150
MIN_INDUSTRY_PEERS = 5  # 同類有價證券至少要5檔才適用同類股平均值（官方除外情形）


@dataclass
class StockDailySeries:
    code: str
    dates: list[str] = field(default_factory=list)  # 由舊到新
    closes: list[float] = field(default_factory=list)
    opens: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)  # 張


@dataclass
class StockPriceMetrics:
    """單一股票單一交易日、只用bars_1d算得出來的數字（None代表歷史天數不夠）。"""

    code: str
    close: float
    volume: float | None
    change_6d_pct: float | None = None
    price_diff_6d: float | None = None
    change_2d_30d_pct: float | None = None
    change_2d_60d_pct: float | None = None
    change_2d_90d_pct: float | None = None
    close_above_open_ref: bool | None = None
    volume_ratio_60d: float | None = None
    avg_volume_ratio_6d_60d: float | None = None


def load_market_series(
    trade_date: str, *, lookback_days: int = LOOKBACK_CALENDAR_DAYS
) -> dict[str, StockDailySeries]:
    """一次query載入全市場(bars_1d全部股票，不限43族群)最近lookback_days個日曆天內、
    ≤trade_date的每日K，依代號分組、按日期由舊到新排序。"""
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT stock_code, substr(bar_time, 1, 10) AS d, open, close, volume
            FROM bars_1d
            WHERE substr(bar_time, 1, 10) <= ?
              AND substr(bar_time, 1, 10) >= date(?, ?)
            ORDER BY stock_code ASC, d ASC
            """,
            (trade_date, trade_date, f"-{lookback_days} days"),
        ).fetchall()
    by_code: dict[str, StockDailySeries] = {}
    for row in rows:
        series = by_code.setdefault(row["stock_code"], StockDailySeries(code=row["stock_code"]))
        series.dates.append(row["d"])
        series.opens.append(float(row["open"]))
        series.closes.append(float(row["close"]))
        series.volumes.append(float(row["volume"]))
    return by_code


def compute_stock_metrics(series: StockDailySeries) -> StockPriceMetrics | None:
    """單一股票的原始數字；series.dates最後一筆必須就是要算的那個交易日
    （load_market_series已經保證由舊到新排序、且不超過trade_date）。沒有任何一天
    資料就回傳None。"""
    if not series.closes:
        return None
    close = series.closes[-1]
    volume = series.volumes[-1] if series.volumes else None
    metrics = StockPriceMetrics(code=series.code, close=close, volume=volume)

    n = len(series.closes)
    if n >= 6 and series.closes[-6] > 0:
        metrics.change_6d_pct = (close - series.closes[-6]) / series.closes[-6] * 100
        metrics.price_diff_6d = abs(close - series.closes[-6])
    for days, attr in ((30, "change_2d_30d_pct"), (60, "change_2d_60d_pct"), (90, "change_2d_90d_pct")):
        if n >= days and series.closes[-days] > 0:
            setattr(metrics, attr, (close - series.closes[-days]) / series.closes[-days] * 100)
    if series.opens and series.opens[-1] > 0:
        metrics.close_above_open_ref = close > series.opens[-1]
    if n >= 60 and series.volumes:
        window60 = series.volumes[-60:]
        avg60 = sum(window60) / len(window60)
        if avg60 > 0:
            metrics.volume_ratio_60d = (volume or 0.0) / avg60
            window6 = series.volumes[-6:]
            avg6 = sum(window6) / len(window6)
            metrics.avg_volume_ratio_6d_60d = avg6 / avg60
    return metrics


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


@dataclass
class MarketSnapshot:
    trade_date: str
    metrics_by_code: dict[str, StockPriceMetrics]
    # 全體有價證券橫斷面平均值（每個欄位各自獨立平均，只用有值的股票）
    peer_avg: dict[str, float]
    # 同類（industry）橫斷面平均值；同類<MIN_INDUSTRY_PEERS檔的產業不建索引，
    # 呼叫端查不到就代表「不適用同類股規定」。
    industry_avg: dict[str, dict[str, float]]


_METRIC_FIELDS = (
    "change_6d_pct", "change_2d_30d_pct", "change_2d_60d_pct", "change_2d_90d_pct",
    "volume_ratio_60d", "avg_volume_ratio_6d_60d",
)


def _cross_sectional_averages(metrics: Iterable[StockPriceMetrics]) -> dict[str, float]:
    result: dict[str, float] = {}
    metrics = list(metrics)
    for attr in _METRIC_FIELDS:
        avg = _mean(v for m in metrics if (v := getattr(m, attr)) is not None)
        if avg is not None:
            result[attr] = avg
    return result


def build_market_snapshot(
    trade_date: str, *, industry_by_code: dict[str, str] | None = None,
) -> MarketSnapshot:
    """算出全市場(bars_1d涵蓋的全部股票)某一天的橫斷面統計：每檔自己的數字＋全體平均＋
    （如果有給industry_by_code）同類平均。industry_by_code是{代號: 產業分類}，來自
    FinMind TaiwanStockInfo（見disposition_prediction.py怎麼合併使用）；不給就只算
    全體平均，同類股相關的款判定會把industry差幅檢查視為「沒有同類股資料，不擋」
    （對照disposition_rules.py的check_clause_1註解）。"""
    series_by_code = load_market_series(trade_date)
    metrics_by_code: dict[str, StockPriceMetrics] = {}
    for code, series in series_by_code.items():
        if series.dates[-1] != trade_date:
            continue  # 這檔今天沒有資料（停牌／今天還沒寫進bars_1d），不列入橫斷面
        metrics = compute_stock_metrics(series)
        if metrics is not None:
            metrics_by_code[code] = metrics

    peer_avg = _cross_sectional_averages(metrics_by_code.values())

    industry_avg: dict[str, dict[str, float]] = {}
    if industry_by_code:
        grouped: dict[str, list[StockPriceMetrics]] = {}
        for code, metrics in metrics_by_code.items():
            industry = industry_by_code.get(code)
            if industry:
                grouped.setdefault(industry, []).append(metrics)
        for industry, members in grouped.items():
            if len(members) < MIN_INDUSTRY_PEERS:
                continue
            industry_avg[industry] = _cross_sectional_averages(members)

    return MarketSnapshot(
        trade_date=trade_date, metrics_by_code=metrics_by_code,
        peer_avg=peer_avg, industry_avg=industry_avg,
    )
