"""HanStock 個股 K 線重啟復原層。

目的：MarketDataHub 的 1m/5m K 棒原本只存在記憶體；Railway 重啟後會歸零。
本模組在個股 K 線被讀取時：
1. 自動確保該股票 Tick 已訂閱；
2. 以 Shioaji api.kbars() 補齊今日已完成的正式 1 分 K；
3. 以 Shioaji api.ticks() 回補逐筆成交方向與主力大單欄位；
4. 聚合成今日已完成的 5 分 K；
5. 與 MarketDataHub 的即時 K 棒依 timestamp 合併（live 覆蓋 history）。

這層不修改 QuoteService / MarketDataHub 的穩定即時流程；主力副圖會另行永久落盤。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from otc_index import (
    TW_TZ,
    ONE_MIN_MS,
    aggregate_1m_to_5m,
    normalize_kbars_1m,
    shioaji_kbar_close_to_start_ms,
    taipei_minute_of_day,
    taipei_trade_date,
)
from market_data_hub import _is_main_force_trade, _trade_side

logger = logging.getLogger("hanstock.stock_bar_bootstrap")

RETRY_AFTER_SECONDS = 30.0


def _latest_weekday_trade_date(now_ms: int) -> str:
    """週末改抓前一個交易日，避免 Shioaji 查到當日空資料。"""
    trade_day = datetime.fromtimestamp(now_ms / 1000, TW_TZ)
    while trade_day.weekday() >= 5:
        trade_day -= timedelta(days=1)
    return trade_day.strftime("%Y-%m-%d")


@dataclass
class _HistoryEntry:
    trade_date: str
    bars_1m: list[dict[str, Any]]
    bars_5m: list[dict[str, Any]]
    fetched_at_monotonic: float
    ok: bool
    error: Optional[str] = None
    main_force_ok: bool = False
    main_force_error: Optional[str] = None


_cache_lock = threading.RLock()
_history_cache: dict[str, _HistoryEntry] = {}
_code_locks: dict[str, threading.Lock] = {}


def clear_stock_bar_bootstrap_cache() -> None:
    """清空歷史 K 棒快取；主要供測試與日後維運使用。"""
    with _cache_lock:
        _history_cache.clear()
        _code_locks.clear()


def _get_code_lock(code: str) -> threading.Lock:
    with _cache_lock:
        lock = _code_locks.get(code)
        if lock is None:
            lock = threading.Lock()
            _code_locks[code] = lock
        return lock


def _default_service() -> Any:
    from quote_service import get_quote_service

    return get_quote_service()


def _default_hub() -> Any:
    from market_data_hub import get_market_data_hub

    return get_market_data_hub()


def _resolve_stock_contract(service: Any, code: str) -> Any:
    """沿用 QuoteService 的股票合約解析；測試 fake service 也可覆寫。"""
    resolver = getattr(service, "_resolve_stock_contract", None)
    if callable(resolver):
        return resolver(code)

    api = getattr(service, "api", None)
    if api is None:
        return None
    contract = api.contracts.get(code)
    if contract is None:
        try:
            contract = api.Contracts.Stocks[code]
        except Exception:
            contract = None
    return contract


def _safe_bar(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    try:
        ts = int(raw.get("ts"))
        open_ = float(raw.get("open"))
        high = float(raw.get("high"))
        low = float(raw.get("low"))
        close = float(raw.get("close"))
        volume = max(0, int(raw.get("volume", 0) or 0))
        tick_count = max(0, int(raw.get("tick_count", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if ts <= 0 or min(open_, high, low, close) <= 0:
        return None
    result = {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "tick_count": tick_count,
    }
    optional_volume_fields = (
        "buy_volume",
        "sell_volume",
        "neutral_volume",
        "main_buy_volume",
        "main_sell_volume",
        "main_tick_count",
    )
    for field in optional_volume_fields:
        if field in raw:
            try:
                result[field] = max(0, int(raw.get(field, 0) or 0))
            except (TypeError, ValueError, OverflowError):
                result[field] = 0
    for field in ("main_buy_amount", "main_sell_amount"):
        if field in raw:
            try:
                result[field] = max(0, round(float(raw.get(field, 0) or 0)))
            except (TypeError, ValueError, OverflowError):
                result[field] = 0
    if "main_net_volume" in raw:
        try:
            result["main_net_volume"] = int(raw.get("main_net_volume", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            result["main_net_volume"] = 0
    if "main_net_amount" in raw:
        try:
            result["main_net_amount"] = round(float(raw.get("main_net_amount", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            result["main_net_amount"] = 0
    if "main_force_available" in raw:
        result["main_force_available"] = bool(raw.get("main_force_available"))
    return result


def _field_values(payload: Any, *names: str) -> list[Any]:
    """讀取 Shioaji Ticks 的欄位；同時支援物件屬性與 Mapping。"""
    for name in names:
        value = payload.get(name) if isinstance(payload, Mapping) else getattr(payload, name, None)
        if value is None:
            continue
        try:
            return list(value)
        except TypeError:
            return []
    return []


def _shioaji_tick_to_ms(value: Any) -> Optional[int]:
    """把 Shioaji 歷史 tick 的台灣牆鐘 timestamp 轉為真正 UTC epoch ms。"""
    kbar_start = shioaji_kbar_close_to_start_ms(value)
    return kbar_start + ONE_MIN_MS if kbar_start is not None else None


def _fetch_historical_ticks(api: Any, contract: Any, trade_date: str) -> Any:
    """要求整日逐筆；舊版／測試替身若無 AllDay enum 則沿用預設查詢。"""
    kwargs: dict[str, Any] = {
        "contract": contract,
        "date": trade_date,
    }
    try:
        import shioaji as sj

        query_types = getattr(getattr(sj, "constant", None), "TicksQueryType", None)
        all_day = getattr(query_types, "AllDay", None)
        if all_day is not None:
            kwargs["query_type"] = all_day
    except Exception:
        pass
    return api.ticks(**kwargs)


def _historical_tick_metrics(
    ticks: Any,
    *,
    trade_date: str,
) -> dict[int, dict[str, Any]]:
    """將歷史逐筆成交依 1 分鐘彙總成內外盤與主力大單統計（單位：張）。"""
    timestamps = _field_values(ticks, "ts", "datetime")
    closes = _field_values(ticks, "close", "Close")
    volumes = _field_values(ticks, "volume", "Volume")
    tick_types = _field_values(ticks, "tick_type", "TickType")
    amounts = _field_values(ticks, "amount", "Amount")
    simtrades = _field_values(ticks, "simtrade", "Simtrade")
    size = min(len(timestamps), len(closes), len(volumes))
    metrics: dict[int, dict[str, Any]] = {}

    for index in range(size):
        ts_ms = _shioaji_tick_to_ms(timestamps[index])
        if ts_ms is None or taipei_trade_date(ts_ms) != trade_date:
            continue
        minute_of_day = taipei_minute_of_day(ts_ms)
        if minute_of_day < 9 * 60 or minute_of_day > 13 * 60 + 30:
            continue
        if index < len(simtrades) and bool(simtrades[index]):
            continue
        try:
            close = float(closes[index])
            volume = max(0, int(volumes[index] or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if close <= 0 or volume <= 0:
            continue

        tick_type: Any = tick_types[index] if index < len(tick_types) else 0
        tick_type = getattr(tick_type, "value", tick_type)
        try:
            tick_type = int(tick_type)
        except (TypeError, ValueError, OverflowError):
            tick_type = 0
        amount: Any = amounts[index] if index < len(amounts) else 0
        minute_ts = ts_ms - (ts_ms % ONE_MIN_MS)
        row = metrics.setdefault(minute_ts, {
            "buy_volume": 0,
            "sell_volume": 0,
            "neutral_volume": 0,
            "main_buy_volume": 0,
            "main_sell_volume": 0,
            "main_net_volume": 0,
            "main_buy_amount": 0.0,
            "main_sell_amount": 0.0,
            "main_net_amount": 0.0,
            "main_tick_count": 0,
            "tick_count": 0,
            "main_force_available": True,
        })
        side = _trade_side({"tick_type": tick_type})
        row[f"{side}_volume"] += volume
        is_main_force = _is_main_force_trade({
            "close": close,
            "volume": volume,
            "amount": amount,
        })
        if is_main_force and side in {"buy", "sell"}:
            try:
                trade_amount = max(0.0, float(amount or 0))
            except (TypeError, ValueError, OverflowError):
                trade_amount = 0.0
            if trade_amount <= 0:
                trade_amount = close * volume * 1000
            row[f"main_{side}_volume"] += volume
            row[f"main_{side}_amount"] += trade_amount
            row["main_tick_count"] += 1
        row["tick_count"] += 1

    for row in metrics.values():
        row["main_net_volume"] = row["main_buy_volume"] - row["main_sell_volume"]
        row["main_buy_amount"] = round(row["main_buy_amount"])
        row["main_sell_amount"] = round(row["main_sell_amount"])
        row["main_net_amount"] = row["main_buy_amount"] - row["main_sell_amount"]
    return metrics


def _attach_tick_metrics(
    bars_1m: list[dict[str, Any]],
    metrics: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {**bar, **metrics[bar["ts"]]} if bar.get("ts") in metrics else dict(bar)
        for bar in bars_1m
    ]


def _is_formal_stock_bar(ts_ms: int, interval: str, trade_date: str) -> bool:
    if taipei_trade_date(ts_ms) != trade_date:
        return False
    minute = taipei_minute_of_day(ts_ms)
    if interval == "1m":
        # 收盤撮合可能形成 13:30 起始標記；沿用既有 V7 1m 顯示行為。
        return 9 * 60 <= minute <= 13 * 60 + 30
    # 5m 正式最後一根為 13:25~13:30。
    return 9 * 60 <= minute < 13 * 60 + 30


def _merge_bars(
    history: list[dict[str, Any]],
    live: list[dict[str, Any]],
    *,
    interval: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    """history 先放、live 後放；即時 OHLCV 覆蓋，同分鐘歷史主力欄位可保留。"""
    merged: dict[int, dict[str, Any]] = {}
    for source in (history, live):
        for raw in source:
            bar = _safe_bar(raw)
            if bar is None or not _is_formal_stock_bar(bar["ts"], interval, trade_date):
                continue
            existing = merged.get(bar["ts"])
            if existing is not None:
                merged[bar["ts"]] = {**existing, **bar}
            else:
                merged[bar["ts"]] = bar
    return [merged[ts] for ts in sorted(merged)]


def _cached_entry(code: str, trade_date: str, now_monotonic: float) -> Optional[_HistoryEntry]:
    with _cache_lock:
        entry = _history_cache.get(code)
    if entry is None or entry.trade_date != trade_date:
        return None
    if not entry.ok and now_monotonic - entry.fetched_at_monotonic >= RETRY_AFTER_SECONDS:
        return None
    return entry


def _store_entry(code: str, entry: _HistoryEntry) -> _HistoryEntry:
    with _cache_lock:
        _history_cache[code] = entry
    return entry


def _bootstrap_history(
    code: str,
    trade_date: str,
    *,
    service: Any,
    now_ms: int,
    monotonic_fn: Callable[[], float],
) -> _HistoryEntry:
    api = getattr(service, "api", None)
    logged_in = bool(getattr(getattr(service, "state", None), "logged_in", False))
    if api is None or not logged_in:
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error="Shioaji 尚未登入",
            ),
        )

    contract = _resolve_stock_contract(service, code)
    if contract is None:
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error=f"找不到股票合約：{code}",
            ),
        )

    try:
        kbars = api.kbars(contract=contract, start=trade_date, end=trade_date)
        bars_1m = normalize_kbars_1m(
            kbars,
            trade_date=trade_date,
            include_current=False,
            now_ms=now_ms,
        )
        main_force_ok = False
        main_force_error: Optional[str] = None
        try:
            ticks = _fetch_historical_ticks(api, contract, trade_date)
            metrics = _historical_tick_metrics(ticks, trade_date=trade_date)
            bars_1m = _attach_tick_metrics(bars_1m, metrics)
            main_force_ok = bool(metrics)
            if not main_force_ok:
                main_force_error = "Shioaji 今日 ticks 暫無資料"
        except Exception as exc:
            main_force_error = str(exc)
            logger.warning("[Stock Main Force] %s 歷史逐筆回補失敗: %s", code, exc)
        bars_5m = aggregate_1m_to_5m(
            bars_1m,
            include_current=False,
            now_ms=now_ms,
        )
        ok = bool(bars_1m)
        entry = _HistoryEntry(
            trade_date=trade_date,
            bars_1m=bars_1m,
            bars_5m=bars_5m,
            fetched_at_monotonic=monotonic_fn(),
            ok=ok,
            error=None if ok else "Shioaji 今日 Kbars 暫無資料",
            main_force_ok=main_force_ok,
            main_force_error=main_force_error,
        )
        _store_entry(code, entry)
        logger.info(
            "[Stock KBar] %s 歷史補齊完成: 1m=%d, 5m=%d, main_force=%s, date=%s",
            code,
            len(bars_1m),
            len(bars_5m),
            main_force_ok,
            trade_date,
        )
        return entry
    except Exception as exc:
        logger.warning("[Stock KBar] %s 歷史補齊失敗: %s", code, exc)
        return _store_entry(
            code,
            _HistoryEntry(
                trade_date=trade_date,
                bars_1m=[],
                bars_5m=[],
                fetched_at_monotonic=monotonic_fn(),
                ok=False,
                error=str(exc),
            ),
        )


def backfill_main_force_date(
    stock_code: str,
    trade_date: str,
    *,
    service: Any = None,
    now_ms: Optional[int] = None,
) -> dict[str, Any]:
    """用 Shioaji 真實 Kbars/ticks 回補指定交易日，無 ticks 時不建立假資料。"""
    code = str(stock_code).strip().upper()
    datetime.strptime(trade_date, "%Y-%m-%d")
    service = service if service is not None else _default_service()
    # 歷史日期的 include_current 判斷需使用該日收盤後時間。
    effective_now = now_ms or int(datetime.now(TW_TZ).timestamp() * 1000)
    entry = _bootstrap_history(
        code, trade_date, service=service, now_ms=effective_now, monotonic_fn=time.monotonic,
    )
    saved_1m = saved_5m = 0
    if entry.main_force_ok:
        from main_force_store import save_main_force_bars
        saved_1m = save_main_force_bars(code, "1m", entry.bars_1m)
        saved_5m = save_main_force_bars(code, "5m", entry.bars_5m)
    return {
        "code": code, "trade_date": trade_date, "history_ok": entry.ok,
        "main_force_ok": entry.main_force_ok, "error": entry.main_force_error or entry.error,
        "saved_1m": saved_1m, "saved_5m": saved_5m,
    }


def get_resilient_stock_bars(
    stock_code: str,
    interval: str,
    *,
    service: Any = None,
    hub: Any = None,
    now_ms: Optional[int] = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """取得可跨 Railway 重啟復原的今日個股 1m/5m K 棒。"""
    code = str(stock_code).strip().upper()
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")

    service = service if service is not None else _default_service()
    hub = hub if hub is not None else _default_hub()
    now_value = now_ms if now_ms is not None else int(datetime.now(TW_TZ).timestamp() * 1000)
    trade_date = _latest_weekday_trade_date(now_value)

    subscription: Any = None
    try:
        subscription = service.ensure_stock_subscriptions([code])
    except Exception as exc:
        logger.warning("[Stock KBar] %s 自動訂閱失敗: %s", code, exc)
        subscription = {"requested": [code], "failed": {code: str(exc)}}

    now_monotonic = monotonic_fn()
    entry = _cached_entry(code, trade_date, now_monotonic)
    if entry is None:
        lock = _get_code_lock(code)
        with lock:
            # 1m/5m 可能同時進來；進鎖後再查一次避免雙重 kbars()。
            entry = _cached_entry(code, trade_date, monotonic_fn())
            if entry is None:
                entry = _bootstrap_history(
                    code,
                    trade_date,
                    service=service,
                    now_ms=now_value,
                    monotonic_fn=monotonic_fn,
                )

    if interval == "1m":
        history = entry.bars_1m
        live = list(hub.get_live_bars_1m(code) or [])
    else:
        history = entry.bars_5m
        live = list(hub.get_live_bars(code) or [])

    bars = _merge_bars(history, live, interval=interval, trade_date=trade_date)
    # API 被讀取時也立即落盤，避免尚未等到背景收集週期就重啟而遺失。
    try:
        from main_force_store import save_main_force_bars
        save_main_force_bars(code, interval, bars)
    except Exception as exc:
        logger.warning("[Stock Main Force] %s %s 永久保存失敗: %s", code, interval, exc)
    return {
        "status": "ok",
        "code": code,
        "interval": interval,
        "bar_count": len(bars),
        "bars": bars,
        "bootstrap": {
            "trade_date": trade_date,
            "history_1m": len(entry.bars_1m),
            "history_5m": len(entry.bars_5m),
            "live_count": len(live),
            "history_ok": entry.ok,
            "error": entry.error,
            "main_force_history_ok": entry.main_force_ok,
            "main_force_error": entry.main_force_error,
            "subscription": subscription,
            "source": "shioaji_kbars+shioaji_ticks+realtime_hub",
        },
    }
