"""HanStock Market Data Hub — 即時行情核心。

統一行情資料源：Shioaji Tick → 記憶體快取 → 1 分／5 分 K 聚合 → WebSocket 廣播。
所有消費者（Rule1、Rule2、905 戰法、LINE Bot、族群排行、K 線、警示）
都從此核心取得資料，不需要每增加一個功能就重新串一次 Shioaji。

架構：
  Shioaji ↔ QuoteService（既有）
       ↓ tick callback
  MarketDataHub（本模組）
       ├── Tick Cache（最新一筆 per code）
       ├── Bar Aggregator（1 分／5 分 K 即時聚合）
       ├── WebSocket Broadcaster（即時推送到前端/其他服務）
       └── REST Query（查詢與備援）
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("hanstock.market_data_hub")

TW_TZ = timezone(timedelta(hours=8))

# K 棒聚合間隔（毫秒）
BAR_INTERVAL_1M_MS = 60 * 1000
BAR_INTERVAL_5M_MS = 5 * 60 * 1000
# 向下相容：既有程式若引用 BAR_INTERVAL_MS，仍代表 5 分 K。
BAR_INTERVAL_MS = BAR_INTERVAL_5M_MS

# 盤中主力進出：單筆成交達任一門檻即列入主力大單。
# 股票 volume 依目前 Hub 規格為「張」，amount 為該筆成交金額（元）。
MAIN_FORCE_MIN_LOTS = max(1, int(os.getenv("HANSTOCK_MAIN_FORCE_MIN_LOTS", "20")))
MAIN_FORCE_MIN_AMOUNT = max(1.0, float(os.getenv("HANSTOCK_MAIN_FORCE_MIN_AMOUNT", "1000000")))


def _bar_start_ms(ts_ms: int, interval_ms: int = BAR_INTERVAL_5M_MS) -> int:
    """將 timestamp (ms) 對齊到指定 K 棒週期起始點。"""
    if interval_ms <= 0:
        raise ValueError("interval_ms 必須大於 0")
    return ts_ms - (ts_ms % interval_ms)


def _now_tw() -> datetime:
    return datetime.now(TW_TZ)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _trade_side(tick_data: dict[str, Any]) -> str:
    """Shioaji TickType：1=外盤買進、2=內盤賣出、其餘=中性。"""
    tick_type = tick_data.get("tick_type")
    if tick_type == 1:
        return "buy"
    if tick_type == 2:
        return "sell"
    return "neutral"


def _is_main_force_trade(tick_data: dict[str, Any], *, futures: bool = False) -> bool:
    """以單筆張數或成交金額辨識大單；門檻可由環境變數調整。"""
    try:
        volume = max(0, int(tick_data.get("volume", 0) or 0))
    except (TypeError, ValueError):
        volume = 0
    if volume >= MAIN_FORCE_MIN_LOTS:
        return True
    if futures:
        return False
    try:
        amount = float(tick_data.get("amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        try:
            amount = float(tick_data.get("close", 0) or 0) * volume * 1000
        except (TypeError, ValueError):
            amount = 0.0
    return amount >= MAIN_FORCE_MIN_AMOUNT


@dataclass(slots=True)
class Bar:
    """通用 OHLCV K 棒。"""
    ts: int  # bar 起始時間 (UTC ms)
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0
    buy_volume: int = 0
    sell_volume: int = 0
    neutral_volume: int = 0
    main_buy_volume: int = 0
    main_sell_volume: int = 0
    main_buy_amount: float = 0.0
    main_sell_amount: float = 0.0
    main_tick_count: int = 0

    def update(
        self,
        price: float,
        volume: int = 0,
        side: str = "neutral",
        is_main_force: bool = False,
        amount: float = 0.0,
    ) -> None:
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        if side == "buy":
            self.buy_volume += volume
            if is_main_force:
                self.main_buy_volume += volume
                self.main_buy_amount += max(0.0, amount)
        elif side == "sell":
            self.sell_volume += volume
            if is_main_force:
                self.main_sell_volume += volume
                self.main_sell_amount += max(0.0, amount)
        else:
            self.neutral_volume += volume
        if is_main_force and side in ("buy", "sell"):
            self.main_tick_count += 1
        self.tick_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "tick_count": self.tick_count,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "neutral_volume": self.neutral_volume,
            "main_buy_volume": self.main_buy_volume,
            "main_sell_volume": self.main_sell_volume,
            "main_net_volume": self.main_buy_volume - self.main_sell_volume,
            "main_buy_amount": round(self.main_buy_amount),
            "main_sell_amount": round(self.main_sell_amount),
            "main_net_amount": round(self.main_buy_amount - self.main_sell_amount),
            "main_tick_count": self.main_tick_count,
            # 即時 bar 與歷史 tick 回補使用同一份資料契約。前端會以此旗標
            # 判斷是否建立盤中主力副圖；缺少旗標時即使淨量已有數字也不會畫。
            "main_force_available": True,
        }


# 向下相容：保留舊類別名稱。
Bar5m = Bar


class BarAggregator:
    """Per-code 即時 K 棒聚合器。保留當日所有已完成 bar + 當前進行中 bar。"""

    def __init__(self, interval_ms: int = BAR_INTERVAL_5M_MS, interval_name: str = "5m") -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms 必須大於 0")
        self.interval_ms = interval_ms
        self.interval_name = interval_name
        self._lock = threading.RLock()
        # code → list of completed bars (today)
        self._completed: dict[str, list[Bar]] = defaultdict(list)
        # code → current (incomplete) bar
        self._current: dict[str, Bar] = {}
        # 當日日期（台北），用於跨日清理
        self._trade_date: str = _now_tw().strftime("%Y-%m-%d")

    def on_tick(
        self,
        code: str,
        price: float,
        volume: int,
        tick_ts_ms: int,
        side: str = "neutral",
        is_main_force: bool = False,
        amount: float = 0.0,
    ) -> Optional[Bar]:
        """收到 tick 時更新 bar。若跨 bar 則回傳剛完成的 bar，否則回傳 None。"""
        self._check_day_rollover()
        bar_start = _bar_start_ms(tick_ts_ms, self.interval_ms)
        completed_bar: Optional[Bar] = None

        with self._lock:
            current = self._current.get(code)
            # 忽略比目前 K 棒更早的延遲 tick，避免時間倒退與 K 棒順序錯亂。
            if current is not None and bar_start < current.ts:
                logger.debug(
                    "[%s] 忽略延遲 tick: code=%s tick_bar=%s current_bar=%s",
                    self.interval_name,
                    code,
                    bar_start,
                    current.ts,
                )
                return None

            if current is None or current.ts != bar_start:
                # 新 bar 開始
                if current is not None and current.tick_count > 0:
                    self._completed[code].append(current)
                    completed_bar = current
                self._current[code] = Bar(ts=bar_start, open=price, high=price, low=price, close=price)
                self._current[code].update(price, volume, side, is_main_force, amount)
            else:
                current.update(price, volume, side, is_main_force, amount)

        return completed_bar

    def get_bars(self, code: str, include_current: bool = True) -> list[dict[str, Any]]:
        """取得指定股票今日所有 K 棒（含或不含進行中 bar）。"""
        with self._lock:
            bars = [b.to_dict() for b in self._completed.get(code, [])]
            if include_current:
                current = self._current.get(code)
                if current and current.tick_count > 0:
                    bars.append(current.to_dict())
        return bars

    def get_current_bar(self, code: str) -> Optional[dict[str, Any]]:
        """取得進行中的 bar。"""
        with self._lock:
            current = self._current.get(code)
            if current and current.tick_count > 0:
                return current.to_dict()
        return None

    def get_all_latest(self) -> dict[str, dict[str, Any]]:
        """取得所有 code 的最新 bar（進行中優先，否則最後完成的）。"""
        result: dict[str, dict[str, Any]] = {}
        with self._lock:
            for code in set(list(self._completed.keys()) + list(self._current.keys())):
                current = self._current.get(code)
                if current and current.tick_count > 0:
                    result[code] = current.to_dict()
                elif code in self._completed and self._completed[code]:
                    result[code] = self._completed[code][-1].to_dict()
        return result

    def _check_day_rollover(self) -> None:
        today = _now_tw().strftime("%Y-%m-%d")
        if today != self._trade_date:
            with self._lock:
                self._completed.clear()
                self._current.clear()
                self._trade_date = today


class MarketDataHub:
    """統一即時行情核心。

    接收 QuoteService 的 tick callback，維護：
    1. Tick Cache（最新一筆 per code）
    2. 1 分／5 分 K 聚合（BarAggregator）
    3. WebSocket 廣播（asyncio event loop）
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Tick Cache: code → latest tick dict
        self._tick_cache: dict[str, dict[str, Any]] = {}
        self._tick_timestamps: dict[str, float] = {}
        # Bar Aggregators：1 分 K 與既有 5 分 K 同時由同一份 tick 更新。
        self.bars_1m = BarAggregator(BAR_INTERVAL_1M_MS, "1m")
        self.bars = BarAggregator(BAR_INTERVAL_5M_MS, "5m")
        # 台指期使用獨立 aggregator，避免與同名股票代號或股票盤別混在一起。
        self.futures_bars_1m = BarAggregator(BAR_INTERVAL_1M_MS, "futures-1m")
        self.futures_bars = BarAggregator(BAR_INTERVAL_5M_MS, "futures-5m")
        # WebSocket subscribers: set of asyncio.Queue (one per connected client)
        self._ws_subscribers: set[asyncio.Queue] = set()
        self._ws_lock = threading.Lock()
        # Event loop reference (set by WebSocket server startup)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Stats
        self._total_ticks: int = 0
        self._total_bars_completed: int = 0
        self._total_bars_1m_completed: int = 0
        self._total_futures_bars_completed: int = 0
        self._total_futures_bars_1m_completed: int = 0

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """設定 asyncio event loop（WebSocket 廣播用）。"""
        self._loop = loop

    # ------------------------------------------------------------------
    # Tick 接收（由 QuoteService callback 呼叫，在 Shioaji 執行緒中）
    # ------------------------------------------------------------------

    def on_stock_tick(self, tick_data: dict[str, Any]) -> None:
        """接收台股 tick，更新 cache 與 bar aggregator，並廣播。"""
        code = tick_data.get("code", "")
        if not code:
            return

        price = tick_data.get("close")
        volume = tick_data.get("volume", 0) or 0
        if price is None:
            return
        side = _trade_side(tick_data)
        is_main_force = _is_main_force_trade(tick_data)
        try:
            trade_amount = max(0.0, float(tick_data.get("amount", 0) or 0))
        except (TypeError, ValueError):
            trade_amount = 0.0
        if trade_amount <= 0:
            trade_amount = max(0.0, float(price) * max(0, int(volume)) * 1000)

        # 更新 Tick Cache
        with self._lock:
            self._tick_cache[code] = tick_data
            self._tick_timestamps[code] = time.time()
            self._total_ticks += 1

        # 計算 tick timestamp (ms)
        tick_ts_ms = self._extract_tick_ts_ms(tick_data)

        # 逐筆 callback 直接送入同秒大單偵測器；不輪詢、不等待分 K 完成。
        try:
            from intraday_large_order import get_intraday_large_order_monitor
            get_intraday_large_order_monitor().on_tick(tick_data, tick_ts_ms)
        except Exception:  # noqa: BLE001
            logger.exception("盤中瞬間大單偵測失敗 code=%s", code)

        # 更新 1 分 K Aggregator
        completed_bar_1m = self.bars_1m.on_tick(
            code, price, volume, tick_ts_ms, side, is_main_force, trade_amount
        )
        if completed_bar_1m:
            self._total_bars_1m_completed += 1
            self._broadcast({
                "type": "bar1m_completed",
                "interval": "1m",
                "code": code,
                "bar": completed_bar_1m.to_dict(),
            })

        # 更新既有 5 分 K Aggregator
        completed_bar = self.bars.on_tick(
            code, price, volume, tick_ts_ms, side, is_main_force, trade_amount
        )
        if completed_bar:
            self._total_bars_completed += 1
            self._broadcast({
                "type": "bar_completed",
                "interval": "5m",
                "code": code,
                "bar": completed_bar.to_dict(),
            })

        # 廣播 tick
        self._broadcast({
            "type": "tick",
            "code": code,
            "data": tick_data,
        })

    def on_futures_tick(self, tick_data: dict[str, Any]) -> None:
        """接收台指期 tick，更新 cache、1m/5m K 棒並廣播。"""
        code = str(tick_data.get("code", "TXFR1") or "TXFR1").strip().upper()
        price = tick_data.get("close")
        volume = tick_data.get("volume", 0) or 0
        if price is None:
            return
        side = _trade_side(tick_data)
        is_main_force = _is_main_force_trade(tick_data, futures=True)

        with self._lock:
            self._tick_cache[f"FUT:{code}"] = tick_data
            self._tick_timestamps[f"FUT:{code}"] = time.time()
            self._total_ticks += 1

        tick_ts_ms = self._extract_tick_ts_ms(tick_data)
        completed_bar_1m = self.futures_bars_1m.on_tick(
            code, price, volume, tick_ts_ms, side, is_main_force
        )
        if completed_bar_1m:
            self._total_futures_bars_1m_completed += 1
            self._broadcast({
                "type": "futures_bar1m_completed",
                "interval": "1m",
                "code": code,
                "bar": completed_bar_1m.to_dict(),
            })

        completed_bar = self.futures_bars.on_tick(
            code, price, volume, tick_ts_ms, side, is_main_force
        )
        if completed_bar:
            self._total_futures_bars_completed += 1
            self._broadcast({
                "type": "futures_bar_completed",
                "interval": "5m",
                "code": code,
                "bar": completed_bar.to_dict(),
            })

        self._broadcast({
            "type": "futures_tick",
            "code": code,
            "data": tick_data,
        })

    # ------------------------------------------------------------------
    # 查詢 API（REST 備援 + intradayScan 使用）
    # ------------------------------------------------------------------

    def get_tick(self, code: str) -> Optional[dict[str, Any]]:
        """取得最新 tick（REST 查詢用）。"""
        with self._lock:
            tick = self._tick_cache.get(code)
            if tick is None:
                return None
            result = dict(tick)
            ts = self._tick_timestamps.get(code)
            result["hub_age_seconds"] = round(time.time() - ts, 1) if ts else None
            return result

    def get_ticks(self, codes: list[str]) -> dict[str, Optional[dict[str, Any]]]:
        """批次取得多檔最新 tick。"""
        return {code: self.get_tick(code) for code in codes}

    def get_all_ticks(self) -> dict[str, dict[str, Any]]:
        """取得所有已快取的 tick。"""
        with self._lock:
            result = {}
            now = time.time()
            for code, tick in self._tick_cache.items():
                r = dict(tick)
                ts = self._tick_timestamps.get(code)
                r["hub_age_seconds"] = round(now - ts, 1) if ts else None
                result[code] = r
            return result

    def get_live_bars_1m(self, code: str) -> list[dict[str, Any]]:
        """取得指定股票今日 1 分 K（含進行中的 K 棒）。"""
        return self.bars_1m.get_bars(code, include_current=True)

    def get_live_bars_1m_batch(self, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
        """批次取得多檔今日 1 分 K。"""
        return {code: self.get_live_bars_1m(code) for code in codes}

    def get_live_bars(self, code: str) -> list[dict[str, Any]]:
        """取得指定股票今日 5 分 K（供 intradayScan 使用）。"""
        return self.bars.get_bars(code, include_current=True)

    def get_live_bars_batch(self, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
        """批次取得多檔今日 5 分 K。"""
        return {code: self.get_live_bars(code) for code in codes}

    def get_live_futures_bars_1m(self, code: str) -> list[dict[str, Any]]:
        """取得指定期貨合約本次執行期間的 1 分 K（含進行中 K 棒）。"""
        return self.futures_bars_1m.get_bars(code.strip().upper(), include_current=True)

    def get_live_futures_bars(self, code: str) -> list[dict[str, Any]]:
        """取得指定期貨合約本次執行期間的 5 分 K（含進行中 K 棒）。"""
        return self.futures_bars.get_bars(code.strip().upper(), include_current=True)

    def get_hub_status(self) -> dict[str, Any]:
        """取得 Hub 狀態摘要。"""
        with self._lock:
            tick_count = len(self._tick_cache)
            timestamps = list(self._tick_timestamps.values())
            latest_ts = max(timestamps) if timestamps else None
            age = round(time.time() - latest_ts, 1) if latest_ts else None

        with self._ws_lock:
            ws_count = len(self._ws_subscribers)

        return {
            "cached_tick_count": tick_count,
            "latest_tick_age_seconds": age,
            "total_ticks_received": self._total_ticks,
            "total_bars_completed": self._total_bars_completed,
            "total_bars_1m_completed": self._total_bars_1m_completed,
            "total_futures_bars_completed": self._total_futures_bars_completed,
            "total_futures_bars_1m_completed": self._total_futures_bars_1m_completed,
            "websocket_clients": ws_count,
            "bar_aggregator_codes": len(self.bars._current),
            "bar_aggregator_1m_codes": len(self.bars_1m._current),
            "futures_bar_aggregator_codes": len(self.futures_bars._current),
            "futures_bar_aggregator_1m_codes": len(self.futures_bars_1m._current),
        }

    # ------------------------------------------------------------------
    # WebSocket 廣播
    # ------------------------------------------------------------------

    def subscribe_ws(self) -> asyncio.Queue:
        """新增一個 WebSocket 訂閱者，回傳其 message queue。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._ws_lock:
            self._ws_subscribers.add(queue)
        logger.info("[Hub] WebSocket 訂閱者加入（目前 %d 人）", len(self._ws_subscribers))
        return queue

    def unsubscribe_ws(self, queue: asyncio.Queue) -> None:
        """移除 WebSocket 訂閱者。"""
        with self._ws_lock:
            self._ws_subscribers.discard(queue)
        logger.info("[Hub] WebSocket 訂閱者離開（目前 %d 人）", len(self._ws_subscribers))

    def _broadcast(self, message: dict[str, Any]) -> None:
        """將訊息廣播到所有 WebSocket 訂閱者（thread-safe）。"""
        if not self._ws_subscribers:
            return

        with self._ws_lock:
            dead_queues: list[asyncio.Queue] = []
            for queue in self._ws_subscribers:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # 客戶端消費太慢，丟棄最舊的訊息
                    try:
                        queue.get_nowait()
                        queue.put_nowait(message)
                    except Exception:
                        dead_queues.append(queue)
                except Exception:
                    dead_queues.append(queue)

            for q in dead_queues:
                self._ws_subscribers.discard(q)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tick_ts_ms(tick_data: dict[str, Any]) -> int:
        """從 tick_data 中提取 timestamp (ms)。"""
        # 優先使用 tick_time ISO string
        tick_time = tick_data.get("tick_time", "")
        if tick_time and isinstance(tick_time, str):
            try:
                dt = datetime.fromisoformat(tick_time)
                return int(dt.timestamp() * 1000)
            except (ValueError, TypeError):
                pass
        # fallback: 使用當前時間
        return _now_ms()


# ------------------------------------------------------------------
# 全域單例
# ------------------------------------------------------------------

_hub: Optional[MarketDataHub] = None


def get_market_data_hub() -> MarketDataHub:
    """取得全域 MarketDataHub 單例。"""
    global _hub
    if _hub is None:
        _hub = MarketDataHub()
    return _hub
