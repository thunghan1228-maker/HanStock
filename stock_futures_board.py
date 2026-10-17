"""股票期貨價格板（2026-10-16 使用者：首頁「股票期貨排名(155)」要跟老師一樣用股期價格算）。

老師的 155 在 08:45～09:00 股票還沒開盤就有漲跌（10/16 08:52 PCB +3.24%），所以用的是股票期貨本身的價格；
空方線切在當下台指期漲跌附近（10/15 08:48 切在 -1.06% 和 -1.17% 之間，那天開盤前台指期約 -1.1%）。

做法：族群（不算排名的除外）裡有股票期貨的標的，每檔取標準型股期 R1 近月合約，用永豐 Snapshot 一次抓一批成交價／漲跌幅，
外加台指期 R1（收盤後給白天盤最後一筆，不給夜盤）；不佔即時訂閱名額（共享連線的訂閱幾乎被全市場現貨逐筆用滿）。快取 60 秒，老師的機器人也是幾分鐘才更新一次。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("hanstock.stock_futures_board")
TW_TZ = timezone(timedelta(hours=8))
CACHE_SECONDS = 60
CONTRACT_RECHECK_SECONDS = 1800      # 近月合約半小時重查一次（換月）
CONTRACT_FAIL_RETRY_SECONDS = 600    # 找不到合約的標的 10 分鐘內不重查
SNAPSHOT_CHUNK = 200

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_contracts: dict[str, tuple[Any, float]] = {}
_contract_failures: dict[str, float] = {}
_tx_day: dict[str, Any] = {"quote": None, "loaded": False}   # 白天盤最後一筆台指期：收盤後（夜盤時間）空方線用它，老師收盤那份是照日盤收盤畫的
TX_DAY_KEY = "tx_day_quote"   # 也存進資料庫：晚上重新部署、週末重啟後還拿得到白天盤收盤（2026-10-17）


def _meta_schema(connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS stock_futures_board_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def _save_tx_day(quote: dict[str, Any]) -> None:
    import json

    from database import get_connection, initialize_database

    try:
        initialize_database()
        with get_connection() as connection:
            _meta_schema(connection)
            connection.execute("INSERT OR REPLACE INTO stock_futures_board_meta (key, value) VALUES (?, ?)", (TX_DAY_KEY, json.dumps(quote, ensure_ascii=False)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Futures Board] 存白天盤台指期失敗：%s", exc)


def _load_tx_day() -> Optional[dict[str, Any]]:
    import json

    from database import get_connection, initialize_database

    try:
        initialize_database()
        with get_connection() as connection:
            _meta_schema(connection)
            row = connection.execute("SELECT value FROM stock_futures_board_meta WHERE key = ?", (TX_DAY_KEY,)).fetchone()
        return json.loads(row["value"]) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Futures Board] 讀白天盤台指期失敗：%s", exc)
        return None


def board_underlyings() -> list[str]:
    """算族群名次的族群裡，有股票期貨的標的。"""
    from stock_groups import STOCK_GROUPS, UNRANKED_GROUP_NAMES
    from stock_trading_eligibility import has_stock_futures

    codes = {
        str(code).strip().upper()
        for name, members in STOCK_GROUPS.items() if name not in UNRANKED_GROUP_NAMES
        for code, _name in members
    }
    return sorted(code for code in codes if has_stock_futures(code))


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _snap_dt(snapshot: Any) -> Optional[datetime]:
    """Snapshot.ts 是「台灣當地時間」當成 UTC 的時間戳（13:44 收盤那筆換算出來會是 21:44），不能再加 8 小時。"""
    raw = _num(getattr(snapshot, "ts", None))
    if not raw or raw <= 0:
        return None
    if raw > 1e16:
        raw /= 1e9
    elif raw > 1e12:
        raw /= 1e3
    try:
        return datetime.fromtimestamp(raw, timezone.utc).replace(tzinfo=TW_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _in_day_session(dt: Optional[datetime]) -> bool:
    if dt is None:
        return False
    hm = dt.strftime("%H:%M")
    return "08:45" <= hm <= "13:45"


def _quote_from_snapshot(snapshot: Any) -> Optional[dict[str, Any]]:
    close = _num(getattr(snapshot, "close", None))
    if not close or close <= 0:
        return None
    change = _num(getattr(snapshot, "change_price", None))
    rate = _num(getattr(snapshot, "change_rate", None))
    reference = close - change if change is not None else None
    if rate is None and reference:
        rate = (close / reference - 1) * 100
    return {
        "price": close,
        "change": change,
        "changePct": round(rate, 3) if rate is not None else None,
        "reference": reference,
        "volume": _num(getattr(snapshot, "total_volume", None)),
        "time": (_snap_dt(snapshot) or datetime.now(TW_TZ)).isoformat(timespec="seconds"),
        "session": "day" if _in_day_session(_snap_dt(snapshot)) else "night",
    }


def _resolve(api: Any, code: str, resolver: Callable[[Any, str, str], Any], now: float) -> Any:
    hit = _contracts.get(code)
    if hit and now - hit[1] < CONTRACT_RECHECK_SECONDS:
        return hit[0]
    failed_at = _contract_failures.get(code)
    if failed_at and now - failed_at < CONTRACT_FAIL_RETRY_SECONDS:
        return None
    try:
        contract = resolver(api, code, "regular")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Futures Board] %s 找不到股期近月合約：%s", code, exc)
        _contract_failures[code] = now
        return hit[0] if hit else None
    _contracts[code] = (contract, now)
    _contract_failures.pop(code, None)
    return contract


def _tx_contract(api: Any) -> Any:
    """台指期近月（R1）：不同版本的 Shioaji 取法不一樣，一個一個試。"""
    futures = getattr(getattr(api, "Contracts", None), "Futures", None)
    if futures is None:
        return None
    for getter in (
        lambda: futures.TXF.TXFR1,
        lambda: futures.TXF["TXFR1"],
        lambda: futures["TXFR1"],
    ):
        try:
            contract = getter()
        except Exception:  # noqa: BLE001
            continue
        if contract is not None:
            return contract
    return None


def _pick_tx(quote: dict[str, Any]) -> dict[str, Any]:
    """白天盤就記下來直接用；夜盤時間改給白天最後一筆（記憶體沒有就讀資料庫；都沒有才照樣給夜盤，標 session=night）。"""
    if quote.get("session") == "day":
        changed = (_tx_day.get("quote") or {}).get("time") != quote.get("time")
        _tx_day.update({"quote": dict(quote), "loaded": True})
        if changed:
            _save_tx_day(quote)
        return quote
    if _tx_day.get("quote") is None and not _tx_day.get("loaded"):
        _tx_day.update({"quote": _load_tx_day(), "loaded": True})
    saved = _tx_day.get("quote")
    if saved:
        return dict(saved, session="day-close")
    return quote


def compute_board(
    api: Any,
    codes: Iterable[str],
    *,
    resolver: Optional[Callable[[Any, str, str], Any]] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    if resolver is None:
        from stock_futures_service import resolve_front_month_contract as resolver  # noqa: N813
    now = time.time() if now is None else now
    wanted = [str(c).strip().upper() for c in codes]
    contract_of: dict[str, Any] = {}
    for code in wanted:
        contract = _resolve(api, code, resolver, now)
        if contract is not None:
            contract_of[code] = contract
    tx = _tx_contract(api)

    pairs: list[tuple[str, Any]] = list(contract_of.items())
    if tx is not None:
        pairs.append(("__TX__", tx))
    quotes: dict[str, Any] = {}
    tx_quote = None
    failed_chunks = 0
    for start in range(0, len(pairs), SNAPSHOT_CHUNK):
        chunk = pairs[start:start + SNAPSHOT_CHUNK]
        try:
            snapshots = list(api.snapshots([contract for _code, contract in chunk]) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Futures Board] Snapshot 失敗：%s", exc)
            failed_chunks += 1
            continue
        by_code = {str(getattr(s, "code", "") or "").strip().upper(): s for s in snapshots}
        for index, (code, contract) in enumerate(chunk):
            snap = (by_code.get(str(getattr(contract, "code", "") or "").strip().upper())
                    or by_code.get(str(getattr(contract, "target_code", "") or "").strip().upper()))
            if snap is None and len(snapshots) == len(chunk):
                snap = snapshots[index]
            quote = _quote_from_snapshot(snap) if snap is not None else None
            if quote is None:
                continue
            if code == "__TX__":
                tx_quote = _pick_tx(quote)
            else:
                quote["futuresCode"] = str(getattr(contract, "target_code", "") or getattr(contract, "code", "") or "")
                quotes[code] = quote
    if pairs and failed_chunks * SNAPSHOT_CHUNK >= len(pairs):
        raise RuntimeError("股期 Snapshot 全部失敗")
    return {
        "status": "ok",
        "requested": len(wanted),
        "resolved": len(contract_of),
        "count": len(quotes),
        "quotes": quotes,
        "tx": tx_quote,
        "generatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
    }


def get_stock_futures_board() -> dict[str, Any]:
    now_mono = time.monotonic()
    with _lock:
        if _cache["value"] is not None and now_mono - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    with _fetch_lock:   # 同時好幾個請求只抓一次
        with _lock:
            if _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
                return _cache["value"]
        from stock_futures_service import get_stock_futures_quote_service

        api = get_stock_futures_quote_service().snapshot_api()
        value = compute_board(api, board_underlyings())
        with _lock:
            _cache.update({"at": time.monotonic(), "value": value})
        return value
