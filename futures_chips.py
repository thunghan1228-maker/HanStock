"""外資空單（2026-10-15 使用者：照老師「外資空單 × 散戶小台・微台 × 加權指數」那頁做，放研究室）。

資料（都是公開資料、免費）：
- FinMind：台指期 TX／小台 MTX／微台 TMF 三大法人未平倉（TaiwanFuturesInstitutionalInvestors）、
  台指選擇權 TXO 三大法人未平倉（TaiwanOptionInstitutionalInvestors）、小台／微台全市場未平倉（TaiwanFuturesDaily，
  一般交易時段各月份加總、不含價差單）、加權指數（TaiwanStockPrice TAIEX）、上市三大法人現貨買賣金額（TaiwanStockTotalInstitutionalInvestors）。
- 期交所「臺指選擇權 Put/Call Ratio」（pcRatio）：全市場 P/C 未平倉比率。不用 FinMind 選擇權日行情自己加，
  因為週選到期那天它會把當天到期的契約也算進去（10/14 算出 83.8%，期交所和老師都是 74.44%）。

怎麼算（2026-10-15 對老師 10/15 那頁，每個數字都一樣）：
- 外資台指淨未平倉＝TX 外資多方未平倉口數 − 空方未平倉口數（負＝淨空單）；投信淨、自營淨同理；增減＝跟前一個交易日比。
- 外資買權／賣權淨未平倉＝TXO 外資買權（賣權）多方未平倉 − 空方未平倉（口）。
- 散戶小台（微台）淨未平倉＝−（三大法人小台（微台）淨未平倉合計）；散戶多空比＝散戶淨未平倉 ÷ 全市場未平倉量。
- 三大法人現貨買賣超（億）：外資＝外資及陸資＋外資自營商、投信、自營商＝自行買賣＋避險。
每個交易日 15:40 抓一次，17:40、20:40 補抓（資料晚出來時）；第一次抓近 400 天。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
PC_URL = "https://www.taifex.com.tw/cht/3/pcRatio"
HISTORY_DAYS = 400           # 第一次抓多久（圖最長 1 年）
REFETCH_DAYS = 10            # 之後每次重抓最近幾天
PC_CHUNK_DAYS = 28           # 期交所 P/C 一次最多查 30 天
PC_RETRIES = 3
PC_PAUSE = 1.0               # 秒
UPDATE_SLOTS = ((15, 40), (17, 40), (20, 40))
POLL_SECONDS = 10 * 60
CHART_DAYS = 250
INST = {"外資": "f", "投信": "t", "自營商": "d"}
SPOT = {"Foreign_Investor": "spot_f", "Foreign_Dealer_Self": "spot_f", "Investment_Trust": "spot_t",
        "Dealer_self": "spot_d", "Dealer_Hedging": "spot_d", "total": "spot_total"}

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "lastFetch": None, "lastError": None, "pcError": None, "rows": 0}


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS futures_chips_series (series TEXT NOT NULL, trade_date TEXT NOT NULL, value REAL NOT NULL, "
        "PRIMARY KEY (series, trade_date))"
    )


def _now() -> datetime:
    return datetime.now(TW_TZ)


def _default_fetcher(params: dict[str, str]) -> Any:
    def call(extra: dict[str, str]) -> Any:
        url = FINMIND_URL + "?" + urllib.parse.urlencode({**params, **extra})
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.load(response)

    try:
        return call({})
    except Exception:  # noqa: BLE001
        token = os.getenv("FINMIND_TOKEN", "").strip()
        if not token:
            raise
        return call({"token": token})


def _default_pc_fetcher(start: date, end: date) -> str:
    body = urllib.parse.urlencode({"queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d")}).encode()
    request = urllib.request.Request(PC_URL, data=body, headers={"User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)",
                                                                  "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="ignore")


def _rows(payload: Any) -> list[dict[str, Any]]:
    return list((payload or {}).get("data") or [])


def parse_inst_net(payload: Any) -> dict[str, dict[str, float]]:
    """期貨三大法人：{f/t/d: {日期: 多方未平倉 − 空方未平倉（口）}}。"""
    out: dict[str, dict[str, float]] = {k: {} for k in INST.values()}
    for r in _rows(payload):
        key = INST.get(r.get("institutional_investors"))
        if key and r.get("date"):
            out[key][r["date"]] = float(r.get("long_open_interest_balance_volume") or 0) - float(r.get("short_open_interest_balance_volume") or 0)
    return out


def parse_option_foreign(payload: Any) -> dict[str, dict[str, float]]:
    """台指選擇權外資：{call/put: {日期: 多方未平倉 − 空方未平倉（口）}}。"""
    out: dict[str, dict[str, float]] = {"call": {}, "put": {}}
    side = {"買權": "call", "賣權": "put"}
    for r in _rows(payload):
        key = side.get(r.get("call_put"))
        if key and r.get("institutional_investors") == "外資" and r.get("date"):
            out[key][r["date"]] = float(r.get("long_open_interest_balance_volume") or 0) - float(r.get("short_open_interest_balance_volume") or 0)
    return out


def parse_total_oi(payload: Any) -> dict[str, float]:
    """全市場未平倉量：一般交易時段（position）各月份加總，不含價差單（契約月份有「/」的）。"""
    out: dict[str, float] = {}
    for r in _rows(payload):
        if r.get("trading_session") == "position" and "/" not in str(r.get("contract_date") or "") and r.get("date"):
            out[r["date"]] = out.get(r["date"], 0.0) + float(r.get("open_interest") or 0)
    return {d: v for d, v in out.items() if v > 0}


def parse_spot(payload: Any) -> dict[str, dict[str, float]]:
    """上市三大法人現貨買賣超（億）。"""
    out: dict[str, dict[str, float]] = {k: {} for k in set(SPOT.values())}
    for r in _rows(payload):
        key = SPOT.get(r.get("name"))
        if key and r.get("date") and r.get("buy") is not None and r.get("sell") is not None:
            out[key][r["date"]] = out[key].get(r["date"], 0.0) + (float(r["buy"]) - float(r["sell"])) / 1e8
    return out


def parse_index(payload: Any) -> dict[str, float]:
    return {r["date"]: float(r["close"]) for r in _rows(payload) if r.get("date") and r.get("close")}


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_pc_ratio(page: str) -> dict[str, dict[str, float]]:
    """期交所 pcRatio 表格：日期、賣權成交量、買權成交量、成交量比率%、賣權未平倉、買權未平倉、未平倉比率%。"""
    out: dict[str, dict[str, float]] = {"pc_oi": {}, "pc_vol": {}}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page or "", re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", cells[0]) if cells else None
        if not m or len(cells) < 7:
            continue
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        vol, oi = _num(cells[3]), _num(cells[6])
        if oi is not None:
            out["pc_oi"][day] = oi
        if vol is not None:
            out["pc_vol"][day] = vol
    return out


def _last_date(connection, series: str) -> str | None:
    row = connection.execute("SELECT MAX(trade_date) AS d FROM futures_chips_series WHERE series = ?", (series,)).fetchone()
    return row["d"] if row else None


def fetch(*, fetcher: Callable[[dict[str, str]], Any] | None = None, pc_fetcher: Callable[[date, date], str] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    call = fetcher or _default_fetcher
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        last = _last_date(connection, "tx_f")
        last_pc = _last_date(connection, "pc_oi")
    today = now.date()
    start = (today - timedelta(days=HISTORY_DAYS)) if not last else (date.fromisoformat(last) - timedelta(days=REFETCH_DAYS))
    s = start.isoformat()
    got: dict[str, dict[str, float]] = {}
    for fid in ("TX", "MTX", "TMF"):
        nets = parse_inst_net(call({"dataset": "TaiwanFuturesInstitutionalInvestors", "data_id": fid, "start_date": s}))
        if fid == "TX":
            got.update({"tx_" + k: v for k, v in nets.items()})
        else:   # 小台／微台只要三大法人合計
            total: dict[str, float] = {}
            for v in nets.values():
                for d, x in v.items():
                    total[d] = total.get(d, 0.0) + x
            got[fid.lower() + "_inst"] = total
            got[fid.lower() + "_oi"] = parse_total_oi(call({"dataset": "TaiwanFuturesDaily", "data_id": fid, "start_date": s}))
    opt = parse_option_foreign(call({"dataset": "TaiwanOptionInstitutionalInvestors", "data_id": "TXO", "start_date": s}))
    got["txo_fc"], got["txo_fp"] = opt["call"], opt["put"]
    got["taiex"] = parse_index(call({"dataset": "TaiwanStockPrice", "data_id": "TAIEX", "start_date": s}))
    got.update(parse_spot(call({"dataset": "TaiwanStockTotalInstitutionalInvestors", "start_date": s})))
    if not got["tx_f"] or not got["taiex"]:
        raise RuntimeError("台指期三大法人或加權指數抓到 0 筆")
    # 期交所 P/C：一次最多 30 天，第一次分段補齊；期交所偶爾回 520，每段重試幾次、段跟段之間停一下，抓不到的段下次再補
    pc_error = None
    pc_start = (today - timedelta(days=HISTORY_DAYS)) if not last_pc else (date.fromisoformat(last_pc) - timedelta(days=REFETCH_DAYS))
    got["pc_oi"], got["pc_vol"] = {}, {}
    cur = pc_start
    while cur <= today:
        end = min(today, cur + timedelta(days=PC_CHUNK_DAYS))
        for attempt in range(PC_RETRIES):
            try:
                pc = parse_pc_ratio((pc_fetcher or _default_pc_fetcher)(cur, end))
                got["pc_oi"].update(pc["pc_oi"])
                got["pc_vol"].update(pc["pc_vol"])
                break
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 == PC_RETRIES:
                    pc_error = f"{cur.isoformat()}～{end.isoformat()} {type(exc).__name__}: {exc}"[:200]
                else:
                    time.sleep(PC_PAUSE * (attempt + 2))
        cur = end + timedelta(days=1)
        if cur <= today:
            time.sleep(PC_PAUSE)
    rows = [(series, d, v) for series, values in got.items() for d, v in values.items()]
    with get_connection() as connection:
        _schema(connection)
        connection.executemany("INSERT INTO futures_chips_series (series, trade_date, value) VALUES (?, ?, ?) "
                               "ON CONFLICT(series, trade_date) DO UPDATE SET value = excluded.value", rows)
    with _lock:
        _state.update({"lastFetch": now.isoformat(timespec="seconds"), "lastError": None, "pcError": pc_error, "rows": len(rows)})
    return {"rows": len(rows), "from": s, "latest": max(got["tx_f"]), "pcError": pc_error}


def _load() -> dict[str, dict[str, float]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT series, trade_date, value FROM futures_chips_series ORDER BY trade_date").fetchall()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        out.setdefault(r["series"], {})[r["trade_date"]] = r["value"]
    return out


def _r(v: float | None, digits: int = 0) -> float | int | None:
    if v is None:
        return None
    return int(round(v)) if digits == 0 else round(v, digits)


def payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    data = _load()
    tx_f, taiex = data.get("tx_f", {}), data.get("taiex", {})
    days = sorted(set(tx_f) & set(taiex))
    if len(days) < 2:
        return {"status": "missing", "message": "資料還沒抓齊（後端剛部署時約 2 分鐘後會抓第一次）"}
    as_of, prev = days[-1], days[-2]

    def val(series: str, d: str) -> float | None:
        return data.get(series, {}).get(d)

    def retail(kind: str, d: str) -> float | None:
        inst = val(kind + "_inst", d)
        return -inst if inst is not None else None

    def ratio(kind: str, d: str) -> float | None:
        net, oi = retail(kind, d), val(kind + "_oi", d)
        return round(net / oi * 100, 2) if net is not None and oi else None

    def card(cur: float | None, before: float | None, digits: int = 0) -> dict[str, Any]:
        chg = cur - before if cur is not None and before is not None else None
        return {"value": _r(cur, digits), "prev": _r(before, digits), "chg": _r(chg, digits)}

    t_cur, t_prev = taiex[as_of], taiex[prev]
    cards = {
        "taiex": {"value": round(t_cur, 2), "chg": round(t_cur - t_prev, 2), "pct": round((t_cur / t_prev - 1) * 100, 2)},
        "txForeign": card(val("tx_f", as_of), val("tx_f", prev)),
        "txTrust": card(val("tx_t", as_of), val("tx_t", prev)),
        "txDealer": card(val("tx_d", as_of), val("tx_d", prev)),
        "callForeign": card(val("txo_fc", as_of), val("txo_fc", prev)),
        "putForeign": card(val("txo_fp", as_of), val("txo_fp", prev)),
        "mtxRetail": card(retail("mtx", as_of), retail("mtx", prev)),
        "tmfRetail": card(retail("tmf", as_of), retail("tmf", prev)),
        "mtxRatio": card(ratio("mtx", as_of), ratio("mtx", prev), 2),
        "tmfRatio": card(ratio("tmf", as_of), ratio("tmf", prev), 2),
        "pcRatio": card(val("pc_oi", as_of), val("pc_oi", prev), 2),
        "spot": {"foreign": _r(val("spot_f", as_of), 2), "trust": _r(val("spot_t", as_of), 2), "dealer": _r(val("spot_d", as_of), 2),
                 "total": _r(val("spot_total", as_of), 2), "date": as_of if val("spot_total", as_of) is not None else None},
    }
    series = []
    for i, d in enumerate(days[-(CHART_DAYS + 1):]):
        if i == 0 and len(days) > CHART_DAYS:
            continue        # 多拿一天只是為了算第一天的增減
        before = days[days.index(d) - 1] if days.index(d) > 0 else None
        f, fb = val("tx_f", d), val("tx_f", before) if before else None
        series.append({
            "d": d, "t": round(taiex[d], 2), "f": _r(f), "fc": _r(f - fb) if f is not None and fb is not None else None,
            "it": _r(val("tx_t", d)), "dl": _r(val("tx_d", d)), "call": _r(val("txo_fc", d)), "put": _r(val("txo_fp", d)),
            "mr": _r(retail("mtx", d)), "tr": _r(retail("tmf", d)), "mq": ratio("mtx", d), "tq": ratio("tmf", d), "pc": _r(val("pc_oi", d), 2),
            "sf": _r(val("spot_f", d), 2), "st": _r(val("spot_t", d), 2), "sd": _r(val("spot_d", d), 2), "s": _r(val("spot_total", d), 2),
        })
    return {
        "status": "ok", "asOf": as_of, "prevDate": prev, "updatedAt": _state.get("lastFetch"), "now": now.isoformat(timespec="seconds"),
        "pcError": _state.get("pcError"), "cards": cards, "series": series,
        "source": "FinMind 公開資料（期交所三大法人期貨／選擇權未平倉、小台微台全市場未平倉、加權指數、證交所上市三大法人買賣金額）"
                  "＋期交所臺指選擇權 Put/Call Ratio；每個交易日 15:40 更新（17:40、20:40 補抓）",
    }


def due(now: datetime, last_fetch: str | None) -> bool:
    """平日 15:40、17:40、20:40 各抓一次（過了哪一個時段、上次抓的時間還在那之前就抓）；剛啟動沒抓過也先抓一次。"""
    if last_fetch is None:
        return True
    if now.weekday() >= 5:
        return False
    passed = [f"{h:02d}:{m:02d}" for h, m in UPDATE_SLOTS if (now.hour, now.minute) >= (h, m)]
    if not passed:
        return False
    return last_fetch[:10] < now.date().isoformat() or last_fetch[11:16] < passed[-1]


def _loop() -> None:
    time.sleep(120)
    while True:
        try:
            if due(_now(), _state.get("lastFetch")):
                fetch()
        except Exception as exc:  # noqa: BLE001
            logger.warning("futures chips fetch failed: %s", exc)
            with _lock:
                _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
        time.sleep(POLL_SECONDS)


def start_futures_chips_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="futures-chips", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)
