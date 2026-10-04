"""日K還原：減資、變更面額（股票分割）、ETF 分割／反分割這類「停止買賣、恢復時價格換算」的事件。

2026-10-04 使用者要做創高黑選股：對方用「官網還原日線（分割減資已還原）」，我們的日K是官方原始價，
遇到減資或分割，價格會一天跳好幾成（例如國巨 2025-08-25 一拆四，546 元變 136.5 元），均線分數、
創新高、跌破均線出場都會算錯。這裡記下每個事件的「停止買賣前收盤價」與「恢復買賣參考價」，
因子＝參考價 ÷ 停止前收盤價；事件日以前的開高低收都乘上因子（之後所有事件的因子連乘），成交量除以因子。
除權息不還原（跟對方一樣只還原分割減資）。原始日K（bars_1d）不改，算特徵的時候才套用。

事件來源（優先順序由高到低，同一檔同一天只留一筆）：
- twse：證交所「減資恢復買賣參考價格」TWTAUU、「變更股票面額恢復買賣參考價格」TWTB8U（正式站主機直接抓得到）。
- tpex：櫃買「減資恢復買賣參考價格」（櫃買擋正式站主機，走 tw-groups data 分支的鏡像）。
- tpex-quote：櫃買每日行情的漲跌是跟參考價比，停止買賣後恢復那天，參考價＝收盤−漲跌（同樣走鏡像）。
- inferred：上面都沒有、但日K看得出來的：這檔中間有交易日沒成交（停止買賣），恢復那天開盤跟停止前收盤
  差超過 11%（正常一天漲跌停只有 10%）。參考價不知道，用恢復那天的開盤價估（ETF 分割，例如 0050 在 2025-06-18 一拆四）。

後兩種（行情反推、推測）只認整數倍（1/2、1/4、2、4…，差 2% 以內）：2026-10-04 用真的上櫃日K跑過，
一天只成交 1 張的冷門股（例如宏太-KY、中湛）幾天沒成交、再開出來差一兩成很常見，參考價也會跟著漂，
不是減資；減資的因子不是整數倍，一律以官方表為準。同一檔 20 天內兩筆不同來源的事件當成同一件事
（官方的恢復買賣日那天沒成交，行情反推晚幾天才抓到），只留來源等級高的。
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from database import get_connection, initialize_database

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
SOURCE_RANK = {"twse": 0, "tpex": 1, "tpex-quote": 2, "inferred": 3}
INFER_JUMP = 0.11          # 恢復那天開盤跟停止前收盤差超過 11% 才推測是減資／分割
QUOTE_EVENT_MOVE = 0.15    # 櫃買行情反推的參考價跟前收盤差 15% 以上才算（減資有官方表；這裡主要抓面額變更，避免把沒成交那幾天的除權息當成事件）
MIN_HALT_DAYS = 2          # 中間至少停止買賣 2 個交易日（減資／分割通常停 5 天左右；只有 1 天沒成交多半是冷門股沒人買）
SNAP_RATIOS = (2, 3, 4, 5, 8, 10, 20, 25, 50)
SNAP_TOLERANCE = 0.02
DEDUPE_DAYS = 20           # 同一檔 20 天內兩筆不同來源的事件＝同一件事，留來源等級高的


def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS price_adjust_events (
            stock_code TEXT NOT NULL, ex_date TEXT NOT NULL,
            prev_close REAL NOT NULL, ref_price REAL NOT NULL, factor REAL NOT NULL,
            kind TEXT NOT NULL, source TEXT NOT NULL, note TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, ex_date)
        )"""
    )


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def snapped(factor: float) -> float | None:
    """因子離整數倍（1/2、1/4、2、4…）很近就回那個倍數；不是整數倍回 None。"""
    for ratio in SNAP_RATIOS:
        for target in (1 / ratio, float(ratio)):
            if abs(factor / target - 1) <= SNAP_TOLERANCE:
                return round(target, 6)
    return None


def snap_factor(factor: float) -> float:
    """推測出來的因子離整數倍很近就取整數倍；不然原樣。"""
    snap = snapped(factor)
    return factor if snap is None else snap


def save_events(events: Iterable[dict[str, Any]]) -> int:
    """寫入事件；同一檔同一天已經有來源等級更高（或一樣）的就不蓋掉低的，低的會被高的蓋掉。"""
    rows = [e for e in events if e.get("prevClose") and e.get("refPrice") and e["prevClose"] > 0 and e["refPrice"] > 0]
    if not rows:
        return 0
    initialize_database()
    written = 0
    with get_connection() as connection:
        _schema(connection)
        existing = {
            (str(r["stock_code"]), str(r["ex_date"])): str(r["source"])
            for r in connection.execute("SELECT stock_code, ex_date, source FROM price_adjust_events").fetchall()
        }
        for e in rows:
            key = (str(e["code"]).strip().upper(), str(e["date"])[:10])
            old = existing.get(key)
            if old is not None and SOURCE_RANK.get(old, 9) < SOURCE_RANK.get(e["source"], 9):
                continue
            factor = float(e.get("factor") or (e["refPrice"] / e["prevClose"]))
            connection.execute(
                """INSERT INTO price_adjust_events (stock_code, ex_date, prev_close, ref_price, factor, kind, source, note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(stock_code, ex_date) DO UPDATE SET prev_close=excluded.prev_close, ref_price=excluded.ref_price,
                     factor=excluded.factor, kind=excluded.kind, source=excluded.source, note=excluded.note, updated_at=excluded.updated_at""",
                (key[0], key[1], float(e["prevClose"]), float(e["refPrice"]), round(factor, 8), e.get("kind") or "adjust",
                 e["source"], e.get("note"), _now()),
            )
            existing[key] = e["source"]
            written += 1
    return written


def load_events(codes: Iterable[str] | None = None) -> dict[str, list[tuple[str, float]]]:
    """{代號: [(事件日, 因子), ...舊到新]}。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT stock_code, ex_date, factor, source FROM price_adjust_events ORDER BY ex_date").fetchall()
    wanted = {str(c).strip().upper() for c in codes} if codes is not None else None
    per_code: dict[str, list[tuple[str, float, int]]] = {}
    for r in rows:
        code = str(r["stock_code"])
        if wanted is not None and code not in wanted:
            continue
        factor, source = float(r["factor"]), str(r["source"])
        if factor <= 0 or (source in ("tpex-quote", "inferred") and snapped(factor) is None):
            continue   # 舊版存下的非整數倍反推／推測事件不套（見最上面的說明）
        per_code.setdefault(code, []).append((str(r["ex_date"]), factor, SOURCE_RANK.get(source, len(SOURCE_RANK))))
    return {code: dedupe_events(events) for code, events in per_code.items()}


def dedupe_events(events: list[tuple[str, float, int]]) -> list[tuple[str, float]]:
    """[(事件日, 因子, 來源等級)] 舊到新 → [(事件日, 因子)]：DEDUPE_DAYS 天內不同來源的兩筆只留等級高的
    （官方恢復買賣日那天沒成交，櫃買行情反推晚幾天才抓到同一件事，兩筆都套會還原兩次）。"""
    kept: list[tuple[str, float, int]] = []
    for ev in events:
        day = date.fromisoformat(ev[0])
        clash = next((i for i, k in enumerate(kept)
                      if k[2] != ev[2] and abs((date.fromisoformat(k[0]) - day).days) <= DEDUPE_DAYS), None)
        if clash is None:
            kept.append(ev)
        elif ev[2] < kept[clash][2]:
            kept[clash] = ev
    return [(d, f) for d, f, _ in sorted(kept)]


def list_events(limit: int = 200) -> list[dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute(
            "SELECT * FROM price_adjust_events ORDER BY ex_date DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [{"code": r["stock_code"], "date": r["ex_date"], "prevClose": r["prev_close"], "refPrice": r["ref_price"],
             "factor": r["factor"], "kind": r["kind"], "source": r["source"], "note": r["note"]} for r in rows]


def events_version() -> str:
    """事件表有沒有變（筆數＋最後更新時間）；黑龍表用來判斷要不要整張重算。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM price_adjust_events").fetchone()
    return f"{int(row['n'] or 0)}:{row['t'] or ''}"


def cumulative_factors(events: list[tuple[str, float]]) -> tuple[list[str], list[float]]:
    """(事件日 舊到新, 「這天以前」要乘的連乘因子)：第 k 個事件日以前的價格要乘上 第 k 個以後所有事件因子的乘積。"""
    dates = [d for d, _f in events]
    products = [1.0] * (len(events) + 1)
    for k in range(len(events) - 1, -1, -1):
        products[k] = products[k + 1] * events[k][1]
    return dates, products


def factor_for(dates: list[str], products: list[float], trade_date: str) -> float:
    """trade_date 那天的價格要乘的因子：排在 trade_date 之後（不含當天）的事件因子連乘。"""
    return products[bisect_right(dates, trade_date)]


def adjust_bars(bars: list[tuple], events: list[tuple[str, float]] | None) -> list[tuple]:
    """bars＝[(日期, 開, 高, 低, 收, 量), ...]；回還原後的同格式（量除以因子、取整數）。沒有事件原樣回。"""
    if not events or not bars:
        return bars
    dates, products = cumulative_factors(sorted(events))
    out = []
    for bar in bars:
        f = factor_for(dates, products, bar[0])
        if f == 1.0:
            out.append(bar)
            continue
        d, o, h, l, c, v = bar[:6]
        out.append((d, round(o * f, 4), round(h * f, 4), round(l * f, 4), round(c * f, 4), int(round(v / f)), *bar[6:]))
    return out


# ------------------------------------------------------------------ 解析官方表

def _roc_or_iso(text: Any) -> str | None:
    """'113/01/22'、'1130205'、'2024-01-22' 都轉成 YYYY-MM-DD。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    digits = raw.replace("/", "")
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
    raw = str(text or "").replace(",", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_official_table(payload: Any, *, source: str, kind: str) -> list[dict[str, Any]]:
    """證交所 TWTAUU／TWTB8U（fields＋data）、櫃買 revivt（tables[0] 或鏡像存的 fields＋data）→ 事件列。
    欄位用名稱找：恢復買賣日期、股票代號、停止買賣前收盤價格（櫃買叫最後交易日之收盤價格）、恢復買賣參考價（櫃買叫減資恢復買賣開始日參考價格）。"""
    if not isinstance(payload, dict):
        return []
    fields = payload.get("fields")
    data = payload.get("data")
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

    i_date = find("恢復買賣日期")
    i_code = find("股票代號", "代號")
    i_prev = find("停止買賣前收盤", "最後交易日之收盤")
    i_ref = find("恢復買賣參考價", "恢復買賣開始日參考價")
    i_reason = find("減資原因")
    if None in (i_date, i_code, i_prev, i_ref):
        return []
    out = []
    for row in data:
        if not isinstance(row, list):
            continue
        try:
            day = _roc_or_iso(row[i_date])
            code = str(row[i_code]).strip().upper()
            prev, ref = _price(row[i_prev]), _price(row[i_ref])
        except IndexError:
            continue
        if not day or not code or not prev or not ref:
            continue
        note = str(row[i_reason]).strip() if i_reason is not None and i_reason < len(row) else None
        out.append({"code": code, "date": day, "prevClose": prev, "refPrice": ref, "factor": ref / prev,
                    "kind": kind, "source": source, "note": note})
    return out


# ------------------------------------------------------------------ 從日K推測

def market_days(min_bars: int = 500) -> list[str]:
    """全市場交易日（那天上市＋上櫃日K有 min_bars 檔以上），舊到新。"""
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT substr(bar_time, 1, 10) AS d, COUNT(*) AS n FROM bars_1d GROUP BY d HAVING n >= ? ORDER BY d", (min_bars,)
        ).fetchall()
    return [str(r["d"]) for r in rows]


def has_event_between(known: dict[str, list[str]], code: str, after: str, until: str) -> bool:
    """這檔在 (after, until] 之間已經有事件（官方恢復買賣日可能比日K第一根早：恢復那天沒成交）。"""
    dates = known.get(code) or []
    i = bisect_right(dates, after)
    return i < len(dates) and dates[i] <= until


def detect_halt_jumps(bars_by_code: dict[str, list[tuple]], days: list[str], known: dict[str, list[str]]) -> list[dict[str, Any]]:
    """停止買賣（中間有交易日沒成交）後恢復、開盤跟停止前收盤差超過 11% 而且是整數倍，又沒有其他來源的事件 → 推測事件。
    known＝{代號: [已經有的事件日 舊到新]}。"""
    index = {d: i for i, d in enumerate(days)}
    out: list[dict[str, Any]] = []
    for code, bars in bars_by_code.items():
        for prev, cur in zip(bars, bars[1:]):
            i_prev, i_cur = index.get(prev[0]), index.get(cur[0])
            if i_prev is None or i_cur is None or i_cur - i_prev - 1 < MIN_HALT_DAYS:
                continue   # 中間沒有停止買賣（或只差一天沒成交）
            if has_event_between(known, code, prev[0], cur[0]):
                continue
            prev_close, open_ = float(prev[4]), float(cur[1])
            if prev_close <= 0 or open_ <= 0 or abs(open_ / prev_close - 1) <= INFER_JUMP:
                continue
            factor = snapped(open_ / prev_close)
            if factor is None:
                continue   # 不是整數倍：冷門股幾天沒成交後的正常漲跌（減資看官方表）
            out.append({"code": code, "date": cur[0], "prevClose": prev_close, "refPrice": round(prev_close * factor, 4),
                        "factor": factor, "kind": "inferred", "source": "inferred",
                        "note": f"停止買賣 {i_cur - i_prev - 1} 個交易日後恢復，開盤 {open_:g}／停止前收盤 {prev_close:g}"})
    return out


def quote_ref_events(day: str, rows: list[list[Any]], prev_close_of: dict[str, tuple[str, float]],
                     previous_market_days: list[str]) -> list[dict[str, Any]]:
    """櫃買鏡像某一天的行情列 [代號, 名稱, 開, 高, 低, 收, 漲跌, 量]：前一次成交早於前 MIN_HALT_DAYS 個交易日（中間停止買賣），
    而且參考價（收盤−漲跌）跟停止前收盤差 15% 以上、是整數倍（面額變更）→ 事件。prev_close_of＝{代號: (前一次成交日, 收盤)}；
    previous_market_days＝這天之前的交易日（新到舊，至少 MIN_HALT_DAYS 天）。"""
    out: list[dict[str, Any]] = []
    if len(previous_market_days) < MIN_HALT_DAYS:
        return out
    cutoff = previous_market_days[MIN_HALT_DAYS - 1]   # 前一次成交要早於這天，才算中間停了至少 MIN_HALT_DAYS 天
    for row in rows:
        try:
            code, close, change = str(row[0]).strip().upper(), float(row[5]), row[6]
        except (IndexError, TypeError, ValueError):
            continue
        if change is None:
            continue
        prev = prev_close_of.get(code)
        if not prev or prev[0] >= cutoff:
            continue   # 最近有成交（或第一次上市）：一般漲跌，不是恢復買賣
        ref = round(close - float(change), 4)
        if ref <= 0 or prev[1] <= 0 or abs(ref / prev[1] - 1) < QUOTE_EVENT_MOVE:
            continue
        factor = snapped(ref / prev[1])
        if factor is None:
            continue   # 不是整數倍：冷門股沒成交幾天參考價也會漂，不當事件（減資看官方表）
        out.append({"code": code, "date": day, "prevClose": prev[1], "refPrice": ref, "factor": factor,
                    "kind": "resume", "source": "tpex-quote", "note": f"停止買賣後恢復（前一次成交 {prev[0]}），參考價 {ref:g}＝收盤−漲跌"})
    return out
