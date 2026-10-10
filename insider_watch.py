"""內部人研究室（2026-10-10 使用者：照莊爸「內部人研究室 INSIDER WATCH」做，但不只三檔，上市櫃全部）。

資料：公開資訊觀測站「董事、監察人、經理人及百分之十以上大股東股權異動彙總表」（IRB110，每月 15 日申報截止後約第三週出來），
觀測站擋正式站主機，走 tw-groups data 分支鏡像（tpex/insider-YYYY-MM.json，.github/workflows/insider-mops.yml）：
每家公司董監本月增加／減少股數、董監／經理人／10% 大股東持股；異動最大的公司另外查「內部人持股異動事後申報表」，
分出集中市場買進／賣出（真金白銀）跟其他原因（贈與、信託、繼承…）。

算法：
- 董監淨增減＝本月增加－本月減少；經理人、大股東用持股跟上個月比。內部人淨增減＝董監＋經理人（大股東常常就是董監，另列不加總）。
- 配股、減資會讓持股同比例變動（8 月配股季最明顯），不是買賣：股本比上月多（少）幾 %，就把上月董監＋經理人持股 × 那個比例扣掉；
  只扣跟股本同方向的部分、最多扣到實際變動量（現金增資、可轉債轉換讓股本變多但內部人沒動，不能算成賣出）。
- 金額＝股數 × 那個月我們日K的平均收盤（估算）；集中市場金額同樣估算。
- 疊上集保 400 張以上大戶持股比（最新一週）與近 4 週變化（百分點）：內部人買＋大戶增＝「雙買」，內部人賣＋大戶減＝「雙賣」。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from chips_daily import _default_fetcher, _mirror_url
from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
MIRROR_INDEX = "insider-index.json"
KEEP_MONTHS = 13
TOP_N = 25
BIG_LEVELS = (12, 13, 14, 15)     # 400 張以上
TDCC_WEEKS = 5                    # 最新一週＋往回 4 週
CAPITAL_CHANGE_MIN = 0.0005       # 股本變動超過 0.05% 才當配股／減資校正
POLL_SECONDS = 60 * 60
COLLECT_AT = (21, 30)             # 鏡像 20:40 跑完；之後每天抓一次
FIELDS = ["code", "name", "market", "issued", "dirInc", "dirDec", "dirHold", "dirPct", "mgrHold", "bigHold"]

_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "lastCollect": None, "lastError": None, "months": []}
_cache: dict[str, Any] = {}


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS insider_month (month TEXT PRIMARY KEY, published TEXT, fetched TEXT, payload TEXT NOT NULL)"
    )


def _now() -> datetime:
    return datetime.now(TW_TZ)


def parse_mirror(payload: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("month"):
        raise ValueError("鏡像格式不對")
    fields = payload.get("fields") or FIELDS
    rows = []
    for r in payload.get("rows") or []:
        if isinstance(r, list) and len(r) >= len(FIELDS):
            row = dict(zip(fields, r))
            row["code"] = str(row["code"]).strip().upper()
            rows.append(row)
    return str(payload["month"]), rows, payload.get("detail") or {}


def collect(*, fetcher: Callable[[str], Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    call = fetcher or _default_fetcher
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "months": {}, "errors": []}
    index = call(_mirror_url(MIRROR_INDEX, volatile=True))
    months = [str(m) for m in (index.get("months") if isinstance(index, dict) else None) or []][:KEEP_MONTHS]
    initialize_database()
    for month in months:
        try:
            raw = call(_mirror_url(f"insider-{month}.json", volatile=True))
            got, rows, detail = parse_mirror(raw)
            if got != month:
                raise RuntimeError(f"鏡像月份 {got} 不是 {month}")
            with get_connection() as connection:
                _schema(connection)
                connection.execute(
                    "INSERT INTO insider_month (month, published, fetched, payload) VALUES (?, ?, ?, ?) ON CONFLICT(month) DO UPDATE SET "
                    "published = excluded.published, fetched = excluded.fetched, payload = excluded.payload",
                    (month, json.dumps(raw.get("published") or {}, ensure_ascii=False), raw.get("fetched"),
                     json.dumps({"rows": rows, "detail": detail, "published": raw.get("published") or {}}, ensure_ascii=False)),
                )
            result["months"][month] = {"rows": len(rows), "detail": len(detail)}
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{month}: {type(exc).__name__}: {exc}")
    with _lock:
        _state.update({"lastCollect": result["at"], "lastError": "; ".join(result["errors"]) or None, "months": list(result["months"])})
        _cache.clear()
    return result


def load_months() -> list[str]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return [r["month"] for r in connection.execute("SELECT month FROM insider_month ORDER BY month DESC").fetchall()]


def _load_month(month: str) -> dict[str, Any] | None:
    key = "m:" + month
    if key in _cache:
        return _cache[key]
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT payload, fetched FROM insider_month WHERE month = ?", (month,)).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["fetched"] = row["fetched"]
    data["byCode"] = {r["code"]: r for r in data["rows"]}
    _cache[key] = data
    return data


def _prev_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1:04d}-12" if mon == 1 else f"{year:04d}-{mon - 1:02d}"


def _next_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year + 1:04d}-01" if mon == 12 else f"{year:04d}-{mon + 1:02d}"


def avg_close(codes: list[str], month: str) -> dict[str, float]:
    """那個月日K平均收盤（估金額用）；那個月沒有日K就用之後最近的收盤。"""
    if not codes:
        return {}
    start, end = month + "-01", _next_month(month) + "-01"
    out: dict[str, float] = {}
    initialize_database()
    with get_connection() as connection:
        for i in range(0, len(codes), 400):
            batch = codes[i:i + 400]
            marks = ",".join("?" for _ in batch)
            for r in connection.execute(
                f"SELECT stock_code, AVG(close) AS c FROM bars_1d WHERE bar_time >= ? AND bar_time < ? AND close > 0 AND stock_code IN ({marks}) "
                "GROUP BY stock_code", (start, end, *batch),
            ).fetchall():
                out[str(r["stock_code"]).upper()] = float(r["c"])
            missing = [c for c in batch if c not in out]
            if missing:
                marks = ",".join("?" for _ in missing)
                for r in connection.execute(
                    f"SELECT b.stock_code, b.close FROM bars_1d b JOIN (SELECT stock_code, MIN(bar_time) AS t FROM bars_1d WHERE bar_time >= ? AND close > 0 "
                    f"AND stock_code IN ({marks}) GROUP BY stock_code) m ON m.stock_code = b.stock_code AND m.t = b.bar_time", (end, *missing),
                ).fetchall():
                    out[str(r["stock_code"]).upper()] = float(r["close"])
    return out


def big_holders(codes: list[str]) -> dict[str, dict[str, Any]]:
    """集保 400 張以上大戶持股比：最新一週、近 4 週變化（百分點）、每週序列。"""
    if not codes:
        return {}
    from fundamentals_daily import tdcc_dates

    dates = tdcc_dates(TDCC_WEEKS + 7)
    if not dates:
        return {}
    series: dict[str, dict[str, float]] = {}
    with get_connection() as connection:
        for i in range(0, len(codes), 300):
            batch = codes[i:i + 300]
            for r in connection.execute(
                f"SELECT data_date, stock_code, SUM(pct) AS p FROM tdcc_weekly WHERE data_date IN ({','.join('?' for _ in dates)}) "
                f"AND stock_code IN ({','.join('?' for _ in batch)}) AND level IN ({','.join(str(x) for x in BIG_LEVELS)}) GROUP BY data_date, stock_code",
                (*dates, *batch),
            ).fetchall():
                series.setdefault(str(r["stock_code"]).upper(), {})[str(r["data_date"])] = round(float(r["p"]), 2)
    out: dict[str, dict[str, Any]] = {}
    for code, by_date in series.items():
        ds = sorted(by_date)
        latest = ds[-1]
        base = ds[-TDCC_WEEKS] if len(ds) >= TDCC_WEEKS else ds[0]
        out[code] = {"date": latest, "pct": by_date[latest], "chg4w": round(by_date[latest] - by_date[base], 2) if base != latest else None,
                     "weeks": [{"d": d, "p": by_date[d]} for d in ds]}
    return out


def _groups() -> dict[str, str]:
    from stock_groups import STOCK_GROUPS

    out: dict[str, str] = {}
    for name, members in STOCK_GROUPS.items():
        if name == "股期標的":
            continue
        for code, _ in members:
            out.setdefault(str(code), name)
    return out


def _row(r: dict[str, Any], prev: dict[str, Any] | None, detail: dict[str, Any] | None, price: float | None) -> dict[str, Any]:
    dir_net = (r.get("dirInc") or 0) - (r.get("dirDec") or 0)
    mgr = None if not prev or r.get("mgrHold") is None or prev.get("mgrHold") is None else r["mgrHold"] - prev["mgrHold"]
    big = None if not prev or r.get("bigHold") is None or prev.get("bigHold") is None else r["bigHold"] - prev["bigHold"]
    raw = dir_net + (mgr or 0)
    # 股本變動（配股、減資）會讓大家的持股同比例增減，不是買賣：上月持股 × 股本變動比例先扣掉
    # 只扣「跟著股本同方向」的那部分、最多扣到實際變動量：現金增資、可轉債轉換也會讓股本變多，但內部人沒動，不能當成賣出
    cap = 0.0
    if prev and r.get("issued") and prev.get("issued"):
        ratio = r["issued"] / prev["issued"] - 1
        if abs(ratio) >= CAPITAL_CHANGE_MIN:
            expected = ((prev.get("dirHold") or 0) + ((prev.get("mgrHold") or 0) if mgr is not None else 0)) * ratio
            cap = min(max(raw, 0.0), expected) if ratio > 0 else max(min(raw, 0.0), expected)
    net = raw - cap
    if abs(net) < 1000:          # 不到一張（配股零頭）當沒動
        net = 0
    out = {
        "code": r["code"], "name": r.get("name"), "market": r.get("market"), "dirNet": round(dir_net), "dirInc": round(r.get("dirInc") or 0),
        "dirDec": round(r.get("dirDec") or 0), "mgrNet": round(mgr) if mgr is not None else None, "bigNet": round(big) if big is not None else None,
        "rawNet": round(raw), "capAdj": round(cap), "net": round(net), "netLots": round(net / 1000), "dirPct": r.get("dirPct"),
        "dirPctChg": round(r["dirPct"] - prev["dirPct"], 2) if prev and r.get("dirPct") is not None and prev.get("dirPct") is not None else None,
        "netPctOfIssued": round(net / r["issued"] * 100, 3) if r.get("issued") else None,
        "price": round(price, 2) if price else None, "amount": round(net * price / 1e8, 2) if price else None,   # 億
    }
    if detail:
        central = (detail.get("buyC") or 0) - (detail.get("sellC") or 0)
        other = (detail.get("buyO") or 0) - (detail.get("sellO") or 0)
        out["central"] = {"buy": round(detail.get("buyC") or 0), "sell": round(detail.get("sellC") or 0), "net": round(central),
                          "amount": round(central * price / 1e8, 2) if price else None, "otherNet": round(other),
                          "people": detail.get("people") or []}
    return out


def overview(month: str | None = None) -> dict[str, Any]:
    months = load_months()
    if not months:
        return {"status": "missing", "message": "內部人月報還沒鏡像進來（每月約第三週出來，鏡像每天 20:40 看一次）", "months": []}
    month = month if month in months else months[0]
    key = "o:" + month
    if key in _cache:
        return _cache[key]
    data = _load_month(month)
    prev = _load_month(_prev_month(month))
    codes = [r["code"] for r in data["rows"]]
    prices = avg_close(codes, month)
    rows = [_row(r, (prev or {}).get("byCode", {}).get(r["code"]), data["detail"].get(r["code"]), prices.get(r["code"])) for r in data["rows"]]
    moved = [x for x in rows if x["net"] != 0]

    def amount(x: dict[str, Any]) -> float:
        return x["amount"] if x["amount"] is not None else x["net"] / 1e7    # 沒價格的排在後面

    buys = sorted((x for x in moved if x["net"] > 0), key=lambda x: -amount(x))[:TOP_N]
    sells = sorted((x for x in moved if x["net"] < 0), key=amount)[:TOP_N]
    with_central = [x for x in rows if x.get("central") and x["central"]["net"]]
    cbuy = sorted((x for x in with_central if x["central"]["net"] > 0), key=lambda x: -(x["central"]["amount"] or 0))[:15]
    csell = sorted((x for x in with_central if x["central"]["net"] < 0), key=lambda x: x["central"]["amount"] or 0)[:15]
    shown = {x["code"] for x in buys + sells + cbuy + csell}
    big = big_holders(sorted(shown))
    groups = _groups()
    for x in rows:
        if x["code"] in shown:
            b = big.get(x["code"])
            x["big400"] = {k: b[k] for k in ("date", "pct", "chg4w")} if b else None
            x["group"] = groups.get(x["code"])
            chg = (b or {}).get("chg4w")
            x["combo"] = ("雙買" if x["net"] > 0 and chg is not None and chg > 0 else
                          "雙賣" if x["net"] < 0 and chg is not None and chg < 0 else None)

    def total(market: str | None, sign: int) -> dict[str, Any]:
        sel = [x for x in moved if (market is None or x["market"] == market) and (x["net"] > 0) == (sign > 0)]
        return {"count": len(sel), "amount": round(sum(x["amount"] or 0 for x in sel), 2)}

    body = {
        "status": "ok", "month": month, "months": months, "published": data.get("published"), "fetched": data.get("fetched"),
        "hasPrev": prev is not None, "companies": len(rows), "moved": len(moved), "detailCount": len(data["detail"]),
        "summary": {"buy": total(None, 1), "sell": total(None, -1), "tseBuy": total("TSE", 1), "tseSell": total("TSE", -1),
                    "otcBuy": total("OTC", 1), "otcSell": total("OTC", -1)},
        "buys": buys, "sells": sells, "centralBuys": cbuy, "centralSells": csell,
        "rule": ("內部人淨增減＝董監（本月增加－減少）＋經理人（持股跟上月比），再扣掉配股／減資造成的同比例變動（股本變動比例 × 上月持股）；"
                 "10% 大股東常常就是董監，另列不加總。金額＝股數 × 該月平均收盤（估算）。"
                 "董監增減包含贈與、信託、繼承等，「集中市場」才是在市場上真的買賣（只查當月異動最大的公司）。"
                 "大戶＝集保 400 張以上持股比，近 4 週變化以百分點計；內部人買＋大戶增＝雙買、內部人賣＋大戶減＝雙賣。"),
        "source": "公開資訊觀測站：董事、監察人、經理人及百分之十以上大股東股權異動彙總表（IRB110）、內部人持股異動事後申報表；集保結算所股權分散表",
    }
    _cache[key] = body
    return body


def stock(code: str) -> dict[str, Any]:
    code = code.strip().upper()
    months = sorted(load_months())
    history = []
    name = None
    for month in months:
        data = _load_month(month)
        r = (data or {}).get("byCode", {}).get(code)
        if not r:
            continue
        name = name or r.get("name")
        prev = (_load_month(_prev_month(month)) or {}).get("byCode", {}).get(code)
        price = avg_close([code], month).get(code)
        row = _row(r, prev, data["detail"].get(code), price)
        row.update({"month": month, "dirHold": r.get("dirHold"), "mgrHold": r.get("mgrHold"), "bigHold": r.get("bigHold"), "issued": r.get("issued")})
        history.append(row)
    if not history:
        return {"status": "missing", "code": code, "message": "這檔沒有內部人月報資料（只收上市、上櫃）"}
    big = big_holders([code]).get(code)
    return {"status": "ok", "code": code, "name": name, "group": _groups().get(code), "history": list(reversed(history)), "big400": big}


def due(now: datetime, last: str | None) -> bool:
    if last is None:
        return True
    return last[:10] < now.date().isoformat() and (now.hour, now.minute) >= COLLECT_AT


def _loop() -> None:
    time.sleep(150)
    while True:
        try:
            if due(_now(), _state.get("lastCollect")):
                collect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("insider collect failed: %s", exc)
            with _lock:
                _state["lastError"] = f"{type(exc).__name__}: {exc}"[:300]
                _state["lastCollect"] = _now().isoformat(timespec="seconds")
        time.sleep(POLL_SECONDS)


def start_insider_collector() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    threading.Thread(target=_loop, name="insider-watch", daemon=True).start()
    return True


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)
