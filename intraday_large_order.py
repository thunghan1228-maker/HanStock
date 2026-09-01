"""漲跌幅前 20 族群的逐筆「同秒大單」即時偵測。"""

from __future__ import annotations

import os
import threading
from math import isfinite
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from group_strength_store import load_group_strength_history, save_group_strength_snapshot
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
MIN_LIVE_GROUPS = max(20, min(100, int(os.getenv("HANSTOCK_INSTANT_LARGE_MIN_LIVE_GROUPS", "40"))))


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


def build_live_group_ranks(service: Any) -> dict[str, int]:
    """直接用本機 Shioaji 現貨 Tick 建立族群排名，避免依賴自我 HTTP 回呼。"""
    averages: list[tuple[str, float]] = []
    for group, members in STOCK_GROUPS.items():
        if group in EXCLUDED_GROUPS:
            continue
        changes: list[float] = []
        for ticker, _name in members:
            quote = service.get_stock_quote(ticker)
            if not isinstance(quote, dict) or quote.get("simtrade") or quote.get("intraday_odd"):
                continue
            try:
                value = float(quote.get("pct_chg"))
            except (TypeError, ValueError):
                continue
            if isfinite(value):
                changes.append(value)
        if changes:
            averages.append((group, sum(changes) / len(changes)))
    averages.sort(key=lambda item: (-item[1], item[0]))
    return {group: index + 1 for index, (group, _change) in enumerate(averages)}


def _ensure_group_universe_subscriptions(service: Any) -> None:
    codes = list(dict.fromkeys(
        ticker
        for group, members in STOCK_GROUPS.items()
        if group not in EXCLUDED_GROUPS
        for ticker, _name in members
    ))
    service.ensure_stock_subscriptions(codes)


class IntradayLargeOrderMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._candidates: dict[str, dict[str, dict[str, Any]]] = {"buy": {}, "sell": {}}
        self._windows: dict[tuple[str, str], deque[tuple[int, int, float, float]]] = defaultdict(deque)
        self._last_emitted: dict[tuple[str, str], int] = {}
        self._recent_signals: deque[dict[str, Any]] = deque(maxlen=1000)
        self._pending_signals: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        self._runtime_trade_date = ""
        self._runtime: dict[str, Any] = {}
        self._status: dict[str, Any] = {"prepared": False, "reason": "waiting_group_snapshot"}

    def _reset_runtime(self, trade_date: str) -> None:
        self._runtime_trade_date = trade_date
        self._runtime = {
            "candidateTickCount": 0,
            "eligibleTickCount": 0,
            "burstThresholdCount": 0,
            "persistedSignalCount": 0,
            "persistenceErrorCount": 0,
            "pendingSignalCount": 0,
        }
        self._windows.clear()
        self._last_emitted.clear()
        self._recent_signals.clear()
        self._pending_signals.clear()

    def set_candidates(self, buy: dict[str, dict[str, Any]], sell: dict[str, dict[str, Any]], status: dict[str, Any]) -> None:
        with self._lock:
            trade_date = str(status.get("tradeDate") or "")
            if trade_date and trade_date != self._runtime_trade_date:
                self._reset_runtime(trade_date)
            self._candidates = {"buy": buy, "sell": sell}
            self._status = {**status, "prepared": bool(buy or sell)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._status, **self._runtime, "pendingSignalCount": len(self._pending_signals)}

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
        with self._lock:
            self._runtime["candidateTickCount"] = int(self._runtime.get("candidateTickCount") or 0) + 1
            self._runtime["lastCandidateTickAt"] = datetime.fromtimestamp(tick_ts_ms / 1000, TW_TZ).isoformat()
        try:
            price = float(tick.get("close") or 0)
            lots = max(0, int(tick.get("volume") or 0))
            amount = max(0.0, float(tick.get("amount") or 0)) or price * lots * 1000
        except (TypeError, ValueError):
            return []
        if price <= 0 or lots <= 0 or (lots < MIN_TICK_LOTS and amount < MIN_TICK_AMOUNT):
            return []
        with self._lock:
            self._runtime["eligibleTickCount"] = int(self._runtime.get("eligibleTickCount") or 0) + 1
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
            self._runtime["burstThresholdCount"] = int(self._runtime.get("burstThresholdCount") or 0) + 1
            self._runtime["lastBurstAt"] = datetime.fromtimestamp(tick_ts_ms / 1000, TW_TZ).isoformat()
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
        signal_key = (signal["tradeDate"], signal["ticker"], signal["kind"], signal["barTs"])
        with self._lock:
            self._recent_signals.append(signal)
        try:
            inserted = save_intraday_signals([signal])
        except Exception as exc:  # noqa: BLE001
            # SQLite 被其他盤中收集器短暫鎖住時，訊號先保留在記憶體並排隊
            # 重試，API 仍可立即顯示，不再整筆消失。
            with self._lock:
                self._pending_signals[signal_key] = signal
                self._runtime["persistenceErrorCount"] = int(self._runtime.get("persistenceErrorCount") or 0) + 1
                self._runtime["lastPersistenceError"] = type(exc).__name__
                self._runtime["pendingSignalCount"] = len(self._pending_signals)
            return [signal]
        if inserted:
            with self._lock:
                self._runtime["lastSignal"] = inserted[0]
                self._runtime["persistedSignalCount"] = int(self._runtime.get("persistedSignalCount") or 0) + len(inserted)
        return inserted or [signal]

    def recent_signals(self, trade_date: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(row) for row in self._recent_signals if row.get("tradeDate") == trade_date]
        return sorted(rows, key=lambda row: int(row.get("barTs") or 0), reverse=True)[:max(1, limit)]

    def flush_pending_signals(self) -> int:
        with self._lock:
            rows = list(self._pending_signals.values())
        if not rows:
            return 0
        try:
            inserted = save_intraday_signals(rows)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._runtime["persistenceErrorCount"] = int(self._runtime.get("persistenceErrorCount") or 0) + 1
                self._runtime["lastPersistenceError"] = type(exc).__name__
            return 0
        row_keys = {(row["tradeDate"], row["ticker"], row["kind"], row["barTs"]) for row in rows}
        with self._lock:
            for signal_key in row_keys:
                self._pending_signals.pop(signal_key, None)
            self._runtime["pendingSignalCount"] = len(self._pending_signals)
            self._runtime["persistedSignalCount"] = int(self._runtime.get("persistedSignalCount") or 0) + len(inserted)
            self._runtime["lastPersistenceRecoveredAt"] = datetime.now(TW_TZ).isoformat()
        return len(inserted)


_monitor = IntradayLargeOrderMonitor()


def get_intraday_large_order_monitor() -> IntradayLargeOrderMonitor:
    return _monitor


def refresh_intraday_large_order_candidates(service: Any) -> dict[str, Any]:
    trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    history = load_group_strength_history(trade_date)
    candidate_source = "stored_group_snapshot"
    fallback_status: dict[str, Any] = {"localFallbackAttempted": False}
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
        # 正式主機已持有全市場即時 Tick；若主機呼叫自己的網站 API 逾時，
        # 直接在程序內依族群平均漲跌幅排名，避免候選名單整天維持 0。
        try:
            fallback_status["localFallbackAttempted"] = True
            _ensure_group_universe_subscriptions(service)
            live_ranks = build_live_group_ranks(service)
            fallback_status["localRankedGroupCount"] = len(live_ranks)
            fallback_status["localRankedGroupMinimum"] = MIN_LIVE_GROUPS
            if len(live_ranks) >= MIN_LIVE_GROUPS:
                now_ms = int(datetime.now(TW_TZ).timestamp() * 1000)
                bucket_ts = now_ms // 300_000 * 300_000
                history = [{"bucketTs": bucket_ts, "ranks": live_ranks}]
                candidate_source = "local_shioaji_group_ranking"
                try:
                    save_group_strength_snapshot(trade_date, bucket_ts, live_ranks)
                    fallback_status["snapshotPersisted"] = True
                except Exception as exc:  # noqa: BLE001
                    # SQLite 可能被其他盤中收集器短暫鎖住；候選排名已完整時，
                    # 仍須立刻啟用大單偵測，下一輪再補存快照即可。
                    fallback_status["snapshotPersisted"] = False
                    fallback_status["snapshotPersistError"] = type(exc).__name__
        except Exception as exc:  # noqa: BLE001
            fallback_status["localFallbackError"] = type(exc).__name__
            history = []
    if not history:
        status = {
            "prepared": False,
            "reason": "waiting_group_snapshot",
            "tradeDate": trade_date,
            "candidateCount": 0,
            **fallback_status,
        }
        _monitor.set_candidates({}, {}, status)
        return status
    latest = history[-1]
    buy, sell = build_group_candidates(latest["ranks"])
    codes = list(dict.fromkeys([*buy, *sell]))
    subscription = service.ensure_stock_subscriptions(codes)
    failed = subscription.get("failed") or {}
    status = {
        "prepared": True,
        "tradeDate": trade_date,
        "snapshotTs": latest["bucketTs"],
        "candidateSource": candidate_source,
        **fallback_status,
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
