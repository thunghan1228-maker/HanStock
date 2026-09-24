"""五分鐘K盤中訊號狀態機：905/A8/520/1+2多系列。

規格來源：使用者提供文件《HANSTOCK｜策略定義備份》（整理日期2026-08-13，
以《HanStock 5分鐘K線盤中選股規則》2026/08/04版為底）。範圍限五分鐘盤中
策略；日線Rule1/Rule2不在這裡（使用者已明確指示跳過）。「1+2多」文件裡
沒有寫，使用者後續口頭補充：條件1是905高、條件2是昨日高，5分K收盤同時
站上兩者時成立，一天一次，沒有時間限制（不受長多前置條件約束）。

只認完整收盤的5分鐘K棒，跟主力副圖共用market_data_hub的5分K
aggregator，不新增任何Shioaji訂閱。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from datetime import date

from daily_bars_store import latest_daily_trade_date_before, load_daily_bars
from daytrade_flow import limit_down_price, limit_up_price
from intraday_signal_store import delete_kline_signals_for_ticker, save_intraday_signals
from ma_alignment_score import compute_ma_alignment_score
from market_data_hub import BAR_INTERVAL_5M_MS
from otc_index import taipei_minute_of_day, taipei_trade_date
from stock_bars_5m_store import (
    bars_5m_coverage_complete,
    load_stock_bars_5m_before,
    prune_stock_bars_5m,
    save_stock_bars_5m,
    save_stock_bars_5m_many,
)
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger("hanstock.intraday_kline_signals")

LONG_PRECONDITION_MAX_PCT = 6.0  # 905收盤漲幅需<6%（相對昨收）才適用【5】/20MA上彎系列
CUTOFF_MINUTE = 10 * 60 + 30  # A8空／破905D的期限
FIRST_BAR_MAX_CLOSE_MINUTE = 9 * 60 + 10  # 真正的905K收盤時間=09:05，多留5分鐘緩衝
BLACK_DRAGON_START_MINUTE = 11 * 60  # 創高黑龍11:00後才成立
BLACK_DRAGON_END_MINUTE = 13 * 60 + 30  # 到13:30收盤
BLACK_DRAGON_MIN_MA_SCORE = 10  # 六均線兩兩比較共15組，至少10組排列正確
_LIMIT_EPS = 1e-6

_group_lookup_cache: dict[str, tuple[str, str]] | None = None


def _group_and_name(code: str) -> tuple[str, str]:
    global _group_lookup_cache
    if _group_lookup_cache is None:
        cache: dict[str, tuple[str, str]] = {}
        # 股期標的是特殊清單不是族群、而且排在最後：以前後面的覆蓋前面，一檔股票會被標成「股期標的」，
        # 只在股期標的清單裡的股票（達發、中華電…）也因此算「有族群」而觸發創高黑龍。改成跳過股期標的、
        # 以第一個出現的一般族群為準（跟 main_force_flip_signals 一致）。
        for group_name, stocks in STOCK_GROUPS.items():
            if group_name in SPECIAL_GROUP_NAMES:
                continue
            for stock_code, stock_name in stocks:
                cache.setdefault(str(stock_code).upper(), (group_name, stock_name))
        _group_lookup_cache = cache
    return _group_lookup_cache.get(code, ("", code))


@dataclass
class _KlineState:
    trade_date: str = ""
    bar_count: int = 0
    closes: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    prev_close: float | None = None
    prev_high: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    limit_hit: bool = False
    bar905_high: float | None = None
    bar905_low: float | None = None
    a8: float | None = None
    long_ok: bool = False
    above_20ma: bool | None = None
    above_prev_high: bool = False
    above_905_5ma: bool = False
    ma20_slope: str | None = None
    prev_ma20: float | None = None
    in_520_multi: bool = False
    in_520_short: bool = False
    count_905: int = 0
    count_prev_high: int = 0
    count_20up: int = 0
    count_20down: int = 0
    fired_first_905: bool = False
    fired_first_20up: bool = False
    fired_first_20down: bool = False
    fired_combo12_bull: bool = False
    fired_a8short: bool = False
    fired_break905d: bool = False
    today_open: float | None = None
    five_day_high: float | None = None
    ma_alignment_score: int | None = None
    fired_black_dragon: bool = False
    late_subscription: bool = False
    seed_count: int = 0  # 昨天種進來的 5 分 K 根數（MA20 跨日接續用），0 = 沒種


def _moving_average(closes: list[float], length: int) -> float | None:
    if len(closes) < length:
        return None
    window = closes[-length:]
    return sum(window) / length


# 「昨日」日K最多能比今天舊幾個日曆天（連假最多也就這麼長）；再舊就是資料沒跟上，不能當昨高。
MAX_PREV_BAR_AGE_DAYS = 12
# 跨日接續 5 分 K MA20 用的種子：MA20 要 20 根、斜率再多 1 根。
SEED_BARS = 21
_market_prev_cache: dict[str, str | None] = {}


def _market_previous_trade_date(trade_date: str) -> str | None:
    if trade_date not in _market_prev_cache:
        try:
            value = latest_daily_trade_date_before(trade_date)
        except Exception:  # noqa: BLE001
            value = None
        _market_prev_cache.clear()
        _market_prev_cache[trade_date] = value
    return _market_prev_cache[trade_date]


def previous_day_bars(code: str, trade_date: str) -> list[dict[str, Any]]:
    """trade_date 之前的日K（最多 5 根、舊到新）。最後一根一定要是市場上一個交易日的那根：
    2026-09-23 的 6218（上櫃）就是因為櫃買來源從 Railway 出去被擋、日K停在更早的日子，
    拿更早那天的高點當「昨日高」，09:10 明明沒過昨高卻發了 1+2多。日K沒跟上就當作沒有昨日資料，
    寧可少發也不要發錯。"""
    try:
        bars = load_daily_bars(code, limit=6)
    except Exception:  # noqa: BLE001
        return []
    prior = [b for b in bars if str(b.get("ts", ""))[:10] < trade_date]
    if not prior:
        return []
    last_date = str(prior[-1].get("ts", ""))[:10]
    if last_date:
        market_prev = _market_previous_trade_date(trade_date)
        if market_prev and last_date < market_prev:
            return []
        try:
            age_days = (date.fromisoformat(trade_date[:10]) - date.fromisoformat(last_date)).days
        except ValueError:
            age_days = 0
        if age_days > MAX_PREV_BAR_AGE_DAYS:
            return []
    return prior[-5:]


def previous_day_bars_5m(
    code: str, trade_date: str, seed_bars: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """trade_date 之前、最後 SEED_BARS 根 5 分 K（舊到新、只留 ts/close/low），給 MA20 跨日接續用。
    seed_bars 有給就直接用（收盤後回補用 Shioaji kbars 拿到的前幾天 bars），沒有就從本機 bars_5m 撈。
    最後一根一定要是市場上一個交易日的（日K知道上一個交易日的話），太舊就當沒有：
    寧可 MA20 晚一點才算得出來，也不要拿上週的 K 棒接到今天後面。"""
    bars = list(seed_bars or [])
    if not bars:
        try:
            bars = load_stock_bars_5m_before(code, trade_date, SEED_BARS)
        except Exception:  # noqa: BLE001
            logger.warning("撈昨天5分K種子失敗 code=%s", code, exc_info=True)
            return []
    prior: list[dict[str, Any]] = []
    for bar in bars:
        try:
            ts = int(bar["ts"])
            close = float(bar["close"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0 or taipei_trade_date(ts) >= trade_date:
            continue
        prior.append({"ts": ts, "close": close, "low": low})
    prior.sort(key=lambda b: b["ts"])
    prior = prior[-SEED_BARS:]
    if not prior:
        return []
    last_date = taipei_trade_date(prior[-1]["ts"])
    market_prev = _market_previous_trade_date(trade_date)
    if market_prev and last_date < market_prev:
        return []
    try:
        age_days = (date.fromisoformat(trade_date[:10]) - date.fromisoformat(last_date)).days
    except ValueError:
        age_days = 0
    if age_days > MAX_PREV_BAR_AGE_DAYS:
        return []
    return prior


def _sync_ma_state(state: _KlineState) -> None:
    """靜默更新 MA 衍生的「目前狀態」（不發訊號）：MA20 值與斜率、收盤在 20MA 上或下、
    五二零多／空。種子種進來時、以及有種子的當天第一根都會呼叫，讓第二根起偵測到的
    跨越／轉彎是相對於昨天連續下來的真實狀態，而不是相對於「今天才開始算」的假 MA。"""
    if not state.closes:
        return
    close = state.closes[-1]
    ma5 = _moving_average(state.closes, 5)
    ma20 = _moving_average(state.closes, 20)
    if ma20 is None:
        return
    if state.prev_ma20 is not None and ma20 != state.prev_ma20:
        state.ma20_slope = "up" if ma20 > state.prev_ma20 else "down"
    state.prev_ma20 = ma20
    state.above_20ma = close > ma20
    if ma5 is not None:
        state.in_520_multi = close > ma5 and close > ma20
        state.in_520_short = close < ma5 and close < ma20


# 即時路徑每根走完的 5 分 K 先排隊，累積到一定數量或隔一段時間再一次寫進 bars_5m：
# 一百多檔同時走完一根 K，不要在 tick 執行緒上每檔各開一次交易。
PERSIST_FLUSH_SECONDS = 30.0
PERSIST_FLUSH_MAX_BARS = 300
_pending_bars_5m: dict[str, list[dict[str, Any]]] = {}
_pending_bars_count = 0
_pending_bars_lock = threading.Lock()
_pending_last_flush = time.monotonic()
_last_reset_trade_date: str | None = None


def queue_bar_5m(code: str, bar: dict[str, Any]) -> None:
    global _pending_bars_count
    with _pending_bars_lock:
        _pending_bars_5m.setdefault(code, []).append(bar)
        _pending_bars_count += 1


def flush_pending_bars_5m(*, force: bool = False) -> int:
    """把排隊中的 5 分 K 寫進 bars_5m；回傳寫了幾根。"""
    global _pending_bars_count, _pending_last_flush
    with _pending_bars_lock:
        due = force or _pending_bars_count >= PERSIST_FLUSH_MAX_BARS or (
            _pending_bars_count > 0 and time.monotonic() - _pending_last_flush >= PERSIST_FLUSH_SECONDS
        )
        if not due:
            return 0
        batch = dict(_pending_bars_5m)
        _pending_bars_5m.clear()
        _pending_bars_count = 0
        _pending_last_flush = time.monotonic()
    if not batch:
        return 0
    try:
        return save_stock_bars_5m_many(batch)
    except Exception:  # noqa: BLE001
        logger.exception("五分鐘K寫入bars_5m失敗 codes=%d", len(batch))
        return 0


class IntradayKlineSignalMonitor:
    def __init__(self) -> None:
        self._states: dict[str, _KlineState] = {}
        self._lock = threading.Lock()

    def _build_fresh_state(
        self, code: str, trade_date: str, seed_bars: list[dict[str, Any]] | None = None,
    ) -> _KlineState:
        """建一個全新、獨立的當日狀態（不寫進 self._states）：_reset_for_new_day 用它建立正式
        的每日狀態，跟正式路徑共用同一份建構邏輯，不會各自維護一份容易兜不起來的複本。"""
        state = _KlineState(trade_date=trade_date)
        prior = previous_day_bars(code, trade_date)
        if prior:
            state.prev_close = float(prior[-1]["close"])
            state.prev_high = float(prior[-1]["high"])
            state.limit_up = limit_up_price(state.prev_close)
            state.limit_down = limit_down_price(state.prev_close)
        if len(prior) >= 5:
            state.five_day_high = max(float(b["high"]) for b in prior[-5:])
        try:
            state.ma_alignment_score = compute_ma_alignment_score(code)
        except Exception:  # noqa: BLE001
            state.ma_alignment_score = None
        seeds = previous_day_bars_5m(code, trade_date, seed_bars)
        if seeds:
            # 昨天最後幾根 5 分 K 先放進 closes/lows（今天的 K 棒接在後面），MA20 從今天
            # 第一根起就是跨日連續的看盤軟體算法；種子本身不發任何訊號。
            state.closes = [b["close"] for b in seeds]
            state.lows = [b["low"] for b in seeds]
            state.seed_count = len(seeds)
            state.prev_ma20 = _moving_average(state.closes[:-1], 20)
            _sync_ma_state(state)
        return state

    def _reset_for_new_day(
        self, code: str, trade_date: str, seed_bars: list[dict[str, Any]] | None = None,
    ) -> _KlineState:
        state = self._build_fresh_state(code, trade_date, seed_bars)
        self._states[code] = state
        return state

    def reset_for_backfill(
        self, code: str, trade_date: str, seed_bars: list[dict[str, Any]] | None = None,
    ) -> None:
        """歷史回補用：強制重建這檔股票在trade_date當天的狀態，避免重複
        呼叫回補（例如重試）時，因為state.trade_date沒變而誤判成「同一天
        繼續累積」，導致bar_count/closes等狀態疊加成兩天份、算出錯誤結果。
        每次回補一檔股票的完整當日bars之前，都要先呼叫這個。"""
        code = str(code).strip().upper()
        with self._lock:
            self._reset_for_new_day(code, trade_date, seed_bars)

    def on_bar_completed(
        self, code: str, bar: dict[str, Any], *, persist: bool = True,
    ) -> list[dict[str, Any]]:
        """persist=True（即時路徑）會把這根 K 排進 bars_5m 寫入佇列，當明天 MA20 的種子；
        收盤後回補自己整批存檔，傳 persist=False。"""
        global _last_reset_trade_date
        code = str(code).strip().upper()
        close_ts = int(bar["ts"]) + BAR_INTERVAL_5M_MS
        trade_date = taipei_trade_date(close_ts)
        minute_of_day = taipei_minute_of_day(close_ts)

        if persist and trade_date != _last_reset_trade_date:
            # 新的一天第一根（整個程序一天一次）：昨天收盤前還排隊中的 K 棒先寫進去，
            # 接下來每檔重置時撈種子才撈得到。
            _last_reset_trade_date = trade_date
            flush_pending_bars_5m(force=True)

        with self._lock:
            state = self._states.get(code)
            if state is None or state.trade_date != trade_date:
                state = self._reset_for_new_day(code, trade_date)
            signals = self._process_bar(code, state, bar, close_ts, trade_date, minute_of_day)

        if persist:
            queue_bar_5m(code, bar)
            flush_pending_bars_5m()

        if signals:
            try:
                save_intraday_signals(signals)
            except Exception:  # noqa: BLE001
                logger.exception("五分鐘K訊號保存失敗 code=%s", code)
        return signals

    def _process_bar(
        self,
        code: str,
        state: _KlineState,
        bar: dict[str, Any],
        close_ts: int,
        trade_date: str,
        minute_of_day: int,
    ) -> list[dict[str, Any]]:
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])

        if state.limit_hit:
            return []
        if (
            (state.limit_up is not None and close >= state.limit_up - _LIMIT_EPS)
            or (state.limit_down is not None and close <= state.limit_down + _LIMIT_EPS)
        ):
            # 漲停/跌停鎖死後，連續好幾根K棒收盤價完全相同，MA5/MA20會被這些
            # 沒有實際價格發現意義的平盤K棒拖著「追上」，跨越MA只是算術上的
            # 假象，不是真正的多空轉折。當天鎖住後就不再偵測任何5分鐘K訊號。
            state.limit_hit = True
            return []

        state.bar_count += 1
        state.closes.append(close)
        state.lows.append(low)

        group_name, name = _group_and_name(code)
        out: list[dict[str, Any]] = []

        def emit(kind: str, label: str, note: str = "", ma20_down: bool | None = None) -> None:
            out.append({
                "tradeDate": trade_date, "ticker": code, "name": name, "groupName": group_name,
                "kind": kind, "label": label, "barTs": close_ts, "price": close,
                "note": note, "ma20Down": ma20_down,
            })

        if state.bar_count == 1:
            # 這根理論上是當天第一根905K（09:00-09:05），只用來建立基準，
            # 不偵測訊號。但即時路徑的股票訂閱是動態、有上限的，某檔股票
            # 如果比較晚才被訂閱到，這裡收到的「第一根」其實是當天較晚
            # 才完整走完的某根K棒，不是真正的09:00-09:05──用它當905高/
            # 昨日高combo的基準會產生假訊號(跟backfill_today_kline_signals
            # 文件字串描述的是同一個根因)。這裡只用bar自己的收盤時間驗證，
            # 不合理就不建立基準，讓後面所有偵測函式(都已經對905高/a8/
            # today_open是None做防呆)自然整天跳過這檔股票，
            # 寧可當天沒訊號、也不要給錯的訊號；正確結果要靠收盤後的
            # backfill_today_kline_signals用歷史kbars重建。
            if state.seed_count:
                # 有昨天的種子時，MA 衍生狀態（20MA 上下／五二零／斜率）跟著第一根靜默更新，
                # 第二根起偵測到的才是真的跨越；第一根本身照舊不發訊號。
                _sync_ma_state(state)
            if minute_of_day > FIRST_BAR_MAX_CLOSE_MINUTE:
                state.late_subscription = True
                return out
            state.bar905_high = high
            state.bar905_low = low
            state.a8 = (state.bar905_high + state.bar905_low) / 2
            state.today_open = float(bar["open"])
            if state.prev_close is not None and state.prev_close > 0:
                pct = (close / state.prev_close - 1) * 100
                state.long_ok = close > state.prev_close and pct < LONG_PRECONDITION_MAX_PCT
            return out

        ma5 = _moving_average(state.closes, 5)
        ma20 = _moving_average(state.closes, 20)

        self._detect_905_cross(state, close, emit)
        self._detect_prev_high_cross(state, close, emit)
        self._detect_combo12_bull(state, close, emit)
        self._detect_20ma_cross(state, close, ma20, emit)
        self._detect_520(state, close, ma5, ma20, emit)
        self._detect_20ma_turn(state, ma20, emit)
        self._detect_a8_and_905d(state, close, minute_of_day, emit)
        bar_start_minute = taipei_minute_of_day(int(bar["ts"]))
        self._detect_black_dragon(state, close, high, bar_start_minute, group_name, emit)

        # 【5】需要ma5，跟其他偵測分開放在最後（依賴上面算好的ma5）。
        self._detect_5ma_905_cross(state, close, ma5, emit)

        return out

    def _detect_5ma_905_cross(self, state: _KlineState, close: float, ma5: float | None, emit) -> None:
        if state.bar905_high is None:
            return
        if state.above_905_5ma and close <= state.bar905_high:
            state.above_905_5ma = False
        if (
            state.long_ok and ma5 is not None and close > state.bar905_high
            and close > ma5 and not state.above_905_5ma
        ):
            state.count_905 += 1
            state.above_905_5ma = True
            emit("crossUp905", "第%d次站上5MA過905高" % state.count_905, f"第{state.count_905}次")

    def _detect_905_cross(self, state: _KlineState, close: float, emit) -> None:
        if state.bar905_high is None or state.fired_first_905:
            return
        if close > state.bar905_high:
            state.fired_first_905 = True
            emit("firstCross905High", "首次過905高")

    def _detect_prev_high_cross(self, state: _KlineState, close: float, emit) -> None:
        if state.prev_high is None:
            return
        if state.above_prev_high and close <= state.prev_high:
            state.above_prev_high = False
        if close > state.prev_high and not state.above_prev_high:
            state.count_prev_high += 1
            state.above_prev_high = True
            emit("crossUpPrevHigh", "第%d次站上昨日高" % state.count_prev_high, f"第{state.count_prev_high}次")

    def _detect_combo12_bull(self, state: _KlineState, close: float, emit) -> None:
        # 「1+2多」＝5分K收盤同時站上905高(條件1)跟昨日高(條件2)；不受長多
        # 前置條件限制（跟⑨/㊇一樣），一天只發一次，沒有10:30這種時間限制
        # （使用者說明：實務上大部分會在10:30前出現，但不是規則本身要求的）。
        if state.fired_combo12_bull or state.bar905_high is None or state.prev_high is None:
            return
        if close > state.bar905_high and close > state.prev_high:
            state.fired_combo12_bull = True
            emit("combo12Bull", "1+2多")

    def _detect_20ma_cross(self, state: _KlineState, close: float, ma20: float | None, emit) -> None:
        if ma20 is None:
            return
        is_above = close > ma20
        if state.above_20ma is None:
            state.above_20ma = is_above
            return
        if not state.above_20ma and is_above:
            if state.long_ok:
                state.count_20up += 1
                emit("crossUp20ma", "第%d次站上20MA" % state.count_20up, f"第{state.count_20up}次")
                if not state.fired_first_20up:
                    state.fired_first_20up = True
                    emit("firstCrossUp20ma", "首次站上20MA")
        elif state.above_20ma and not is_above:
            state.count_20down += 1
            emit("crossDown20ma", "第%d次跌破20MA" % state.count_20down, f"第{state.count_20down}次")
            if not state.fired_first_20down:
                state.fired_first_20down = True
                emit("firstCrossDown20ma", "首次跌破20MA")
        state.above_20ma = is_above

    def _detect_520(self, state: _KlineState, close: float, ma5: float | None, ma20: float | None, emit) -> None:
        if ma5 is None or ma20 is None:
            return
        is_multi = close > ma5 and close > ma20
        if is_multi and not state.in_520_multi:
            emit("ma520Up", "五二零上")
        state.in_520_multi = is_multi
        is_short = close < ma5 and close < ma20
        if is_short and not state.in_520_short:
            emit("ma520Down", "五二零下")
        state.in_520_short = is_short

    def _detect_20ma_turn(self, state: _KlineState, ma20: float | None, emit) -> None:
        if ma20 is None:
            return
        if state.prev_ma20 is not None:
            if ma20 > state.prev_ma20:
                new_slope = "up"
            elif ma20 < state.prev_ma20:
                new_slope = "down"
            else:
                new_slope = state.ma20_slope
            if state.ma20_slope is not None and new_slope in ("up", "down") and new_slope != state.ma20_slope:
                if new_slope == "up":
                    emit("ma20turnUp", "20MA轉上彎")
                else:
                    emit("ma20turnDown", "20MA轉下彎")
            state.ma20_slope = new_slope
        state.prev_ma20 = ma20

    def _detect_a8_and_905d(self, state: _KlineState, close: float, minute_of_day: int, emit) -> None:
        if minute_of_day >= CUTOFF_MINUTE:
            return
        if not state.fired_a8short and state.a8 is not None and close < state.a8:
            state.fired_a8short = True
            emit("a8short", "A8空")
        if not state.fired_break905d and state.bar905_low is not None and close < state.bar905_low:
            state.fired_break905d = True
            emit("break905d", "破905D")

    def _detect_black_dragon(
        self, state: _KlineState, close: float, high: float, bar_start_minute: int, group_name: str, emit
    ) -> None:
        """創高黑龍(盤中版)：11:00~13:30限定(用「這根5分K自己的起始時間」
        判斷，10:55-11:00這根收盤時間剛好=11:00但起始在11:00前，不算)，
        同一根5分K自己的最高價突破前5個完整交易日最高價(平高不算)、該根
        收盤<今日09:00開盤價、六均線(5/10/20/60/120/240)排列分數≥10
        (滿分15)，一天一次。股票要屬於HanStock正式主族群範圍(group_name
        非空)。"""
        if state.fired_black_dragon:
            return
        if not (BLACK_DRAGON_START_MINUTE <= bar_start_minute <= BLACK_DRAGON_END_MINUTE):
            return
        if not group_name:
            return
        if (
            state.today_open is None or state.five_day_high is None
            or state.ma_alignment_score is None or state.ma_alignment_score < BLACK_DRAGON_MIN_MA_SCORE
        ):
            return
        if high > state.five_day_high and close < state.today_open:
            emit("blackDragon", "創高黑龍")
            state.fired_black_dragon = True


_monitor: IntradayKlineSignalMonitor | None = None
_monitor_lock = threading.Lock()


def get_intraday_kline_signal_monitor() -> IntradayKlineSignalMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = IntradayKlineSignalMonitor()
    return _monitor


def backfill_today_kline_signals(
    *, service: Any = None, hub: Any = None, trade_date: str | None = None, delay: float = 0.3,
) -> dict[str, Any]:
    """一次性回補：用Shioaji歷史kbars重播trade_date當天已經走完的5分K，
    補回「偵測引擎當天收盤後才上線」這段時間本來會漏掉的訊號。

    回補範圍是STOCK_GROUPS全部股票（前端K線圖搜尋得到的完整清單，
    約660多檔）。原本只挑main_force_bars今天剛好有資料的股票，覆蓋率
    不夠：主力副圖收集器今天沒追蹤到的股票會整檔被跳過（即使它明顯
    有觸發訊號的走勢），跟使用者任意打開一檔股票圖表就期待看到訊號的
    需求不符。

    重播前會先刪掉這檔股票trade_date當天既有的K線訊號紀錄（只刪這個
    家族，不影響大單/四項精選等其他家族），再用歷史kbars重新算過、
    整批寫入。這是刻意設計成「覆蓋」而不是單純「跳過重複」：即時路徑
    的股票訂閱是動態、有上限的（一次最多190檔），某檔股票如果比較晚
    才被訂閱到，它在即時路徑上第一次真正開始被偵測的那根bar會被誤判
    成「當天第一根905K」，導致當天最早的訊號時間算錯、被存成一個偏晚
    的錯誤時間。歷史kbars不受即時訂閱時機影響，永遠看得到當天完整的
    09:00起走勢，用它重算才是準的；只有ONCE_PER_DAY_KINDS去重、不覆蓋
    的話，這筆錯的舊紀錄會一直卡住，回補等於白做。只有當這次確實抓到
    今天的bars時才會刪除重寫，避免用一次抓資料失敗/沒資料的結果把既有
    正確資料整個清空又補不回來。

    524檔逐檔打歷史kbars很吃永豐的歷史流量額度（2026-09-23實測一天500MB額度
    在收盤前就會用完），使用者要求降低「全市場都掃一遍」的頻率。頻率本身沒得降
    ——每天都要跑，訊號才不會整天停在錯的基準；能降的是規模：這裡先看
    bars_5m_coverage_complete，這檔今天從開盤到收盤最後一根都連續追到了（即時
    路徑沒有晚訂閱、沒有缺根）就直接跳過，不打任何外部API；只有真的有缺口
    （晚訂閱、中途斷線、完全沒追蹤到）的股票才重抓歷史kbars。大多數股票多數
    交易日即時路徑其實整天都追得到，實際會重抓的通常是一小部分。"""
    from datetime import datetime

    from otc_index import TW_TZ
    from stock_history_service import get_stock_history_bars_5m

    trade_date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    # 只重算 43 個一般族群的股票（2026-09-24 使用者：不在族群裡的不要掃，浪費 Shioaji 歷史額度）
    codes = sorted({
        str(code).strip().upper()
        for name, stocks in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES
        for code, _ in stocks
    })
    monitor = get_intraday_kline_signal_monitor()
    processed = 0
    bars_replayed = 0
    signals_emitted = 0
    bars_stored = 0
    codes_skipped_complete = 0
    failures: list[dict[str, str]] = []
    flush_pending_bars_5m(force=True)
    for code in codes:
        try:
            if bars_5m_coverage_complete(code, trade_date):
                # 即時路徑今天從開盤到收盤最後一根都連續追到了（沒有晚訂閱、沒有缺根），
                # 當天即時算出的訊號本來就是對的，不用再打一次歷史 kbars 重抓／重播一遍——
                # 全市場524檔逐檔重抓很吃永豐的歷史流量額度，只有真的有缺口的股票才需要。
                # 完整的不用打任何外部 API，不需要延遲；跳過的檔數越多，這一輪收盤後校正
                # 整體要花的時間跟吃掉的額度也跟著降低。
                codes_skipped_complete += 1
                processed += 1
                continue
            # priority=backfill：永豐額度留給收盤後校正的那一份也可以用（開圖等即時需求剩不到保留額度就走備援）
            result = get_stock_history_bars_5m(code, calendar_days=3, service=service, hub=hub, priority="backfill")
            all_bars = sorted(result.get("bars", []), key=lambda b: int(b["ts"]))
            todays_bars = [b for b in all_bars if taipei_trade_date(int(b["ts"])) == trade_date]
            # kbars 裡 trade_date 之前那幾天的 K 棒直接當 MA20 種子（比本機 bars_5m 更不依賴
            # 前一天有沒有存到）；週一 calendar_days=3 抓不到上週五時，reset 會退回本機資料。
            prior_bars = [b for b in all_bars if taipei_trade_date(int(b["ts"])) < trade_date]
            if todays_bars:
                delete_kline_signals_for_ticker(trade_date, code)
            monitor.reset_for_backfill(code, trade_date, seed_bars=prior_bars)
            for bar in todays_bars:
                emitted = monitor.on_bar_completed(code, bar, persist=False)
                bars_replayed += 1
                signals_emitted += len(emitted)
            # 這幾天的 5 分 K 一併存進 bars_5m：明天開盤 MA20 的種子就齊了，即時路徑
            # 沒訂閱到的股票也有。
            try:
                bars_stored += save_stock_bars_5m(code, all_bars)
            except Exception:  # noqa: BLE001
                logger.warning("五分鐘K存檔失敗 code=%s", code, exc_info=True)
            processed += 1
        except Exception as error:  # noqa: BLE001
            failures.append({"code": code, "error": str(error)})
            logger.exception("五分鐘K訊號回補失敗 code=%s", code)
        time.sleep(max(0.0, delay))
    try:
        bars_pruned = prune_stock_bars_5m()
    except Exception:  # noqa: BLE001
        logger.warning("清除舊的個股5分K失敗", exc_info=True)
        bars_pruned = 0
    return {
        "tradeDate": trade_date,
        "codeCount": len(codes),
        "codesProcessed": processed,
        "codesSkippedComplete": codes_skipped_complete,
        "barsReplayed": bars_replayed,
        "signalsEmitted": signals_emitted,
        "barsStored": bars_stored,
        "barsPruned": bars_pruned,
        "failures": failures,
    }


_backfill_status: dict[str, Any] = {"running": False, "result": None}
_backfill_status_lock = threading.Lock()


def kline_signal_backfill_status() -> dict[str, Any]:
    with _backfill_status_lock:
        return dict(_backfill_status)


def start_kline_signal_backfill_today(trade_date: str | None = None) -> dict[str, Any]:
    """背景執行緒觸發一次性回補；已經在跑就不會重複啟動。trade_date預設
    今天，也可以指定過去幾天內的日期(例如週末想驗證週五的資料)——只要
    在Shioaji歷史kbars查詢範圍內(目前呼叫端calendar_days=3天)就抓得到。"""
    with _backfill_status_lock:
        if _backfill_status["running"]:
            return {"started": False, "reason": "already_running"}
        _backfill_status["running"] = True
        _backfill_status["result"] = None

    def _run() -> None:
        try:
            result = backfill_today_kline_signals(trade_date=trade_date)
        except Exception as error:  # noqa: BLE001
            result = {"error": str(error)}
            logger.exception("五分鐘K訊號回補整體失敗")
        with _backfill_status_lock:
            _backfill_status["running"] = False
            _backfill_status["result"] = result

    threading.Thread(target=_run, name="hanstock-kline-signal-backfill", daemon=True).start()
    return {"started": True}
