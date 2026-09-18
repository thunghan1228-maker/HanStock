"""創高黑龍用的六均線(5/10/20/60/120/240日)排列分數。

先用本地daily_bars_store(官方TWSE/TPEx日K)；天數不夠(通常是MA240需要
的240個交易日，官方日K收集器是這次會期才加，舊股票可能還沒累積滿)
才整段退回FinMind TaiwanStockPrice重新取得完整序列，跟otc_gap_backfill.py
同一組付費Sponsor API，避免local跟FinMind兩段資料日期對不齊拼接出錯。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any, Callable

from urllib.parse import urlencode
from urllib.request import Request, urlopen

from daily_bars_store import load_daily_bars

logger = logging.getLogger("hanstock.ma_alignment_score")

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
PRICE_DATASET = "TaiwanStockPrice"
MA_PERIODS = (5, 10, 20, 60, 120, 240)


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


def _default_fetcher(url: str, params: dict[str, str]) -> dict[str, Any]:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "HanStock/1.0 (+https://hanstock.xyz)",
        },
    )
    with urlopen(request, timeout=45) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _fetch_finmind_closes(code: str, needed: int, *, fetcher: Callable[..., Any] | None = None) -> list[float]:
    """回傳最近needed個交易日的收盤價(舊到新)；資料不足或沒有token/失敗
    就回傳[]，讓呼叫端當成「算不出來，暫時跳過」處理。"""
    token = _token()
    if not token:
        return []
    end = date.today()
    start = end - timedelta(days=int(needed * 1.6) + 30)  # 交易日約日曆天*0.68，多留緩衝
    call = fetcher or _default_fetcher
    try:
        payload = call(
            FINMIND_DATA_URL,
            {
                "dataset": PRICE_DATASET, "data_id": str(code).strip().upper(),
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "token": token,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("創高黑龍：FinMind補齊均線歷史失敗 code=%s", code)
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    dated_closes: list[tuple[str, float]] = []
    for entry in rows:
        try:
            close = float(entry["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if close <= 0:
            continue
        dated_closes.append((str(entry.get("date") or ""), close))
    dated_closes.sort(key=lambda item: item[0])
    return [close for _, close in dated_closes]


def _moving_average(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def compute_ma_alignment_score(code: str, *, fetcher: Callable[..., Any] | None = None) -> int | None:
    """六均線(5/10/20/60/120/240)兩兩比較共15組，短天期均線>長天期均線
    算1分，滿分15。任何一條算不出來(資料不足)就回傳None——創高黑龍對
    這檔股票暫時不判斷，是歷史資料還沒累積足夠，不是bug。"""
    needed = max(MA_PERIODS)
    try:
        bars = load_daily_bars(code, limit=needed)
    except Exception:  # noqa: BLE001
        bars = []
    closes = [float(b["close"]) for b in bars]
    if len(closes) < needed:
        closes = _fetch_finmind_closes(code, needed, fetcher=fetcher)
    if len(closes) < needed:
        return None

    mas = {period: _moving_average(closes, period) for period in MA_PERIODS}
    if any(value is None for value in mas.values()):
        return None

    periods = sorted(mas)
    score = 0
    for i in range(len(periods)):
        for j in range(i + 1, len(periods)):
            if mas[periods[i]] > mas[periods[j]]:  # type: ignore[operator]
                score += 1
    return score
