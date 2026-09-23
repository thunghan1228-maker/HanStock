"""處置股預測Phase 3：FinMind Sponsor帳號抓當日沖銷成交量(第十三款，也是官方規則裡
唯一決定處置期間5天/7天的款)跟借券賣出成交量(第十二款)，只服務43個官方族群股票。

跟Phase 2(finmind_disposition_fundamentals_collector.py)不同，這裡兩個資料集都用
「全市場單日一次查詢」模式(不帶data_id)而不是逐檔查詢——TaiwanStockDayTrading跟
TaiwanDailyShortSaleBalances都支援Backer/Sponsor全市場單日查詢，一天只需要2次API
呼叫(不是524*2次)，減輕跟其他收集器共用的每小時額度負擔。

資料集(已用官方文件/llms-full.txt核對過欄位)：
  - TaiwanStockDayTrading(當日沖銷交易標的及成交量值)：只取Volume(當日沖銷成交量)。
  - TaiwanDailyShortSaleBalances(信用額度總量管制餘額表)：只取SBLShortSalesShortSales
    (借券賣出成交量)，不是同一個FinMind資料集的TaiwanStockMarginPurchaseShortSale
    (那個是融資融券，Phase 2已經在用)。

單位假設：兩個欄位都當作「張」處理，跟bars_1d的volume(張)同單位、也跟現有
TaiwanStockMarginPurchaseShortSale(Phase 2已在用，同樣沒做單位轉換)一致——這是
「餘額/目標名單」類報表的官方慣例，不是逐筆成交明細的「股數」慣例；但這裡沒有在沙盒
環境用真實API回應驗證過，之後若跟TaiwanStockDispositionSecuritiesPeriod的官方處置
期間資料對照發現數量級不對，要回來檢查這個假設。

FinMind回應裡沒有出現的股票，代表當天SBL賣出量/當沖量是0(這兩個都是「有出現在目標
名單/報表才有數字」的資料集)，會存0(不是略過不存)；但如果整個資料集的API呼叫本身失敗
(網路/認證問題)，那天完全不存任何row，讓disposition_phase3_assembly.py能區分「有收集
過(值可能是0)」跟「沒收集過」。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any

from disposition_phase3_store import save_day_trading_rows, save_sbl_short_sale_rows

logger = logging.getLogger("hanstock.finmind_disposition_phase3")
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"


def _token() -> str:
    return os.getenv("FINMIND_TOKEN", "").strip()


class RollingHourlyLimiter:
    """同其他FinMind收集器：Sponsor每小時6,000次，這裡一天只打2次，額度設寬鬆一點
    即可，但沿用同一套class方便一致的retry/backoff行為。"""

    def __init__(self, limit: int = 2000, window_seconds: float = 3600.0) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                wait_seconds = self.window_seconds - (now - self._calls[0]) + 0.25
            time.sleep(max(0.25, wait_seconds))


_hourly_limiter = RollingHourlyLimiter(
    limit=int(os.getenv("FINMIND_DISPOSITION_SAFE_HOURLY_LIMIT", "2000"))
)


def _request_json(dataset: str, trade_date: str, *, retries: int = 3) -> list[dict[str, Any]]:
    """全市場單日查詢：只帶start_date、不帶data_id(Backer/Sponsor tier才支援)。"""
    token = _token()
    if not token:
        raise RuntimeError("FINMIND_TOKEN 尚未設定")
    params = {"dataset": dataset, "start_date": trade_date}
    request = urllib.request.Request(
        f"{FINMIND_DATA_URL}?{urllib.parse.urlencode(params)}",
        headers={
            "Accept": "application/json", "Authorization": f"Bearer {token}",
            "User-Agent": "HanStock-FinMind-DispositionPhase3Collector/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        _hourly_limiter.acquire()
        try:
            with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
                payload = json.load(response)
            data = payload.get("data") if isinstance(payload, dict) else None
            return data if isinstance(data, list) else []
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 30.0
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 >= retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(str(last_error or "FinMind request failed"))


def fetch_day_trading_volume_by_code(trade_date: str) -> dict[str, float]:
    rows = _request_json("TaiwanStockDayTrading", trade_date)
    result: dict[str, float] = {}
    for row in rows:
        code = str(row.get("stock_id") or "").strip()
        volume = row.get("Volume")
        if code and volume is not None:
            result[code] = float(volume)
    return result


def fetch_sbl_short_sale_volume_by_code(trade_date: str) -> dict[str, float]:
    rows = _request_json("TaiwanDailyShortSaleBalances", trade_date)
    result: dict[str, float] = {}
    for row in rows:
        code = str(row.get("stock_id") or "").strip()
        volume = row.get("SBLShortSalesShortSales")
        if code and volume is not None:
            result[code] = float(volume)
    return result


def collect_trade_date(trade_date: str, codes: list[str]) -> dict[str, Any]:
    if not _token():
        return {"status": "skipped", "reason": "FINMIND_TOKEN 未設定", "tradeDate": trade_date}
    code_set = set(codes)
    failures: list[str] = []
    saved_day_trading = 0
    saved_sbl = 0

    try:
        day_trading_by_code = fetch_day_trading_volume_by_code(trade_date)
        rows = [
            {"code": code, "tradeDate": trade_date, "volume": day_trading_by_code.get(code, 0.0)}
            for code in code_set
        ]
        saved_day_trading = save_day_trading_rows(rows)
    except Exception as error:  # noqa: BLE001
        failures.append(f"TaiwanStockDayTrading:{error}")
        logger.warning("[DispositionPhase3] 當沖成交量抓取失敗，今天先不存這個資料集: %s", error)

    try:
        sbl_by_code = fetch_sbl_short_sale_volume_by_code(trade_date)
        rows = [
            {"code": code, "tradeDate": trade_date, "volume": sbl_by_code.get(code, 0.0)}
            for code in code_set
        ]
        saved_sbl = save_sbl_short_sale_rows(rows)
    except Exception as error:  # noqa: BLE001
        failures.append(f"TaiwanDailyShortSaleBalances:{error}")
        logger.warning("[DispositionPhase3] 借券賣出成交量抓取失敗，今天先不存這個資料集: %s", error)

    return {
        "status": "ok", "tradeDate": trade_date, "requested": len(code_set),
        "savedDayTrading": saved_day_trading, "savedSblShortSale": saved_sbl,
        "failed": failures,
    }
