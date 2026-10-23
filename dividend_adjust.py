"""除權息還原（2026-10-24 使用者：只用在均線分數，其他功能不動）。

背景：2026-10-24 拿老師均線分數排行頁前 60 名逐檔對，伊雲谷、日友、佰鴻這類除過息的上市股，分數都比老師的少 1～4 分——
老師用的日線是除權息還原價（除息那天跳空下跌不算跌），我們原本的還原只有分割／減資（price_adjust），除權息不還原。
這裡另外記「除權息日、除權息前收盤價、除權息參考價」，因子＝參考價 ÷ 前收盤價；除權息日以前的價格都乘上因子
（之後所有事件的因子連乘，跟 price_adjust 同一套算法，直接用 price_adjust.adjust_bars）。

**只還原現金股利（純除息）**：含股票股利的事件（除權、除權息）不還原。2026-10-24 拿老師 10/22 前 60 名 60 檔逐檔試過三種規則——
不還原（5 檔不對）、全部還原（倍力 6874 的 9/3 除權息、永光 1711 的 10/20 除權會多算 3 檔不對）、只還原純除息（伊雲谷、日友都對上，沒有多錯的）——
老師的還原價看起來只還原現金股利，所以照這個做。

只有均線分數（站上均線、創新高、多頭排列）用還原後的價格；創高黑龍的持有報酬、漲跌幅、乖離、月營收的收盤價等
全部還是原本的價格（原始日K bars_1d 也不改）。呼叫端：heilong_backtest 算分數、ma_rank 盤中即時分數。

事件來源：
- twse：證交所「除權除息計算結果表」TWT49U（正式站主機直接抓，一個月一份）。
- tpex：櫃買「除權息計算結果表」exDailyQ（櫃買擋正式站主機，走 tw-groups data 分支的鏡像 tpex/actions/exdiv-YYYY.json）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from database import get_connection, initialize_database
from price_adjust import adjust_bars

TW_TZ = timezone(timedelta(hours=8))
MIN_FACTOR = 0.4           # 一次除權息價格掉超過六成不合理（配股最多也才到 0.67）：資料怪就不套
MAX_FACTOR = 1.6
TWSE_DIVIDEND_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS dividend_events (
            stock_code TEXT NOT NULL, ex_date TEXT NOT NULL,
            prev_close REAL NOT NULL, ref_price REAL NOT NULL, factor REAL NOT NULL,
            source TEXT NOT NULL, note TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, ex_date)
        )"""
    )


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def _date(text: Any) -> str | None:
    """'114年10月21日'、'114/10/21'、'1141021'、'20251021'、'2025-10-21' 都轉成 YYYY-MM-DD。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    m = re.match(r"^(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?$", raw)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif "-" in raw and len(raw) >= 10:
        return raw[:10]
    else:
        digits = raw.replace("/", "").replace(".", "")
        if not digits.isdigit() or len(digits) < 6:
            return None
        year, month, day = int(digits[:-4]), int(digits[-4:-2]), int(digits[-2:])
    if year < 1911:
        year += 1911
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _price(text: Any) -> float | None:
    try:
        value = float(str(text or "").replace(",", "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def parse_table(payload: Any, *, source: str) -> list[dict[str, Any]]:
    """證交所 TWT49U（fields＋data）、櫃買 exDailyQ（tables[0] 或鏡像存的 fields＋data）→ 事件列。
    欄位用名稱找：資料日期／除權息日期、股票代號／代號、除權息前收盤價、除權息參考價（兩邊名稱一樣）。"""
    if not isinstance(payload, dict):
        return []
    fields, data = payload.get("fields"), payload.get("data")
    if not fields and payload.get("tables"):
        table = (payload.get("tables") or [{}])[0] or {}
        fields, data = table.get("fields"), table.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        return []
    names = [str(f).strip() for f in fields]

    def find(*keys: str) -> int | None:
        for i, name in enumerate(names):
            if any(k in name for k in keys):
                return i
        return None

    i_date = find("資料日期", "除權息日期")
    i_code = find("股票代號", "代號")
    i_prev = find("除權息前收盤價")
    i_ref = find("除權息參考價")
    i_kind = find("權/息")
    if None in (i_date, i_code, i_prev, i_ref):
        return []
    out = []
    for row in data:
        if not isinstance(row, list):
            continue
        try:
            day = _date(row[i_date])
            code = str(row[i_code]).strip().upper()
            prev, ref = _price(row[i_prev]), _price(row[i_ref])
        except IndexError:
            continue
        if not day or not code or not prev or not ref:
            continue
        factor = ref / prev
        if factor == 1.0 or not (MIN_FACTOR <= factor <= MAX_FACTOR):
            continue
        note = str(row[i_kind]).strip() if i_kind is not None and i_kind < len(row) else None
        if not note or "息" not in note or "權" in note:      # 只收純除息（證交所寫「息」、櫃買寫「除息」）；除權、除權息不還原
            continue
        out.append({"code": code, "date": day, "prevClose": prev, "refPrice": ref, "factor": factor, "source": source, "note": note})
    return out


def save_events(events: Iterable[dict[str, Any]]) -> int:
    """寫入事件；回「新增或內容有變」的筆數（沒變的不算，呼叫端用它判斷要不要重算黑龍表）。"""
    rows = [e for e in events if e.get("prevClose") and e.get("refPrice")]
    if not rows:
        return 0
    initialize_database()
    changed = 0
    with get_connection() as connection:
        _schema(connection)
        existing = {(str(r["stock_code"]), str(r["ex_date"])): (float(r["prev_close"]), float(r["ref_price"]))
                    for r in connection.execute("SELECT stock_code, ex_date, prev_close, ref_price FROM dividend_events").fetchall()}
        for e in rows:
            key = (str(e["code"]).strip().upper(), str(e["date"])[:10])
            if existing.get(key) == (float(e["prevClose"]), float(e["refPrice"])):
                continue
            connection.execute(
                """INSERT INTO dividend_events (stock_code, ex_date, prev_close, ref_price, factor, source, note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(stock_code, ex_date) DO UPDATE SET prev_close=excluded.prev_close, ref_price=excluded.ref_price,
                     factor=excluded.factor, source=excluded.source, note=excluded.note, updated_at=excluded.updated_at""",
                (key[0], key[1], float(e["prevClose"]), float(e["refPrice"]), round(float(e["refPrice"]) / float(e["prevClose"]), 8),
                 e["source"], e.get("note"), _now()),
            )
            existing[key] = (float(e["prevClose"]), float(e["refPrice"]))
            changed += 1
    return changed


def load_events(codes: Iterable[str] | None = None) -> dict[str, list[tuple[str, float]]]:
    """{代號: [(除權息日, 因子), ...舊到新]}。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT stock_code, ex_date, factor FROM dividend_events ORDER BY ex_date").fetchall()
    wanted = {str(c).strip().upper() for c in codes} if codes is not None else None
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        code, factor = str(r["stock_code"]), float(r["factor"])
        if (wanted is not None and code not in wanted) or not (MIN_FACTOR <= factor <= MAX_FACTOR):
            continue
        out.setdefault(code, []).append((str(r["ex_date"]), factor))
    return out


def last_event_date() -> str | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT MAX(ex_date) AS d FROM dividend_events").fetchone()
    return str(row["d"]) if row and row["d"] else None


def event_counts() -> dict[str, Any]:
    """診斷：各來源筆數、最早／最晚除權息日。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT source, COUNT(*) AS n, MIN(ex_date) AS a, MAX(ex_date) AS b FROM dividend_events GROUP BY source").fetchall()
    return {str(r["source"]): {"n": int(r["n"]), "from": r["a"], "to": r["b"]} for r in rows}


def adjust(bars: list[tuple], events: list[tuple[str, float]] | None) -> list[tuple]:
    """bars＝[(日期, 開, 高, 低, 收, 量), ...]（已經套過分割減資還原的也可以）→ 再乘上除權息因子。沒有事件原樣回。"""
    return adjust_bars(bars, events)


def adjust_closes(closes_by_code: dict[str, list[float]], dates_by_code: dict[str, list[str]]) -> dict[str, list[float]]:
    """只有收盤價序列時用：{代號: 收盤} 配 {代號: 日期}，回還原後的收盤（沒有事件的代號原樣）。"""
    events = load_events(closes_by_code.keys())
    out: dict[str, list[float]] = {}
    for code, closes in closes_by_code.items():
        ev = events.get(code)
        if not ev:
            out[code] = closes
            continue
        bars = [(d, c, c, c, c, 0) for d, c in zip(dates_by_code[code], closes)]
        out[code] = [b[4] for b in adjust_bars(bars, ev)]
    return out


# ------------------------------------------------------------------ 抓資料

def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    """[(月初, 月底)]，從 start 所在月到 end 所在月；最後一個月只到 end。"""
    out = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((cur, min(nxt - timedelta(days=1), end)))
        cur = nxt
    return out


def refresh(start: date, end: date, *, fetcher: Callable[..., Any], mirror: Callable[[int], Any],
            sleep: Callable[[float], None], delay: float = 2.0) -> dict[str, Any]:
    """證交所一個月一份（TWT49U，直接抓）＋櫃買每年一份（鏡像）。fetcher(url, params)→dict、mirror(year)→dict。"""
    out: dict[str, Any] = {"twse": 0, "tpex": 0, "changed": 0, "errors": []}
    events: list[dict[str, Any]] = []
    for first, last in month_ranges(start, end):
        params = {"startDate": first.strftime("%Y%m%d"), "endDate": last.strftime("%Y%m%d"), "response": "json"}
        try:
            rows = parse_table(fetcher(TWSE_DIVIDEND_URL, params), source="twse")
            events.extend(rows)
            out["twse"] += len(rows)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"TWSE 除權息 {first:%Y-%m}: {type(exc).__name__}: {exc}"[:200])
        sleep(delay)
    for year in range(start.year, end.year + 1):
        try:
            rows = parse_table(mirror(year), source="tpex")
            events.extend(rows)
            out["tpex"] += len(rows)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"TPEx 除權息 {year}: {type(exc).__name__}: {exc}"[:200])
    out["changed"] = save_events(events)
    return out
