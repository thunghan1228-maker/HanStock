"""漲跌幅前 20 族群的逐筆「同秒大單」即時偵測。"""

from __future__ import annotations

import os
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from group_strength_store import load_group_strength_history
from intraday_signal_store import save_intraday_signals
from stock_groups import STOCK_GROUPS

TW_TZ = timezone(timedelta(hours=8))
GROUP_LIMIT = max(1, min(50, int(os.getenv("HANSTOCK_INSTANT_LARGE_GROUP_LIMIT", "20"))))
WINDOW_MS = max(250, int(os.getenv("HANSTOCK_INSTANT_LARGE_WINDOW_MS", "1000")))
MIN_TICK_LOTS = max(1, int(os.getenv("HANSTOCK_INSTANT_LARGE_MIN_TICK_LOTS", "20")))
MIN_TICK_AMOUNT = max(1.0, float(os.getenv("HANSTOCK_INSTANT_LARGE_MIN_TICK_AMOUNT", "1000000")))
MIN_BURST_LOTS = max(1, int(os.getenv("HANSTOCK_INSTANT_LARGE_MIN_BURST_LOTS", "100")))
MIN_BURST_AMOUNT = max(1.0, float(os.getenv("HANSTOCK_INSTANT_LARGE_MIN_BURST_AMOUNT", "10000000")))
EXTRA_BURST_LOTS = max(MIN_BURST_LOTS, int(os.getenv("HANSTOCK_INSTANT_EXTRA_LARGE_LOTS", "300")))
EXTRA_BURST_AMOUNT = max(MIN_BURST_AMOUNT, float(os.getenv("HANSTOCK_INSTANT_EXTRA_LARGE_AMOUNT", "30000000")))
COOLDOWN_MS = max(60_000, int(os.getenv("HANSTOCK_INSTANT_LARGE_COOLDOWN_MS", "300000")))
EXCLUDED_GROUPS = {"股期標的", "小型股票期貨", "ETF"}


def _money(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 億"
    if value >= 10_000:
        return f"{value / 10_000:.1f} 萬"
    return f"{value:,.0f} 元"


def build_group_candidates(ranks: dict[str, int]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    clean = {name: rank for name, rank in ranks.items() if name not in EXCLUDED_GROUPS and isinstance(rank, int) and rank > 0}
    if not clean:
        return {}, {}
    total = max(clean.values())
    rising = {name: rank for name, rank in clean.items() if rank <= GROUP_LIMIT}
    falling = {name: total - rank + 1 for name, rank in clean.items() if total - rank + 1 <= GROUP_LIMIT}
    buy: dict[str, dict[str, Any]] = {}
    sell: dict[str, dict[str, Any]] = {}
    for group, members in STOCK_GROUPS.items():
        if group in rising:
            for ticker, name in members:
                old = buy.get(ticker)
                if old is None or rising[group] < old["rank"]:
                    buy[ticker] = {"name": name, "group": group, "rank": rising[group], "direction": "漲幅"}
        if group in falling:
            for ticker, name in members:
                old = sell.get(ticker)
                if old is None or falling[group] < old["rank"]:
                    sell[ticker] = {"name": name, "group": group, "rank": falling[group], "direction": "跌幅"}
    return buy, sell


class IntradayLargeOrderMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._candidates: dict[str, dict[str, dict[str, Any]]] = {"buy": {}, "sell": {}}
        self._windows: dict[tuple[str, str], deque[tuple[int, int, float, float]]] = defaultdict(deque)
        self._last_emitted: dict[tuple[str, str], int] = {}
        self._status: dict[str, Any] = {"prepared": False, "reason": "waiting_group_snapshot"}

    def set_candidates(self, buy: dict[str, dict[str, Any]], sell: dict[str, dict[str, Any]], status: dict[str, Any]) -> None:
        with self._lock:
            self._candidates = {"buy": buy, "sell": sell}
            self._status = {**status, "prepared": bool(buy or sell)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def update_status(self, **values: Any) -> None:
        """補充收集器健康狀態，不改動目前的候選名單。"""
        with self._lock:
            self._status.update(values)

    def on_tick(self, tick: dict[str, Any], tick_ts_ms: int) -> list[dict[str, Any]]:
        code = str(tick.get("code") or "").strip().upper()
        tick_type = int(tick.get("tick_type") or 0)
        side = "buy" if tick_type == 1 else "sell" if tick_type == 2 else ""
        if not code or not side or bool(tick.get("simtrade")) or bool(tick.get("intraday_odd")):
            return []
        with self._lock:
            meta = self._candidates[side].get(code)
        if not meta:
            return []
        try:
            price = float(tick.get("close") or 0)
            lots = max(0, int(tick.get("volume") or 0))
            amount = max(0.0, float(tick.get("amount") or 0)) or price * lots * 1000
        except (TypeError, ValueError):
            return []
        if price <= 0 or lots <= 0 or (lots < MIN_TICK_LOTS and amount < MIN_TICK_AMOUNT):
            return []
        key = (code, side)
        with self._lock:
            window = self._windows[key]
            window.append((tick_ts_ms, lots, amount, price))
            while window and window[0][0] < tick_ts_ms - WINDOW_MS:
                window.popleft()
            total_lots = sum(item[1] for item in window)
            total_amount = sum(item[2] for item in window)
            if total_lots < MIN_BURST_LOTS and total_amount < MIN_BURST_AMOUNT:
                return []
            if tick_ts_ms - self._last_emitted.get(key, 0) < COOLDOWN_MS:
                return []
            self._last_emitted[key] = tick_ts_ms
            snapshot = list(window)
        extra = total_lots >= EXTRA_BURST_LOTS or total_amount >= EXTRA_BURST_AMOUNT
        is_buy = side == "buy"
        signal = {
            "tradeDate": datetime.fromtimestamp(tick_ts_ms / 1000, TW_TZ).strftime("%Y-%m-%d"),
            "ticker": code,
            "name": str(meta.get("name") or code),
            "groupName": str(meta["group"]),
            "kind": "instantLargeBuy" if is_buy else "instantLargeSell",
            "label": ("瞬間特大買單敲進" if is_buy else "瞬間特大賣單倒出") if extra else ("瞬間大單連續敲進" if is_buy else "瞬間大單連續倒出"),
            "barTs": tick_ts_ms,
            "price": price,
            "note": f"同秒 {len(snapshot)} 筆｜合計 {total_lots:,} 張｜約 {_money(total_amount)}｜成交價 {min(x[3] for x in snapshot):g}～{max(x[3] for x in snapshot):g}｜族群同步 {meta['group']} {meta['direction']}第 {meta['rank']} 名",
        }
        inserted = save_intraday_signals([signal])
        if inserted:
            with self._lock:
                self._status["lastSignal"] = inserted[0]
        return inserted


_monitor = IntradayLargeOrderMonitor()


def get_intraday_large_order_monitor() -> IntradayLargeOrderMonitor:
    return _monitor


def refresh_intraday_large_order_candidates(service: Any) -> dict[str, Any]:
    trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    history = load_group_strength_history(trade_date)
    if not history:
        # 背景族群收集器可能因部署重啟或暫時網路錯誤錯過第一輪；大單偵測
        # 不應因此整個交易日維持 0。候選刷新時主動補抓一次，再重新讀取。
        try:
            from group_strength_collector import collect_once as collect_group_strength_once

            if collect_group_strength_once():
                history = load_group_strength_history(trade_date)
        except Exception:  # noqa: BLE001
            history = []
    if not history:
        status = {"prepared": False, "reason": "waiting_group_snapshot", "tradeDate": trade_date, "candidateCount": 0}
        _monitor.set_candidates({}, {}, status)
        return status
    latest = history[-1]
    buy, sell = build_group_candidates(latest["ranks"])
    codes = list(dict.fromkeys([*buy, *sell]))
    subscription = service.ensure_stock_subscriptions(codes)
    failed = subscription.get("failed") or {}
    status = {
        "tradeDate": trade_date,
        "snapshotTs": latest["bucketTs"],
        "groupLimitPerSide": GROUP_LIMIT,
        "buyCandidateCount": len(buy),
        "sellCandidateCount": len(sell),
        "candidateCount": len(codes),
        "subscriptionCapacity": int(subscription.get("capacity") or 0),
        "activeCount": int(subscription.get("active_count") or len(subscription.get("already_subscribed") or []) + len(subscription.get("newly_subscribed") or [])),
        "failedCount": len(failed),
        "failed": failed,
    }
    _monitor.set_candidates(buy, sell, status)
    return status
