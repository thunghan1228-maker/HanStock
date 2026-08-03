"""HanStock 本機 Shioaji 即時行情同步器（只讀行情、不下單）。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib import error, request

import shioaji as sj

from shioaji_client import shioaji_session


def load_dotenv_simple(path: Path = Path(".env")) -> None:
    """在 VS Code 未注入 .env 時仍可讀取本機設定。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def first_number(values: Any) -> float | int | None:
    if isinstance(values, (list, tuple)) and values:
        return number(values[0])
    return number(values)


def iso_time(value: Any = None) -> str:
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="milliseconds")
    if value:
        return str(value)
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class QuoteBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, dict[str, Any]] = {}

    def seed(self, contract: Any, snapshot: Any | None) -> None:
        code = str(getattr(contract, "code", ""))
        name = str(getattr(contract, "name", ""))
        close = number(getattr(snapshot, "close", None)) if snapshot is not None else None
        change_price = number(getattr(snapshot, "change_price", None)) if snapshot is not None else None
        previous_close = None
        if close is not None and change_price is not None:
            previous_close = number(float(close) - float(change_price))
        with self._lock:
            self._quotes[code] = {
                "code": code,
                "name": name,
                "price": close,
                "change_rate": number(getattr(snapshot, "change_rate", None)) if snapshot is not None else None,
                "price_change": change_price,
                "volume": number(getattr(snapshot, "total_volume", None)) if snapshot is not None else None,
                "bid": number(getattr(snapshot, "buy_price", None)) if snapshot is not None else None,
                "ask": number(getattr(snapshot, "sell_price", None)) if snapshot is not None else None,
                "open": number(getattr(snapshot, "open", None)) if snapshot is not None else None,
                "high": number(getattr(snapshot, "high", None)) if snapshot is not None else None,
                "low": number(getattr(snapshot, "low", None)) if snapshot is not None else None,
                "previous_close": previous_close,
                "updated_at": iso_time(getattr(snapshot, "ts", None)) if snapshot is not None else iso_time(),
            }

    def update_tick(self, tick: Any) -> None:
        code = str(getattr(tick, "code", ""))
        if not code:
            return
        with self._lock:
            quote = self._quotes.setdefault(code, {"code": code, "name": ""})
            price = number(getattr(tick, "close", None))
            if price is not None:
                quote["price"] = price
                previous_close = number(quote.get("previous_close"))
                if previous_close not in (None, 0):
                    change = float(price) - float(previous_close)
                    quote["price_change"] = number(change)
                    quote["change_rate"] = number(change / float(previous_close) * 100)
            volume = number(getattr(tick, "total_volume", None))
            if volume is not None:
                quote["volume"] = volume
            if price is not None:
                high = number(quote.get("high"))
                low = number(quote.get("low"))
                quote["high"] = price if high is None else max(float(high), float(price))
                quote["low"] = price if low is None else min(float(low), float(price))
            quote["updated_at"] = iso_time(getattr(tick, "datetime", None))

    def update_bidask(self, data: Any) -> None:
        code = str(getattr(data, "code", ""))
        if not code:
            return
        with self._lock:
            quote = self._quotes.setdefault(code, {"code": code, "name": ""})
            bid = first_number(getattr(data, "bid_price", None))
            ask = first_number(getattr(data, "ask_price", None))
            if bid is not None:
                quote["bid"] = bid
            if ask is not None:
                quote["ask"] = ask
            quote["updated_at"] = iso_time(getattr(data, "datetime", None))

    def payload(self) -> dict[str, Any]:
        with self._lock:
            quotes = [dict(value) for value in self._quotes.values() if value.get("price") is not None]
        latest_times = [item.get("updated_at") for item in quotes if item.get("updated_at")]
        return {
            "source": "shioaji-local",
            "updated_at": max(latest_times) if latest_times else iso_time(),
            "quotes": quotes,
        }


def post_payload(url: str, token: str, payload: dict[str, Any], timeout: int = 8) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-HanStock-Sync-Token": token,
            "User-Agent": "HanStock-Realtime-Sync/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"雲端回應 HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"無法連到雲端 API：{exc.reason}") from exc


def quote_client(api: Any) -> Any:
    return api if hasattr(api, "subscribe") else api.quote


def stock_contracts(api: Any) -> Any:
    contracts = getattr(api, "contracts", None)
    if contracts is None:
        contracts = api.Contracts
    return contracts.Stocks


def main() -> None:
    parser = argparse.ArgumentParser(description="將 Shioaji 即時行情同步到 HanStock 網站")
    parser.add_argument("--codes", default="2330", help="逗號分隔股票代號，例如 2330,2344")
    parser.add_argument("--interval", type=float, default=2.0, help="同步間隔秒數，預設 2 秒")
    parser.add_argument("--cloud-url", default=None, help="覆蓋 HANSTOCK_CLOUD_API_URL")
    parser.add_argument("--dry-run", action="store_true", help="只顯示行情，不送到雲端")
    args = parser.parse_args()

    load_dotenv_simple()
    codes = list(dict.fromkeys(item.strip() for item in args.codes.split(",") if item.strip()))
    cloud_base = (args.cloud_url or os.getenv("HANSTOCK_CLOUD_API_URL") or "https://hanstock.xyz").rstrip("/")
    sync_url = f"{cloud_base}/api/realtime/sync"
    token = os.getenv("HANSTOCK_SYNC_TOKEN", "")
    if not args.dry_run and not token:
        raise SystemExit("缺少 HANSTOCK_SYNC_TOKEN，請先檢查 .env。")

    book = QuoteBook()
    with shioaji_session() as api:
        stocks = stock_contracts(api)
        contracts = []
        for code in codes:
            contract = stocks[code]
            if contract is None or not getattr(contract, "code", None):
                print(f"略過找不到的股票代號：{code}")
                continue
            contracts.append(contract)
        if not contracts:
            raise SystemExit("沒有可訂閱的股票代號。")

        snapshots = api.snapshots(contracts)
        snapshot_by_code = {str(getattr(item, "code", "")): item for item in snapshots}
        for contract in contracts:
            book.seed(contract, snapshot_by_code.get(str(contract.code)))

        def on_tick(data: Any) -> None:
            book.update_tick(data)

        def on_bidask(data: Any) -> None:
            book.update_bidask(data)

        if hasattr(api, "set_on_tick_stk_v1_callback"):
            api.set_on_tick_stk_v1_callback(on_tick)
            api.set_on_bidask_stk_v1_callback(on_bidask)
        else:
            api.quote.set_on_tick_stk_v1_callback(on_tick)
            api.quote.set_on_bidask_stk_v1_callback(on_bidask)

        client = quote_client(api)
        for contract in contracts:
            client.subscribe(contract, quote_type=sj.QuoteType.Tick)
            client.subscribe(contract, quote_type=sj.QuoteType.BidAsk)

        print(f"已訂閱：{', '.join(str(c.code) for c in contracts)}")
        print("只讀行情，不會下單。按 Ctrl+C 停止。")
        if args.dry_run:
            print("目前為 dry-run，不會送往網站。")
        else:
            print(f"同步目標：{sync_url}")

        try:
            while True:
                payload = book.payload()
                if args.dry_run:
                    summary = ", ".join(f"{q['code']}={q.get('price')}" for q in payload["quotes"])
                    print(f"{datetime.now():%H:%M:%S}｜{summary}", flush=True)
                else:
                    try:
                        result = post_payload(sync_url, token, payload)
                        print(
                            f"{datetime.now():%H:%M:%S}｜同步成功 "
                            f"{result.get('quote_count', len(payload['quotes']))} 檔",
                            flush=True,
                        )
                    except RuntimeError as exc:
                        print(f"{datetime.now():%H:%M:%S}｜同步失敗：{exc}", flush=True)
                time.sleep(max(1.0, args.interval))
        except KeyboardInterrupt:
            print("\n正在停止訂閱…")
        finally:
            for contract in contracts:
                for quote_type in (sj.QuoteType.Tick, sj.QuoteType.BidAsk):
                    try:
                        client.unsubscribe(contract, quote_type=quote_type)
                    except Exception as exc:
                        print(f"取消 {contract.code} 訂閱時收到訊息：{exc}")


if __name__ == "__main__":
    main()
