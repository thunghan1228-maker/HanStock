"""處置監獄（2026-10-09 使用者：照莊爸「處置股・出獄與嫌疑名單」zhuang.tw/prison 一模一樣做到我們網站）。

資料全部用證交所／櫃買中心的公開公告（不抓莊爸網站）：
- 上市：證交所「公布注意有價證券資訊」announcement/notice、「公布處置有價證券資訊」announcement/punish（正式站直抓，可指定期間）
- 上櫃：櫃買中心 bulletin/attention、bulletin/disposal；櫃買擋正式站主機，走 tw-groups data 分支鏡像
  tpex/jail-attention.json、tpex/jail-disposal.json（排程主機每個交易日晚上抓最近 150 天）

頁面各段：
- 一週出獄時間表：處置迄日的下一個交易日＝出獄（恢復正常交易），本週／下週／下下週，休市日標「休市」
- 犯罪集團：同一族群（我們的 55 個族群）≥2 檔被關或即將被關（資料日在關＋今天剛公告的）；不在任何族群的傳產股
  用官方產業別補（鋼鐵、塑膠、航運…；電子類太廣、交給我們的族群，不補）
- 今日入獄：資料日當天公告的新處置（下一個交易日生效）、幾分盤、剩幾個交易日出獄
- 嫌疑名單：照作業要點第六條的累積規則（連續 3 日第一款；連續 5 日、10 日內 6 日、30 日內 12 日第一～八款），
  明天再被公布一次注意就會被關（處置中的＝會延長）的股票，並用公開收盤價反推明天的門檻：
  第一款收盤價（6 日累積漲跌＝每天漲跌幅相加：上市 32%，或 25% 且起迄價差 50 元；上櫃 30%，或 23% 且價差 40 元）、
  第四款（6 日累積上市 25%／上櫃 27% ＋ 當日週轉率上市 10%／上櫃 5%）、第六款（週轉率 5%、上市另需 3000 張）、
  第二款（30/60/90 日起迄漲跌持續超標，收紅就再中）。門檻數字取自證交所第四條異常標準詳細數據（115.08.03 版），
  上櫃的從櫃買實際公告的數字反推（8/10 新制後：第一款最低 30.02%、23.38%＋價差 40.5 元；第三／四款 27%；週轉率 5%）
- 今日第一次第一款：資料日觸及第一款、前 9 個交易日都沒有第一款（出獄後重新算：關完之前的第一款不算，耀穎 10/08）
- 個股處置前科索引／查詢：處置迄日在近 95 天內、或近 25 個交易日內被注意過的股票（照莊爸 10/08 的 293 檔反推），
  每檔的處置紀錄、注意日期與款別、明天判定

累積天數只算最近一次處置公告「之後」的注意（公告那天以前的已經用掉了）；第九款以後的不算進累積
（第十三款只影響處置天數 5→7 天）。官方的差幅條件（跟全體／同類平均比）這裡不算，平常市場平均很小，
門檻是「至少要這樣才可能被關」的估計，非即時報價、非選股建議。
"""

from __future__ import annotations

import html
import json
import logging
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from chips_daily import _default_fetcher, _mirror_url
from daily_bars_store import load_daily_bars
from database import get_connection, initialize_database
from fundamentals_daily import MIRROR_BASICS, TWSE_BASICS_URL, parse_basics, shares_map
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from trading_days import is_trading_day, next_trading_day, previous_trading_day

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))

TWSE_NOTICE_URL = ("https://www.twse.com.tw/rwd/zh/announcement/notice?querytype=1&stockNo=&selectType=&startDate={s}"
                   "&endDate={e}&sortKind=STKNO&response=json")
TWSE_PUNISH_URL = ("https://www.twse.com.tw/rwd/zh/announcement/punish?querytype=1&stockNo=&selectType=&startDate={s}"
                   "&endDate={e}&response=json")
MIRROR_ATTENTION = "jail-attention.json"
MIRROR_DISPOSAL = "jail-disposal.json"
HISTORY_DAYS = 150          # 第一次啟動回補幾天（30 營業日累積＋90 日前科索引）
INDEX_PUNISH_DAYS = 95      # 前科索引：處置迄日在近 95 天內（莊爸 10/08：迄日 7/06 有、7/03 沒有）
INDEX_NOTICE_DAYS = 25      # 　　　　　或近 25 個交易日內被注意過（9/02 以後有、9/01 沒有）
KEEP_DAYS = 400
POLL_SECONDS = 15 * 60
POLL_START = 17 * 60 + 20   # 證交所約 18:00 前後公告
POLL_END = 22 * 60
MARKET_LABEL = {"TSE": "上市", "OTC": "上櫃"}
ACCUM_CLAUSES = set(range(1, 9))   # 第六條累積只算第一～八款

# 第一款／第四款／第六款門檻（6 日累積＝每天漲跌幅相加）
RULES = {
    "TSE": {"c1": 32.0, "c1alt": 25.0, "c1diff": 50.0, "c4cum": 25.0, "c4turn": 10.0, "c6turn": 5.0, "c6vol": 3000},
    "OTC": {"c1": 30.0, "c1alt": 23.0, "c1diff": 40.0, "c4cum": 27.0, "c4turn": 5.0, "c6turn": 5.0, "c6vol": 0},
}
CLAUSE2_WINDOWS = ((30, 100.0), (60, 130.0), (90, 160.0))
# 官方產業別（證交所、櫃買共用代碼）：犯罪集團給不在我們族群裡的傳產股用。電子類（24～31、34、36）、綜合、其他不補
INDUSTRY_NAMES = {"01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織", "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷",
                  "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "14": "建材營造", "15": "航運", "16": "觀光餐旅",
                  "17": "金融保險", "18": "貿易百貨", "21": "化學", "22": "生技醫療", "23": "油電燃氣", "32": "文化創意",
                  "33": "農業科技", "35": "綠能環保", "37": "運動休閒", "38": "居家生活"}
HIGH_PROB_DROP = -5.0       # 最容易的門檻要跌超過 5% 才躲得掉＝高機率
LIMIT_PCT = 10.0

_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CLAUSE_RE = re.compile(r"第([一二三四五六七八九十]+)款")
DAYS_RE = re.compile(r"(?:﹝|〔|起)\s*([0-9一二三四五六七八九十]+)\s*個營業日")
MINUTES_RE = re.compile(r"約每\s*([0-9一二三四五六七八九十]+)\s*分鐘")
CODE_RE = re.compile(r"[1-9]\d{3}")
WEEKDAYS = "一二三四五六日"

_state: dict[str, Any] = {"lastCollect": None, "lastError": None, "result": None, "backfilled": False}
_cache: dict[str, Any] = {"key": None, "payload": None, "at": 0.0}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


# ------------------------------------------------------------------ 解析

def cn_number(text: str) -> int | None:
    """中文或阿拉伯數字（一～九十九）轉整數：五、十、十三、二十。"""
    text = str(text or "").strip()
    if text.isdigit():
        return int(text)
    if not text or any(ch not in _CN and ch != "十" for ch in text):
        return None
    if "十" not in text:
        return _CN.get(text)
    tens, _, ones = text.partition("十")
    return (_CN.get(tens, 1) if tens else 1) * 10 + (_CN.get(ones, 0) if ones else 0)


def roc_to_iso(text: Any) -> str | None:
    """民國日期 115/10/08、115.10.08、1151008 → 2026-10-08。"""
    raw = str(text or "").strip()
    m = re.match(r"^(\d{2,3})[./-](\d{1,2})[./-](\d{1,2})", raw)
    if m:
        return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if len(raw) == 7 and raw.isdigit():
        return f"{int(raw[:3]) + 1911:04d}-{raw[3:5]}-{raw[5:7]}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    return None


def parse_period(text: Any) -> tuple[str | None, str | None]:
    parts = re.split(r"[~～至]", str(text or ""))
    if len(parts) < 2:
        return None, None
    return roc_to_iso(parts[0]), roc_to_iso(parts[1])


def parse_clauses(info: Any) -> list[int]:
    found = {cn_number(m.group(1)) for m in CLAUSE_RE.finditer(str(info or ""))}
    return sorted(c for c in found if c)


def clean_text(value: Any) -> str:
    text = re.sub(r"<br\s*/?>", "；", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def clean_name(value: Any) -> str:
    """櫃買處置的名稱欄會帶連結：聯一光(../../mainboard/listed/company-detail.html?code=3441)。"""
    return re.sub(r"\([^)]*html[^)]*\)", "", clean_text(value)).strip()


def _table(payload: Any) -> tuple[list[str], list[list[Any]]]:
    """證交所 {fields, data}；櫃買 {tables:[{fields, data}]}；鏡像 {fields, data}。"""
    if isinstance(payload, dict) and isinstance(payload.get("tables"), list) and payload["tables"]:
        payload = payload["tables"][0]
    if not isinstance(payload, dict):
        return [], []
    return [str(f).strip() for f in (payload.get("fields") or [])], list(payload.get("data") or [])


def _col(fields: list[str], *names: str) -> int | None:
    for name in names:
        for i, f in enumerate(fields):
            if f == name:
                return i
    for name in names:
        for i, f in enumerate(fields):
            if name in f:
                return i
    return None


def parse_notice_payload(payload: Any, market: str) -> list[dict[str, Any]]:
    fields, data = _table(payload)
    ic, iname, iinfo = _col(fields, "證券代號"), _col(fields, "證券名稱"), _col(fields, "注意交易資訊")
    idate, iclose = _col(fields, "日期", "公告日期"), _col(fields, "收盤價")
    if None in (ic, iname, iinfo, idate):
        raise ValueError(f"注意股欄位對不上：{fields}")
    out: list[dict[str, Any]] = []
    for row in data:
        code = str(row[ic]).strip()
        day = roc_to_iso(row[idate])
        if not CODE_RE.fullmatch(code) or not day:
            continue
        info = clean_text(row[iinfo])
        try:
            close = float(str(row[iclose]).replace(",", "")) if iclose is not None else None
        except ValueError:
            close = None
        out.append({"code": code, "date": day, "market": market, "name": clean_name(row[iname]),
                    "clauses": parse_clauses(info), "info": info, "close": close})
    return out


def parse_punish_payload(payload: Any, market: str) -> list[dict[str, Any]]:
    fields, data = _table(payload)
    ic, iname = _col(fields, "證券代號"), _col(fields, "證券名稱")
    iann, iperiod = _col(fields, "公布日期"), _col(fields, "處置起迄時間", "處置起訖時間")
    icond, imeasure, icontent = _col(fields, "處置條件", "處置原因"), _col(fields, "處置措施"), _col(fields, "處置內容")
    if None in (ic, iname, iann, iperiod):
        raise ValueError(f"處置股欄位對不上：{fields}")
    out: list[dict[str, Any]] = []
    for row in data:
        code = str(row[ic]).strip()
        start, end = parse_period(row[iperiod])
        if not CODE_RE.fullmatch(code) or not start or not end:
            continue
        content = clean_text(row[icontent]) if icontent is not None else ""
        m_days, m_min = DAYS_RE.search(content), MINUTES_RE.search(content)
        days = cn_number(m_days.group(1)) if m_days else None
        if not days:
            days = count_trading_days(start, end)
        out.append({
            "code": code, "name": clean_name(row[iname]), "market": market, "announce": roc_to_iso(row[iann]),
            "start": start, "end": end, "condition": clean_text(row[icond]) if icond is not None else "",
            "measure": clean_text(row[imeasure]) if imeasure is not None else "", "days": days,
            "minutes": cn_number(m_min.group(1)) if m_min else None, "content": content[:600],
        })
    return out


# ------------------------------------------------------------------ 交易日

def _d(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def count_trading_days(start: str, end: str) -> int:
    day, last, n = _d(start), _d(end), 0
    while day <= last:
        n += is_trading_day(day)
        day += timedelta(days=1)
    return n


def trading_days_back(day: str, count: int) -> list[str]:
    """day（含）往前 count 個交易日，由新到舊。"""
    cur = _d(day)
    if not is_trading_day(cur):
        cur = previous_trading_day(cur)
    out = [cur.isoformat()]
    while len(out) < count:
        cur = previous_trading_day(cur)
        out.append(cur.isoformat())
    return out


def release_day(end: str) -> str:
    return next_trading_day(_d(end)).isoformat()


def md(day: str) -> str:
    d = _d(day)
    return f"{d.month}/{d.day}"


def weekday_label(day: str) -> str:
    return WEEKDAYS[_d(day).weekday()]


# ------------------------------------------------------------------ 儲存

def _schema(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS jail_notice (
            code TEXT NOT NULL, trade_date TEXT NOT NULL, market TEXT NOT NULL, name TEXT NOT NULL,
            clauses TEXT NOT NULL, info TEXT NOT NULL, close REAL, PRIMARY KEY (code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS jail_notice_date_idx ON jail_notice (trade_date);
        CREATE TABLE IF NOT EXISTS jail_punish (
            code TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, market TEXT NOT NULL,
            name TEXT NOT NULL, announce_date TEXT, condition TEXT, measure TEXT, days INTEGER, minutes INTEGER,
            content TEXT, PRIMARY KEY (code, start_date)
        );
        CREATE INDEX IF NOT EXISTS jail_punish_end_idx ON jail_punish (end_date);
        CREATE TABLE IF NOT EXISTS jail_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jail_company (
            code TEXT PRIMARY KEY, market TEXT NOT NULL, industry TEXT, shares INTEGER, updated_at TEXT NOT NULL
        );
        """
    )


def save_notices(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    initialize_database()
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:   # 同一天同一檔兩筆（少見）就把款別併起來
        key = (r["code"], r["date"])
        if key in merged:
            merged[key]["clauses"] = sorted(set(merged[key]["clauses"]) | set(r["clauses"]))
            if r["info"] not in merged[key]["info"]:
                merged[key]["info"] += "；" + r["info"]
        else:
            merged[key] = dict(r)
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            "INSERT OR REPLACE INTO jail_notice (code, trade_date, market, name, clauses, info, close) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r["code"], r["date"], r["market"], r["name"], ",".join(str(c) for c in r["clauses"]), r["info"], r["close"])
             for r in merged.values()],
        )
    return len(merged)


def save_punishes(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT OR REPLACE INTO jail_punish (code, start_date, end_date, market, name, announce_date, condition, measure,
               days, minutes, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(r["code"], r["start"], r["end"], r["market"], r["name"], r["announce"], r["condition"], r["measure"],
              r["days"], r["minutes"], r["content"]) for r in rows],
        )
    return len(rows)


def parse_companies(payload: Any) -> dict[str, dict[str, Any]]:
    """t187ap03（上市證交所直抓、上櫃鏡像）：代號 → 官方產業別代碼、已發行股數（全部公司，不限族群）。"""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, list):
        return out
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("公司代號") or item.get("SecuritiesCompanyCode") or "").strip().upper()
        if not CODE_RE.fullmatch(code):
            continue
        industry = str(item.get("產業別") or item.get("SecuritiesIndustryCode") or "").strip()
        out[code] = {"industry": industry.zfill(2) if industry.isdigit() else None, "shares": parse_basics([item]).get(code)}
    return out


def save_companies(market: str, rows: dict[str, dict[str, Any]]) -> int:
    """鏡像還沒帶產業別時不要把舊的洗掉（COALESCE）。"""
    if not rows:
        return 0
    initialize_database()
    stamp = _now().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT INTO jail_company (code, market, industry, shares, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET market = excluded.market,
                 industry = COALESCE(excluded.industry, jail_company.industry),
                 shares = COALESCE(excluded.shares, jail_company.shares), updated_at = excluded.updated_at""",
            [(code, market, r["industry"], r["shares"], stamp) for code, r in rows.items()],
        )
    return len(rows)


def load_companies() -> dict[str, dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT code, industry, shares FROM jail_company").fetchall()
    return {r["code"]: {"industry": r["industry"], "shares": r["shares"]} for r in rows}


def _meta_get(key: str) -> str | None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT value FROM jail_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(key: str, value: str) -> None:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT OR REPLACE INTO jail_meta (key, value) VALUES (?, ?)", (key, value))


def load_notices(since: str) -> list[dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT * FROM jail_notice WHERE trade_date >= ? ORDER BY trade_date", (since,)).fetchall()
    return [{"code": r["code"], "date": r["trade_date"], "market": r["market"], "name": r["name"],
             "clauses": [int(c) for c in str(r["clauses"]).split(",") if c], "info": r["info"], "close": r["close"]} for r in rows]


def load_punishes(since: str) -> list[dict[str, Any]]:
    """處置迄日在 since 之後（含）的處置。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT * FROM jail_punish WHERE end_date >= ? ORDER BY start_date", (since,)).fetchall()
    return [{"code": r["code"], "start": r["start_date"], "end": r["end_date"], "market": r["market"], "name": r["name"],
             "announce": r["announce_date"], "condition": r["condition"], "measure": r["measure"], "days": r["days"],
             "minutes": r["minutes"], "content": r["content"]} for r in rows]


def prune(today: date) -> None:
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DELETE FROM jail_notice WHERE trade_date < ?", (cutoff,))
        connection.execute("DELETE FROM jail_punish WHERE end_date < ?", (cutoff,))


# ------------------------------------------------------------------ 抓資料

def _now() -> datetime:
    return datetime.now(TW_TZ)


def collect(now: datetime | None = None, fetcher: Callable[[str], Any] | None = None, *, days: int | None = None) -> dict[str, Any]:
    """上市直抓證交所（指定期間，一次 31 天），上櫃讀鏡像（整份最近 150 天）。days=None：第一次回補 150 天、之後只抓近 10 天。"""
    now = now or _now()
    call = fetcher or _default_fetcher
    if days is None:
        days = 10 if _meta_get("backfilled") else HISTORY_DAYS
    today = now.date()
    result: dict[str, Any] = {"at": now.isoformat(timespec="seconds"), "days": days, "errors": []}
    notices, punishes = [], []
    # 處置是照「處置期間」篩：今天公告、下一個交易日才生效的，迄日要往後拉 30 天才查得到
    for kind, url, parser, bucket, until in (("notice", TWSE_NOTICE_URL, parse_notice_payload, notices, today),
                                             ("punish", TWSE_PUNISH_URL, parse_punish_payload, punishes, today + timedelta(days=30))):
        cursor = today - timedelta(days=days)
        while cursor <= until:
            upto = min(until, cursor + timedelta(days=30))
            span = {"s": cursor.strftime("%Y%m%d"), "e": upto.strftime("%Y%m%d")}
            cursor = upto + timedelta(days=1)
            try:
                payload = call(url.format(**span))
                time.sleep(0.6 if fetcher is None else 0)
                if isinstance(payload, dict) and payload.get("stat") not in (None, "OK") and not payload.get("data"):
                    continue   # 那段期間沒有資料（證交所回「很抱歉，沒有符合條件的資料」）
                bucket.extend(parser(payload, "TSE"))
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"TWSE {kind} {span['s']}: {type(exc).__name__}: {exc}")
    for kind, name, parser, bucket in (("attention", MIRROR_ATTENTION, parse_notice_payload, notices),
                                       ("disposal", MIRROR_DISPOSAL, parse_punish_payload, punishes)):
        try:
            payload = call(_mirror_url(name, volatile=True))
            bucket.extend(parser(payload, "OTC"))
            result[f"mirror_{kind}"] = payload.get("updated") if isinstance(payload, dict) else None
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"TPEx 鏡像 {kind}: {type(exc).__name__}: {exc}")
    result["notices"] = save_notices(notices)
    result["punishes"] = save_punishes(punishes)
    # 公司基本資料（產業別、已發行股數）一天抓一次就好
    if _meta_get("companies") != today.isoformat():
        saved = {}
        for market, url in (("TSE", TWSE_BASICS_URL), ("OTC", _mirror_url(MIRROR_BASICS, volatile=True))):
            try:
                saved[market] = save_companies(market, parse_companies(call(url)))
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"公司基本資料 {market}: {type(exc).__name__}: {exc}")
        result["companies"] = saved
        if saved.get("TSE") and saved.get("OTC"):
            _meta_set("companies", today.isoformat())
    tse_ok = not any(e.startswith("TWSE") for e in result["errors"])
    if days >= HISTORY_DAYS and tse_ok and result["notices"]:
        _meta_set("backfilled", now.isoformat(timespec="seconds"))
    prune(today)
    with _lock:
        _state["lastCollect"] = result["at"]
        _state["lastError"] = "; ".join(result["errors"]) or None
        _state["result"] = {k: v for k, v in result.items() if k != "errors"}
        _cache["key"] = None
    return result


# ------------------------------------------------------------------ 計算

def _group_index() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for group, members in STOCK_GROUPS.items():
        if group in SPECIAL_GROUP_NAMES:
            continue
        for code, _name in members:
            out.setdefault(code, []).append(group)
    return out


def _latest_by_market(notices: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in notices:
        if n["date"] > out.get(n["market"], ""):
            out[n["market"]] = n["date"]
    return out


def accumulation(notice_days: dict[str, list[int]], data_day: str, after: str | None) -> dict[str, Any]:
    """notice_days：{日期: [款]}。只算 after（最近一次處置公告日）之後的注意。回連續第一款天數、連續任款天數、
    最近 9／29 個交易日內任款天數（明天再中一次就湊滿 10 日 6 次／30 日 12 次）。"""
    days = trading_days_back(data_day, 29)
    usable = {d: c for d, c in notice_days.items() if (after is None or d > after)}

    def hit(d: str, clause_set: set[int]) -> bool:
        return bool(set(usable.get(d, [])) & clause_set)

    c1 = 0
    for d in days:
        if not hit(d, {1}):
            break
        c1 += 1
    cany = 0
    for d in days:
        if not hit(d, ACCUM_CLAUSES):
            break
        cany += 1
    n9 = sum(1 for d in days[:9] if hit(d, ACCUM_CLAUSES))
    n29 = sum(1 for d in days[:29] if hit(d, ACCUM_CLAUSES))
    return {"c1": c1, "cany": cany, "n9": n9, "n29": n29,
            "pathA": c1 == 2, "pathB": cany == 4, "pathC": n9 == 5, "pathD": n29 == 11}


def _fmt_price(value: float) -> str:
    return f"{value:,.2f}"


def _lots_over(value: float) -> int:
    """週轉率「達」5%＝成交量 ≥ 門檻；張數是整數，寫成「> N 張」的 N＝門檻無條件進位再減 1（立碁 5,455.2 張 → > 5,455）。"""
    return max(int(math.ceil(value - 1e-9)) - 1, 0)


def _fmt_lots(value: float) -> str:
    return f"{_lots_over(value):,}"


def thresholds(code: str, market: str, data_day: str, *, need_any: bool, recent_clauses: set[int], verb: str,
               shares: int | None) -> dict[str, Any]:
    """明天的門檻（用公開收盤價反推）。回 {lines, high, note, close, volume}。"""
    bars = load_daily_bars(code, limit=100)
    if len(bars) < 7 or str(bars[-1]["ts"])[:10] != data_day:
        return {"lines": [], "high": False, "close": None, "volume": None, "missing": True}
    closes = [b["close"] for b in bars]
    c = closes[-1]
    vol = bars[-1]["volume"]
    s5 = sum((closes[-k] / closes[-k - 1] - 1) * 100 for k in range(1, 6) if closes[-k - 1] > 0)
    start_close = closes[-5]           # 明天 6 日窗口的起點（今天往前推 4 個交易日）
    rule = RULES.get(market, RULES["TSE"])
    up = s5 >= 0
    sign = 1 if up else -1
    lines: list[dict[str, Any]] = []
    pcts: list[float] = []

    def price_for(cum: float) -> float:
        return c * (1 + (sign * cum - s5) / 100)

    def add_price(price: float, label: str) -> None:
        pct = (price / c - 1) * 100
        if sign * pct > LIMIT_PCT:
            return   # 一天到不了
        if sign * pct <= -LIMIT_PCT:
            lines.append({"kind": "price", "always": True, "pct": round(pct, 2), "clause": label,
                          "text": f"{'跌停' if up else '漲停'}都會{verb}（6 日累積已達{label}門檻）"})
            pcts.append(-LIMIT_PCT * sign)
            return
        op = ">" if up else "<"
        lines.append({"kind": "price", "price": round(price, 2), "pct": round(pct, 2), "clause": label,
                      "text": f"收盤 {op} {_fmt_price(price)} 元（{pct:+.2f}%）會{verb}"})
        pcts.append(pct)

    if c >= 5:   # 收盤價未滿 5 元不適用第一款
        add_price(price_for(rule["c1"]), "第一款")
        alt = price_for(rule["c1alt"])
        alt = max(alt, start_close + rule["c1diff"]) if up else min(alt, start_close - rule["c1diff"])
        if (up and alt < price_for(rule["c1"]) - 0.005) or (not up and alt > price_for(rule["c1"]) + 0.005):
            add_price(alt, "第一款")
    lots = shares / 1000 if shares else None
    if need_any and lots:
        p4 = price_for(rule["c4cum"])
        v4 = lots * rule["c4turn"] / 100
        pct4 = (p4 / c - 1) * 100
        if sign * pct4 <= -LIMIT_PCT:
            lines.append({"kind": "note", "text": f"{'跌停' if up else '漲停'}都觸發 第四款（6 日累積已超 {rule['c4cum']:g}%）"})
            lines.append({"kind": "volume", "lots": _lots_over(v4), "today": vol, "passed": vol > v4, "clause": "第四款",
                          "text": f"成交量 > {_fmt_lots(v4)} 張 會{verb}"})
        elif sign * pct4 <= LIMIT_PCT:
            op = ">" if up else "<"
            lines.append({"kind": "volume", "lots": _lots_over(v4), "today": vol, "passed": vol > v4, "clause": "第四款",
                          "text": f"收盤 {op} {_fmt_price(p4)} 元（{pct4:+.2f}%）且成交量 > {_fmt_lots(v4)} 張 會{verb}"})
        if 6 in recent_clauses:
            v6 = max(lots * rule["c6turn"] / 100, rule["c6vol"])
            lines.append({"kind": "volume", "lots": _lots_over(v6), "today": vol, "passed": vol > v6, "clause": "第六款",
                          "text": f"成交量 > {_fmt_lots(v6)} 張 會{verb}"})
    persistent = False
    if need_any and 2 in recent_clauses and len(closes) >= 91:
        best = None
        for window, limit in CLAUSE2_WINDOWS:
            base = closes[-window]
            if base > 0:
                change = (c / base - 1) * 100
                if abs(change) > limit + 20 and (best is None or abs(change) - limit > abs(best[1]) - best[2]):
                    best = (window, change, limit)
        if best:
            persistent = True
            lines.append({"kind": "note", "text": f"第二款 持續中（{best[0]}日累積 {best[1]:+.0f}%）— 收{'紅' if best[1] > 0 else '黑'}就大機率自動再中"})
    easiest = min(pcts, key=lambda p: sign * p) if pcts else None
    high = persistent or (easiest is not None and sign * easiest <= HIGH_PROB_DROP)
    return {"lines": lines, "high": bool(high), "close": c, "volume": vol, "s5": round(s5, 2), "easiest": easiest}


def _status_text(acc: dict[str, Any], last_clauses: list[int], jailed: bool) -> str:
    if acc["pathA"]:
        text = "連2 第一款 警告"
    elif acc["pathB"]:
        extra = "、".join(f"第{_cn(c)}款" for c in last_clauses if c in ACCUM_CLAUSES and c != 1)
        text = f"連4 任款 警告{f'({extra})' if extra else ''}"
    elif acc["pathC"]:
        text = "10日內5次 警告"
    else:
        text = "30日內11次 警告"
    return f"處置中 {text} → 會延長" if jailed else text


def _cn(n: int) -> str:
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    return "十" + (digits[n % 10] if n % 10 else "")


def _tier(count: int) -> dict[str, str]:
    if count >= 3:
        return {"key": "boss", "icon": "👑", "label": "老大級慣犯"}
    if count >= 1:
        return {"key": "prior", "icon": "🔒", "label": "前科犯"}
    return {"key": "new", "icon": "🆕", "label": "新嫌"}


def _week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_payload(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    key = (_state.get("lastCollect"), now.date().isoformat())
    with _lock:
        if _cache["key"] == key and _cache["payload"] is not None and time.time() - _cache["at"] < 120:
            return _cache["payload"]
    payload = _build(now)
    with _lock:
        _cache.update(key=key, payload=payload, at=time.time())
    return payload


def _build(now: datetime) -> dict[str, Any]:
    today = now.date()
    since = (today - timedelta(days=HISTORY_DAYS + 30)).isoformat()
    notices = load_notices(since)
    punishes = load_punishes(since)
    if not notices:
        return {"status": "empty", "message": "處置／注意資料還在抓，晚一點再看", "collector": collector_status()}
    latest = _latest_by_market(notices)
    data_day = max(latest.values())
    next_day = next_trading_day(_d(data_day)).isoformat()
    groups_of = _group_index()
    companies = load_companies()
    names: dict[str, str] = {}
    markets: dict[str, str] = {}
    by_code_notice: dict[str, dict[str, list[int]]] = {}
    for n in notices:
        names[n["code"]] = n["name"] or names.get(n["code"], "")
        markets[n["code"]] = n["market"]
        by_code_notice.setdefault(n["code"], {})[n["date"]] = sorted(set(by_code_notice.get(n["code"], {}).get(n["date"], [])) | set(n["clauses"]))
    by_code_punish: dict[str, list[dict[str, Any]]] = {}
    for p in punishes:
        names.setdefault(p["code"], p["name"])
        markets.setdefault(p["code"], p["market"])
        p["release"] = release_day(p["end"])
        p["new"] = p["announce"] == data_day
        by_code_punish.setdefault(p["code"], []).append(p)

    # 一週出獄時間表：同一檔前一次還沒關完又被「再次處置」（期間重疊或接著關），出獄日以最後一次為準，
    # 前一次的迄日隔天其實還在關，不列（天擎 10/06~10/13 又 10/12~10/16 → 只在 10/19 出獄）
    def still_jailed(code: str, day: str) -> bool:
        return any(q["start"] <= day <= q["end"] for q in by_code_punish.get(code, []))

    real_releases = [p for p in punishes if not still_jailed(p["code"], p["release"])]
    monday = _week_monday(_d(data_day))
    weeks = []
    releases: dict[str, list[dict[str, Any]]] = {}
    for p in real_releases:
        releases.setdefault(p["release"], []).append(p)
    for w, label in enumerate(("本週", "下週", "下下週")):
        start = monday + timedelta(days=7 * w)
        days = []
        for k in range(5):
            day = (start + timedelta(days=k)).isoformat()
            rows = sorted(releases.get(day, []), key=lambda p: p["code"])
            days.append({"date": day, "md": md(day), "weekday": weekday_label(day), "closed": not is_trading_day(day),
                         "past": day <= data_day,
                         "stocks": [{"code": p["code"], "name": p["name"], "new": p["new"]} for p in rows]})
        weeks.append({"label": label, "range": f"{md(start.isoformat())}~{md((start + timedelta(days=4)).isoformat())}", "days": days})
    pending = sum(1 for p in real_releases if p["release"] > data_day)

    # 下一個交易日還在關的（含今天剛公告的）；同一檔有好幾筆蓋到就用最晚出獄那筆
    def covering(day: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for p in punishes:
            if p["start"] <= day <= p["end"] and (p["code"] not in out or p["end"] > out[p["code"]]["end"]):
                out[p["code"]] = p
        return out

    jailed_next = covering(next_day)
    jailed_now = covering(data_day)

    # 犯罪集團：資料日還在關＋剛公告入獄的（「被關或即將被關」，彰源 10/12 出獄也算）；我們的族群，
    # 不在任何族群的就用官方產業別（彰源、佳大＝鋼鐵）
    gangs: dict[str, list[dict[str, Any]]] = {}
    for code, p in ({**jailed_now, **jailed_next}).items():
        industry = INDUSTRY_NAMES.get((companies.get(code) or {}).get("industry") or "")
        for g in groups_of.get(code) or ([industry] if industry else []):
            gangs.setdefault(g, []).append({"code": code, "name": p["name"], "new": p["new"], "release": p["release"],
                                            "releaseMd": md(p["release"])})
    gang_list = [{"group": g, "stocks": sorted(rows, key=lambda r: r["code"])} for g, rows in gangs.items() if len(rows) >= 2]
    gang_list.sort(key=lambda g: (-len(g["stocks"]), g["group"]))

    # 今日入獄
    new_jail = []
    for p in sorted((p for p in punishes if p["new"]), key=lambda p: p["code"]):
        left = count_trading_days(p["start"], p["release"])
        new_jail.append({"code": p["code"], "name": p["name"], "market": MARKET_LABEL.get(p["market"], p["market"]),
                         "start": p["start"], "end": p["end"], "release": p["release"], "releaseMd": md(p["release"]),
                         "minutes": p["minutes"], "days": p["days"], "daysLeft": left, "measure": p["measure"],
                         "condition": p["condition"]})

    # 嫌疑名單
    suspects = []
    candidates = []
    for code, day_map in by_code_notice.items():
        market = markets.get(code, "TSE")
        own_day = latest.get(market, data_day)
        if own_day != data_day:
            continue   # 那個市場今天的公告還沒進來（上櫃鏡像晚一點），先不算，免得用舊資料
        past = [p for p in by_code_punish.get(code, []) if p["announce"] and p["announce"] <= data_day]
        after = max((p["announce"] for p in past), default=None)
        if code in jailed_next and jailed_next[code]["announce"] == data_day:
            continue   # 今天剛公告入獄
        acc = accumulation(day_map, data_day, after)
        if not (acc["pathA"] or acc["pathB"] or acc["pathC"] or acc["pathD"]):
            continue
        candidates.append((code, market, acc, day_map))
    share_of = shares_map([c for c, *_ in candidates]) if candidates else {}
    for code, *_ in candidates:   # 族群外的股票波段日報沒存股數，用這裡一天抓一次的全部公司資料
        if (companies.get(code) or {}).get("shares"):
            share_of[code] = companies[code]["shares"]
    for code, market, acc, day_map in candidates:
        jailed = code in jailed_next
        verb = "延長" if jailed else "被關"
        recent_days = trading_days_back(data_day, 5)
        recent = set()
        for d in recent_days:
            recent |= set(day_map.get(d, []))
        need_any = acc["pathB"] or acc["pathC"] or acc["pathD"]
        th = thresholds(code, market, data_day, need_any=need_any, recent_clauses=recent, verb=verb, shares=share_of.get(code))
        suspects.append({
            "code": code, "name": names.get(code, ""), "market": MARKET_LABEL.get(market, market), "jailed": jailed,
            "release": jailed_next[code]["release"] if jailed else None, "releaseMd": md(jailed_next[code]["release"]) if jailed else None,
            "status": _status_text(acc, day_map.get(data_day, []), jailed), "acc": acc,
            "high": th["high"], "lines": th["lines"], "close": th.get("close"), "volume": th.get("volume"),
            "missingBars": bool(th.get("missing")),
        })
    suspects.sort(key=lambda s: (not s["high"], s["jailed"], s["code"]))

    # 今日第一次第一款
    prev9 = trading_days_back(data_day, 10)[1:]
    first_time = []
    for code, day_map in by_code_notice.items():
        if 1 not in day_map.get(data_day, []):
            continue
        served = max((p["end"] for p in by_code_punish.get(code, []) if p["release"] <= data_day), default="")
        if not any(1 in day_map.get(d, []) for d in prev9 if d > served):
            first_time.append({"code": code, "name": names.get(code, ""), "market": MARKET_LABEL.get(markets.get(code, ""), "")})
    first_time.sort(key=lambda r: r["code"])

    # 前科索引：處置迄日近 95 天內、或近 25 個交易日內被注意
    punish_cut = (_d(data_day) - timedelta(days=INDEX_PUNISH_DAYS)).isoformat()
    notice_cut = trading_days_back(data_day, INDEX_NOTICE_DAYS)[-1]
    index_codes = {n["code"] for n in notices if n["date"] >= notice_cut} | {p["code"] for p in punishes if p["end"] >= punish_cut}
    index_codes |= {s["code"] for s in suspects} | set(jailed_next)
    index = [{"code": c, "name": names.get(c, ""), "tier": _tier(len(by_code_punish.get(c, [])))["key"],
              "jailed": c in jailed_next} for c in sorted(index_codes)]

    payload = {
        "status": "ok", "dataDate": data_day, "nextDay": next_day, "nextMd": md(next_day), "nextWeekday": weekday_label(next_day),
        "marketDates": latest, "updatedAt": _state.get("lastCollect"),
        "weeks": weeks, "pendingCount": pending, "gangs": gang_list, "newJail": new_jail,
        "suspects": suspects, "suspectCounts": {"high": sum(s["high"] for s in suspects), "low": sum(not s["high"] for s in suspects)},
        "firstTime": first_time, "index": index, "copyText": copy_text(next_day, suspects),
        "jailedNow": len(jailed_now), "jailedNext": len(jailed_next),
    }
    return payload


def copy_text(next_day: str, suspects: list[dict[str, Any]]) -> str:
    """可直接複製貼上（照莊爸的格式）。"""
    out = [f"{md(next_day)}({weekday_label(next_day)})"]
    for high, title in ((True, "⚠️高機率會關~\n[-5% 以上或者量很難躲]"), (False, "⚠️有機會躲過")):
        rows = [s for s in suspects if s["high"] == high]
        if not rows:
            continue
        out.append(title)
        for s in rows:
            for line in s["lines"]:
                if line["kind"] == "note":
                    continue
                tail = f" (今{s['volume']}張)" if line["kind"] == "volume" and s.get("volume") is not None else ""
                out.append(f"{s['code']} {s['name']} {line['text']}{tail}")
            if not any(line["kind"] != "note" for line in s["lines"]):
                out.append(f"{s['code']} {s['name']} {s['status']}")
        out.append("")
    return "\n".join(out).strip()


def stock_detail(code: str, now: datetime | None = None) -> dict[str, Any]:
    """個股前科查詢：目前狀態、處置紀錄、近 30 個交易日注意款別、明天判定。"""
    code = str(code or "").strip()
    payload = build_payload(now)
    if payload.get("status") != "ok":
        return {"status": "empty"}
    data_day = payload["dataDate"]
    since = (_d(data_day) - timedelta(days=HISTORY_DAYS + 30)).isoformat()
    notices = [n for n in load_notices(since) if n["code"] == code]
    punishes = [p for p in load_punishes(since) if p["code"] == code]
    if not notices and not punishes:
        return {"status": "none", "code": code}
    name = (notices[-1]["name"] if notices else "") or (punishes[-1]["name"] if punishes else "")
    market = (notices[-1]["market"] if notices else punishes[-1]["market"])
    records = []
    for p in sorted(punishes, key=lambda p: p["start"], reverse=True):
        records.append({"announce": p["announce"], "start": p["start"], "end": p["end"], "release": release_day(p["end"]),
                        "condition": p["condition"], "measure": p["measure"], "days": p["days"], "minutes": p["minutes"]})
    window = set(trading_days_back(data_day, 30))
    attention = [{"date": n["date"], "md": md(n["date"]), "clauses": n["clauses"], "info": n["info"]}
                 for n in sorted(notices, key=lambda n: n["date"], reverse=True) if n["date"] in window]
    suspect = next((s for s in payload["suspects"] if s["code"] == code), None)
    next_day = payload["nextDay"]
    jailed = next((r for r in records if r["start"] <= next_day <= r["end"]), None)
    if suspect:
        verdict = {"kind": "suspect", "text": suspect["status"], "high": suspect["high"], "lines": suspect["lines"]}
    elif jailed:
        verdict = {"kind": "jailed", "text": f"處置中，{md(jailed['release'])} 出獄"}
    else:
        last = attention[0] if attention else None
        verdict = {"kind": "clear", "text": "明天再被注意也還不會被關" if last else "近 30 個交易日沒有注意紀錄"}
    latest_clause = None
    if attention:
        latest_clause = {"md": attention[0]["md"], "clauses": attention[0]["clauses"]}
    return {"status": "ok", "code": code, "name": name, "market": MARKET_LABEL.get(market, market),
            "tier": _tier(len(records)), "jailCount": len(records), "records": records, "attention": attention,
            "latestClause": latest_clause, "verdict": verdict, "dataDate": data_day, "nextMd": payload["nextMd"]}


# ------------------------------------------------------------------ 背景排程

def collector_status() -> dict[str, Any]:
    with _lock:
        return {"lastCollect": _state["lastCollect"], "lastError": _state["lastError"], "result": _state["result"],
                "backfilled": _meta_get("backfilled")}


def run_collect(*, days: int | None = None) -> dict[str, Any]:
    try:
        return collect(days=days)
    except Exception as exc:  # noqa: BLE001
        logger.exception("處置監獄抓資料失敗")
        with _lock:
            _state["lastError"] = f"{type(exc).__name__}: {exc}"
        return {"error": str(exc)}


def _loop() -> None:
    run_collect()
    while True:
        time.sleep(POLL_SECONDS)
        now = _now()
        minute = now.hour * 60 + now.minute
        if is_trading_day(now.date()) and POLL_START <= minute <= POLL_END:
            run_collect()


def start_jail_collector() -> bool:
    global _thread
    if _thread and _thread.is_alive():
        return False
    _thread = threading.Thread(target=_loop, name="disposition-jail", daemon=True)
    _thread.start()
    return True
