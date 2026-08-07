"""HanStock 櫃買指數即時子 Hub。

與既有 MarketDataHub 並存，不改動股票 Tick 聚合器；專門接 Shioaji QuoteIdxV1，
維護櫃買指數今日 1 分／5 分 K，並支援登入/重連後用歷史 Kbars 補齊早盤資料。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from otc_index import (
    FIVE_MIN_MS,
    ONE_MIN_MS,
    OTC_INDEX_DISPLAY_NAME,
    OTC_INDEX_HUB_CODE,
    TW_TZ,
    is_regular_otc_session,
    taipei_trade_date,
    timestamp_to_ms,
)


@dataclass
class IndexBar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    tick_count: int = 0

    def update(self, price: float, volume: int = 0) -> None:
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
        self.close = price
        self.volume += max(0, int(volume or 0))
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
        }


class _IndexBarSeries:
    def __init__(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self.completed: dict[int, IndexBar] = {}
        self.current: Optional[IndexBar] = None

    def seed(self, bars: list[dict[str, Any]]) -> None:
        for raw in bars:
            try:
                ts = int(raw["ts"])
                bar = IndexBar(
                    ts=ts - (ts % self.interval_ms),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=max(0, int(raw.get("volume", 0) or 0)),
                    tick_count=max(1, int(raw.get("tick_count", 1) or 1)),
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                continue
            # 歷史 bootstrap 只放已完成 K；同 timestamp 後寫覆蓋前寫。
            self.completed[bar.ts] = bar

    def on_quote(self, price: float, volume: int, ts_ms: int) -> None:
        bucket = ts_ms - (ts_ms % self.interval_ms)
        if self.current is not None and bucket < self.current.ts:
            return
        if self.current is None or self.current.ts != bucket:
            if self.current is not None and self.current.tick_count > 0:
                self.completed[self.current.ts] = self.current
            # 若 bootstrap 已經有同一 bucket，延用該 OHLC 再接續即時 quote。
            seeded = self.completed.pop(bucket, None)
            if seeded is not None:
                self.current = seeded
            else:
                self.current = IndexBar(bucket, price, price, price, price)
            self.current.update(price, volume)
        else:
            self.current.update(price, volume)

    def bars(self, include_current: bool = True) -> list[dict[str, Any]]:
        rows = [bar.to_dict() for _, bar in sorted(self.completed.items())]
        if include_current and self.current is not None and self.current.tick_count > 0:
            rows.append(self.current.to_dict())
        return rows

    def reset(self) -> None:
        self.completed.clear()
        self.current = None


class OtcIndexHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        self._bars_1m = _IndexBarSeries(ONE_MIN_MS)
        self._bars_5m = _IndexBarSeries(FIVE_MIN_MS)
        self._latest_quote: Optional[dict[str, Any]] = None
        self._latest_quote_received_at: Optional[float] = None
        self._contract_code: Optional[str] = None
        self._contract_name: Optional[str] = None
        self._subscribed = False
        self._bootstrap_ok = False
        self._bootstrap_bar_count_1m = 0
        self._bootstrap_bar_count_5m = 0
        self._error: Optional[str] = None

    def _rollover_if_needed(self, trade_date: Optional[str] = None) -> None:
        current_date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
        if current_date == self._trade_date:
            return
        self._bars_1m.reset()
        self._bars_5m.reset()
        self._latest_quote = None
        self._latest_quote_received_at = None
        self._bootstrap_ok = False
        self._bootstrap_bar_count_1m = 0
        self._bootstrap_bar_count_5m = 0
        self._trade_date = current_date

    def configure_contract(self, code: str, name: str) -> None:
        with self._lock:
            self._contract_code = str(code or "").strip().upper() or None
            self._contract_name = str(name or "").strip() or None

    def set_subscribed(self, value: bool, error: Optional[str] = None) -> None:
        with self._lock:
            self._subscribed = bool(value)
            self._error = error

    def seed_today(
        self,
        bars_1m: list[dict[str, Any]],
        bars_5m: list[dict[str, Any]],
        trade_date: str,
    ) -> None:
        with self._lock:
            self._rollover_if_needed(trade_date)
            self._bars_1m.seed(bars_1m)
            self._bars_5m.seed(bars_5m)
            self._bootstrap_bar_count_1m = len(bars_1m)
            self._bootstrap_bar_count_5m = len(bars_5m)
            self._bootstrap_ok = bool(bars_5m)
            if bars_5m:
                self._error = None

    def on_quote(self, quote_data: dict[str, Any]) -> None:
        price_raw = quote_data.get("close")
        try:
            price = float(price_raw)
        except (TypeError, ValueError, OverflowError):
            return
        if price <= 0:
            return

        ts_ms = timestamp_to_ms(quote_data.get("datetime"))
        if ts_ms is None:
            ts_ms = timestamp_to_ms(quote_data.get("quote_time"))
        if ts_ms is None:
            ts_ms = int(time.time() * 1000)

        trade_date = taipei_trade_date(ts_ms)
        if not is_regular_otc_session(ts_ms):
            # 收盤後的最後 snapshot 可保留 latest，但不產生 13:30 之後 K 棒。
            with self._lock:
                self._rollover_if_needed(trade_date)
                self._latest_quote = dict(quote_data)
                self._latest_quote_received_at = time.time()
            return

        volume = quote_data.get("volume", 0) or 0
        try:
            volume_value = max(0, int(volume))
        except (TypeError, ValueError, OverflowError):
            volume_value = 0

        with self._lock:
            self._rollover_if_needed(trade_date)
            self._bars_1m.on_quote(price, volume_value, ts_ms)
            self._bars_5m.on_quote(price, volume_value, ts_ms)
            self._latest_quote = dict(quote_data)
            self._latest_quote_received_at = time.time()
            self._subscribed = True
            self._error = None

    def get_bars_1m(self, include_current: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            self._rollover_if_needed()
            return self._bars_1m.bars(include_current=include_current)

    def get_bars_5m(self, include_current: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            self._rollover_if_needed()
            return self._bars_5m.bars(include_current=include_current)

    def get_latest_quote(self) -> Optional[dict[str, Any]]:
        with self._lock:
            if self._latest_quote is None:
                return None
            row = dict(self._latest_quote)
            if self._latest_quote_received_at is not None:
                row["hub_age_seconds"] = round(time.time() - self._latest_quote_received_at, 1)
            return row

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            self._rollover_if_needed()
            age = (
                round(time.time() - self._latest_quote_received_at, 1)
                if self._latest_quote_received_at is not None
                else None
            )
            bars_1m = self._bars_1m.bars(include_current=True)
            bars_5m = self._bars_5m.bars(include_current=True)
            return {
                "hub_code": OTC_INDEX_HUB_CODE,
                "display_name": OTC_INDEX_DISPLAY_NAME,
                "trade_date": self._trade_date,
                "contract_code": self._contract_code,
                "contract_name": self._contract_name,
                "subscribed": self._subscribed,
                "bootstrap_ok": self._bootstrap_ok,
                "bootstrap_bar_count_1m": self._bootstrap_bar_count_1m,
                "bootstrap_bar_count_5m": self._bootstrap_bar_count_5m,
                "bar_count_1m": len(bars_1m),
                "bar_count_5m": len(bars_5m),
                "latest_quote_age_seconds": age,
                "error": self._error,
            }


_hub: Optional[OtcIndexHub] = None


def get_otc_index_hub() -> OtcIndexHub:
    global _hub
    if _hub is None:
        _hub = OtcIndexHub()
    return _hub
