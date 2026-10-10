"""下午報・黑龍回測：每天收盤後照「均線分數高＋當天收黑」（創高黑龍）挑名單存起來，
隔天用 D+1 開高低收算各種出場方式（收盤出、停利出、破黑低出、停利＋破黑低、隔天開盤出、
開高走・開低抱、D+2 收盤）的績效，以及爆發力（之後 1／2／3 個交易日的盤中最高）。

2026-09-28 使用者：照學員專區那套「創高黑龍・績效分析」做在下午報裡，參數可以在頁面上調。

表 heilong_daily：族群表內（不含金融股）每一檔、每一個交易日的開高低收量、漲跌幅、均線分數、
近 20 日漲逾 8% 次數、5 日均成交值、族群與族群平均分、當時看得到的集保大戶週增％、當天是否處置中。
留近 60 個交易日；每次重算只補沒有的日子，最新 3 天一定重算（日K修補、集保週資料晚到）。

2026-10-04 使用者：照學員專區的參數面板補齊——月季乖離％、排除剛出關 ≤N 個交易日、收盤價範圍、
當天成交量、只看有股票期貨、範圍（族群表內／全市場）、扣手續費與交易稅，以及「排除注意股」
（學員專區的「排除嫌疑犯」是處置預測，本站已拿掉；改用交易所公布的注意股，收盤後整表時把永豐
個股資訊列標注意的代號記進 heilong_attention_log，有紀錄的日子才排除得到）。全市場＝日K裡所有
4 位數代號的股票（不在族群表的沒有族群平均分、日K不足 240 根算不出分數就不會入選）。

2026-10-04 創高黑選股：日K補到三年（bars_history），表留 HISTORY_DAYS（預設 250）個交易日，給選股的實績回測與模擬帳戶用；
價格改用分割減資還原後的日K（price_adjust，原始日K不動；還原事件有變就整張重算），另外記「創高天數」
（今天收盤是近幾天最高收盤）。特徵改成一次掃過整串日K（滑動視窗），算 250 天也不慢；整表時分批寫入、最後一次換上，
讀的人不會看到寫一半的表。黑龍回測頁面最多看近 60 天（MAX_DAYS_PARAM），只載入需要的那幾天。
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from bisect import bisect_right
from statistics import median
from typing import Any

from brew_launch import MA_PERIODS, _latest_bar_date, group_codes, ma_alignment_score, skipped_codes
from brew_launch_history import group_and_name
from database import get_connection, initialize_database
from fundamentals_daily import BIG_HOLDER_LEVELS, _schema as _fundamentals_schema
from price_adjust import adjust_bars, events_version, load_events
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from swing_report import _logged_dispositions

logger = logging.getLogger(__name__)

TW_TZ = timezone(timedelta(hours=8))
HISTORY_DAYS = max(60, int(os.getenv("HANSTOCK_HEILONG_HISTORY_DAYS", "250")))   # 表裡留幾個交易日（創高黑選股要長一點）
RECOMPUTE_TAIL = 3               # 每次一定重算最新幾天
LOOKBACK_CALENDAR_DAYS = 560     # 算 240 日均線、360 日新高要往前抓的日K（約 380 個交易日）
CODE_BATCH = 150                 # 整表時一次算幾檔（記憶體不要一次全載）
HITS_DAYS = 20                   # 近 20 日
HITS_MIN_PCT = 8.0               # 單日漲幅 >8% 才算「有人在拉」
VALUE_DAYS = 5                   # 5 日均成交值
GAP_PCT = 35.0                   # 價格斷層：D+1 跟進場價差超過這個就留空
DISPOSITION_LOOKBACK_DAYS = 90
MAX_DAYS_PARAM = 60             # 黑龍回測頁面最多看近 60 天（0＝全部＝近 60 天）
BIAS_SHORT, BIAS_LONG = 20, 60   # 月季乖離：收盤價離「月線與季線的中點」幾％
FEE_PCT = 0.1425                 # 手續費％（買賣各一次），乘上折數
TAX_PCT = 0.3                    # 證交稅％（賣出一次）
SCOPES = ("groups", "market")

OFFICIAL_HI_PERIODS = (5, 10, 20, 60, 120, 360)   # 官網式「創 6 個天期新高」
OFFICIAL_RECENT_DAYS = 3                            # 最近 3 個交易日的最高收盤比這個天期內更早的都高（平手不算）就算創新高
# 官網式「多頭排列加分」三項。2026-10-10 使用者給莊爸均線分數排行頁（zhuang.tw/ma）：他寫的是 MA120>MA350（不是 240），
# 創新高也是平手不算；拿他 10/08 前 60 名逐檔比，這樣改才對得上（240／平手算的版本差 8 檔）。日K不到 350 根就用現有的平均。
OFFICIAL_ALIGN_PAIRS = ((20, 60), (60, 120), (120, 350))
ALIGN_LONG = 350
SCORE_VERSION = "2026-10-10"     # 算法改了就換：整張表重算
ALGOS = ("site", "official")
ALGO_LABELS = {"site": "本站", "official": "內定"}   # 2026-09-28 使用者：前端只用內定這套

K_KINDS = ("black", "red", "any")
MINE_KINDS = ("close", "tp", "sl", "both")
SORT_KEYS = ("score", "week", "gavg", "hits", "drop", "val")
METHODS = ("close", "tp", "sl", "both", "open", "ohl", "d2")
METHOD_LABELS = {"close": "收盤出場", "tp": "停利出場", "sl": "破黑低出場", "both": "停利＋破黑低",
                 "open": "隔天開盤出", "ohl": "開高走・開低抱", "d2": "D+2 收盤"}
CURVE_METHODS = ("close", "tp", "sl", "both")

DEFAULT_PARAMS: dict[str, Any] = {
    "score": 10, "k": "black", "min": -10.0, "max": 3.0,
    "week": None, "gavg": None, "hits": None, "val": None,
    "exdispo": True, "cap": 0, "sort": "score", "tp": 3.0, "mine": "both", "days": 10, "amt": 50.0, "algo": "site",
    # 2026-10-04 新增：月季乖離下／上限％、排除注意股、排除剛出關 ≤N 個交易日（0＝不用）、收盤價下／上限、
    # 當天成交量 ≥ 張、只看有股票期貨、範圍、手續費折數（0＝不扣費用，例如 0.6＝6 折；證交稅固定 0.3%）
    "bmin": None, "bmax": None, "exattn": False, "exout": 0, "pmin": None, "pmax": None, "vmin": None,
    "fut": False, "scope": "groups", "fee": 0.0,
}

RULES = [
    "進場＝符合條件那天的收盤價；D+1＝下一個有日K的交易日。日K不足 240 根算不出均線分數、不會入選。",
    "均線分數（內定算法，滿分 15）＝收盤站上 5／10／20／60／120／240 日均線各 1 分＋創 6 個天期（5／10／20／60／120／360 日）新高各 1 分（最近 3 個交易日的最高收盤比那個天期內更早的都高，平手不算）＋多頭排列加分 3 分（20 日線在 60 日線上、60 在 120 上、120 在 350 上各 1 分）。",
    "黑K＝收盤＜開盤、紅K＝收盤＞開盤；漲跌幅跟前一天收盤比。",
    "週籌碼＝那天當時看得到的集保週（結算日早於那天的最近一週）400 張以上大戶張數比前一週的增減％；沒有資料的股，勾了這條件就不算符合。",
    "族群平均分＝該股所屬族群全部成員當天均線分數的平均。5 日均成交值＝近 5 天「收盤價×成交量」的平均（億），是估算值。",
    "停利出場＝D+1 開盤 ≥ 目標就開盤賣；盤中最高碰到目標 → 用目標價賣；都沒有 → 收盤賣。破黑低出場＝D+1 開盤就低於黑K最低 → 開盤賣；盤中最低跌破黑K最低 → 用黑K最低價賣；沒破 → 收盤賣。",
    "停利＋破黑低＝兩個一起掛；開盤就到目標先算停利、開盤就破低先算停損，否則同一天兩個都碰到（日K分不出先後）保守算停損。",
    "開高走・開低抱＝開盤高於進場價就開盤賣，否則抱到收盤。D+2 收盤＝抱兩天，資金會跟隔天那批重疊，總損益參考就好。",
    "爆發力＝之後 1／2／3 個交易日的盤中最高；最高是盤中價、實際賣不到，看的是「停利目標到得到嗎」。",
    "處置＝用本站處置紀錄（2026-09-26 起）判斷那天是否處置中；更早的日子沒有紀錄，一律當沒處置。剛出關＝處置結束後第幾個交易日（出關日＝第 1 天），只有紀錄到的處置算得出來。",
    "注意股＝每個交易日收盤後整表時，把永豐個股資訊列標「注意」的代號記下來（交易所當天公布注意交易資訊的股票）；只有記錄起始日之後的日子排除得到，之前的日子一律當不是注意股。",
    "月季乖離％＝收盤價 ÷（20 日線＋60 日線）÷2 − 1；日K不足 60 根算不出來，設了門檻就不算符合。超過 30% 通常是相對高檔。",
    "費用＝勾了就每筆扣「手續費 0.1425%×折數×2（買＋賣）＋證交稅 0.3%」，各種出場方式的％、總損益都扣；爆發力（盤中最高）不扣。",
    "價格斷層（D+1 開盤或收盤跟進場價差超過 35%，例如分割、減資）留空不算；價格未還原除權息。",
    "每檔 N 萬只是把％換成金額看總損益，不考慮零股與資金重疊。範圍＝族群表內（不含金融股）或全市場（日K裡所有 4 位數代號的股票；不在族群表的沒有族群平均分，設了族群平均分門檻就不會入選）。",
    "樣本短，參數調到某段特別好看，要用其他區間驗證再用。",
]

Bar = tuple[str, float, float, float, float, int]   # (日期, 開, 高, 低, 收, 量張)


# ------------------------------------------------------------------ 資料表

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS heilong_daily (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume INTEGER NOT NULL,
            prev_close REAL, change_pct REAL, score INTEGER, hits20 INTEGER, val5 REAL,
            group_name TEXT, group_avg REAL, week_pct REAL, week_date TEXT, disposed INTEGER NOT NULL DEFAULT 0,
            score2 INTEGER, group_avg2 REAL, bias REAL, out_days INTEGER,
            attention INTEGER NOT NULL DEFAULT 0, in_group INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (trade_date, stock_code)
        )"""
    )
    columns = {str(r["name"]) for r in connection.execute("PRAGMA table_info(heilong_daily)").fetchall()}
    for column, kind in (("score2", "INTEGER"), ("group_avg2", "REAL"), ("bias", "REAL"), ("out_days", "INTEGER"),
                         ("attention", "INTEGER NOT NULL DEFAULT 0"), ("in_group", "INTEGER NOT NULL DEFAULT 1"), ("hi_len", "INTEGER"),
                         ("ma_bits", "INTEGER"), ("hi_bits", "INTEGER"), ("align_n", "INTEGER")):
        if column not in columns:
            connection.execute(f"ALTER TABLE heilong_daily ADD COLUMN {column} {kind}")
    connection.execute("CREATE TABLE IF NOT EXISTS heilong_meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS heilong_attention_log (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, PRIMARY KEY (trade_date, stock_code)
        )"""
    )


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _bar_dates(limit: int) -> list[str]:
    """有日K的交易日，新到舊。"""
    from daily_bars_store import recent_bar_dates

    return recent_bar_dates(limit)


def _load_bars(codes: list[str], *, since: str, until: str, adjusted: bool = False) -> dict[str, list[Bar]]:
    """{代號: [(日期, 開, 高, 低, 收, 量張), ...舊到新]}，since ≤ 日期 ≤ until。adjusted＝套用分割減資還原（price_adjust）。"""
    initialize_database()
    out: dict[str, list[Bar]] = {}
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                f"""
                SELECT stock_code, substr(bar_time, 1, 10) AS d, open, high, low, close, volume FROM bars_1d
                WHERE stock_code IN ({placeholders}) AND bar_time >= ? AND bar_time < ? || 'z'
                """,
                (*batch, since, until),
            ).fetchall()
            for row in rows:
                out.setdefault(str(row["stock_code"]).strip().upper(), []).append(
                    (str(row["d"]), float(row["open"] or 0), float(row["high"]), float(row["low"]), float(row["close"]), int(row["volume"] or 0))
                )
    for bars in out.values():
        bars.sort(key=lambda bar: bar[0])
    if adjusted:
        events = load_events(out.keys())
        for code, bars in out.items():
            if code in events:
                out[code] = adjust_bars(bars, events[code])
    return out


def _tdcc_weeks(codes: list[str]) -> dict[str, list[tuple[str, float]]]:
    """{代號: [(集保結算日 新到舊, 400 張以上大戶合計股數)]}。"""
    initialize_database()
    out: dict[str, list[tuple[str, float]]] = {}
    levels = ",".join(str(x) for x in BIG_HOLDER_LEVELS)
    with get_connection() as connection:
        _fundamentals_schema(connection)
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT data_date, stock_code, SUM(shares) AS shares FROM tdcc_weekly
                    WHERE stock_code IN ({','.join('?' for _ in batch)}) AND level IN ({levels})
                    GROUP BY data_date, stock_code""",
                tuple(batch),
            ).fetchall()
            for r in rows:
                out.setdefault(str(r["stock_code"]).upper(), []).append((str(r["data_date"]), float(r["shares"] or 0)))
    for weeks in out.values():
        weeks.sort(reverse=True)
    return out


def _week_pct(weeks: list[tuple[str, float]], trade_date: str) -> tuple[float | None, str | None]:
    """那天當時看得到的集保週（結算日早於那天）：大戶張數比前一週增減％。"""
    visible = [(d, shares) for d, shares in weeks if d < trade_date]
    if not visible:
        return None, None
    if len(visible) < 2 or visible[1][1] <= 0:
        return None, visible[0][0]
    return round((visible[0][1] / visible[1][1] - 1) * 100, 2), visible[0][0]


def _disposition_spans(since: str) -> dict[str, list[tuple[str, str]]]:
    spans: dict[str, list[tuple[str, str]]] = {}
    for e in _logged_dispositions(since):
        spans.setdefault(e["code"], []).append((e["start"], e.get("end") or "9999-12-31"))
    return spans


def _disposed(spans: dict[str, list[tuple[str, str]]], code: str, trade_date: str) -> bool:
    return any(start <= trade_date <= end for start, end in spans.get(code, ()))


def _out_days(spans: dict[str, list[tuple[str, str]]], code: str, trade_date: str, trading_dates: list[str]) -> int | None:
    """出關第幾個交易日（出關日＝1）；trading_dates 舊到新。還在處置中、或從沒處置過回 None；多次處置取最近一次。"""
    i = bisect_right(trading_dates, trade_date) - 1
    if i < 0 or trading_dates[i] != trade_date:
        return None
    best: int | None = None
    for _start, end in spans.get(code, ()):
        if end >= trade_date:
            continue
        j = bisect_right(trading_dates, end)   # 出關日＝處置結束後第一個交易日
        if j > i:
            continue
        days = i - j + 1
        if best is None or days < best:
            best = days
    return best


def _log_attention(trade_date: str, codes: list[str]) -> int:
    """把永豐個股資訊列現在標「注意」的代號記成 trade_date 的注意股（收盤後整表時呼叫）。"""
    try:
        from stock_trading_eligibility import peek_trading_eligibility
    except Exception:  # noqa: BLE001
        return 0
    flagged = [c for c in codes if (peek_trading_eligibility(c) or {}).get("attention")]
    if not flagged:
        return 0
    with get_connection() as connection:
        _schema(connection)
        connection.executemany("INSERT OR IGNORE INTO heilong_attention_log (trade_date, stock_code) VALUES (?, ?)", [(trade_date, c) for c in flagged])
    return len(flagged)


def _attention_pairs(dates: list[str]) -> set[tuple[str, str]]:
    if not dates:
        return set()
    with get_connection() as connection:
        _schema(connection)
        placeholders = ",".join("?" for _ in dates)
        rows = connection.execute(f"SELECT trade_date, stock_code FROM heilong_attention_log WHERE trade_date IN ({placeholders})", tuple(dates)).fetchall()
    return {(str(r["trade_date"]), str(r["stock_code"])) for r in rows}


def attention_log_since() -> str | None:
    """注意股紀錄的起始日（給前端註明「紀錄自某日起」）。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT MIN(trade_date) AS d FROM heilong_attention_log").fetchone()
    return str(row["d"]) if row and row["d"] else None


def _market_codes(since: str) -> list[str]:
    """全市場：日K裡 since 之後有資料的 4 位數代號（排除 ETF 等）。"""
    with get_connection() as connection:
        rows = connection.execute("SELECT DISTINCT stock_code FROM bars_1d WHERE bar_time >= ?", (since,)).fetchall()
    # 4 位數純數字、不是 00 開頭（0050／0056 這類 ETF 也是 4 位數）
    return sorted(c for c in (str(r["stock_code"]).strip().upper() for r in rows) if len(c) == 4 and c.isdigit() and not c.startswith("00"))


def _has_futures(code: str) -> bool:
    from stock_trading_eligibility import has_stock_futures

    return has_stock_futures(code)


# ------------------------------------------------------------------ 特徵

def official_parts(closes: list[float], mas: dict[int, float] | None = None) -> tuple[int, int, int] | None:
    """官網式均線分數的三部分：(站上均線位元, 創新高位元, 多頭排列分)。位元第 k 位＝第 k 個天期（MA_PERIODS／OFFICIAL_HI_PERIODS 的順序）。
    站上：收盤＞5／10／20／60／120／240 日均線；創新高：最近 3 個交易日的最高收盤＞那個天期（5／10／20／60／120／360 日，
    不夠長用現有長度）內更早的最高收盤（平手不算）；排列：20 日＞60 日、60 日＞120 日、120 日＞350 日各 1 分。
    closes 舊到新、最後一筆是當天；不足 240 根回 None。mas 可以先給 MA_PERIODS 的均線（350 日不給就自己算）。"""
    n = len(closes)
    if n < max(MA_PERIODS):
        return None
    mas = dict(mas) if mas else {p: sum(closes[-p:]) / p for p in MA_PERIODS}
    if ALIGN_LONG not in mas:
        span = min(ALIGN_LONG, n)
        mas[ALIGN_LONG] = sum(closes[-span:]) / span
    close = closes[-1]
    ma_bits = sum(1 << k for k, p in enumerate(MA_PERIODS) if close > mas[p])
    hi_bits = 0
    for k, p in enumerate(OFFICIAL_HI_PERIODS):
        window = closes[-p:]
        earlier = window[:-OFFICIAL_RECENT_DAYS]
        if earlier and max(window[-OFFICIAL_RECENT_DAYS:]) > max(earlier):
            hi_bits |= 1 << k
    align = sum(1 for short, long in OFFICIAL_ALIGN_PAIRS if mas[short] > mas[long])
    return ma_bits, hi_bits, align


def official_score(closes: list[float], mas: dict[int, float] | None = None) -> int | None:
    """官網式均線分數（滿分 15）＝站上 6 條均線＋創 6 個天期新高＋多頭排列 3 分（細節見 official_parts）。不足 240 根回 None。"""
    parts = official_parts(closes, mas)
    if parts is None:
        return None
    ma_bits, hi_bits, align = parts
    return bin(ma_bits).count("1") + bin(hi_bits).count("1") + align


def _sliding_max(values: list[float], width: int) -> list[float]:
    """每個位置 i：values[max(0, i−width+1) .. i] 的最大值（單調佇列，一次掃完）。"""
    out = [0.0] * len(values)
    window: deque[int] = deque()
    for i, v in enumerate(values):
        while window and values[window[-1]] <= v:
            window.pop()
        window.append(i)
        if window[0] <= i - width:
            window.popleft()
        out[i] = values[window[0]]
    return out


def hi_lengths(closes: list[float]) -> list[int]:
    """創高天數：今天收盤是近幾天（含今天）的最高收盤，平手也算＝往前數到第一根收盤比今天高的那天之前有幾天；
    前面都沒有比較高的＝手上日K的根數。「創 N 日新高」就是創高天數 ≥ N。"""
    out = [0] * len(closes)
    stack: list[int] = []
    for i, c in enumerate(closes):
        while stack and closes[stack[-1]] <= c:
            stack.pop()
        out[i] = i - stack[-1] if stack else i + 1
        stack.append(i)
    return out


def compute_features(bars: list[Bar], wanted: set[str]) -> dict[str, dict[str, Any]]:
    """bars 舊到新；回 {日期: 特徵}，只算 wanted 裡的日期。均線分數要 240 根（含當天）才有。
    跟逐日呼叫 official_score 的結果一樣，只是新高那幾項用滑動視窗一次算完。"""
    out: dict[str, dict[str, Any]] = {}
    if not bars:
        return out
    closes = [b[4] for b in bars]
    prefix = [0.0]
    for c in closes:
        prefix.append(prefix[-1] + c)
    changes: list[float | None] = [None]
    for i in range(1, len(closes)):
        changes.append((closes[i] / closes[i - 1] - 1) * 100 if closes[i - 1] > 0 else None)
    big = [0]
    for ch in changes:
        big.append(big[-1] + (1 if ch is not None and ch > HITS_MIN_PCT else 0))
    longest = max(MA_PERIODS)
    recent = OFFICIAL_RECENT_DAYS
    # 天期內「最近 3 天以前」那段的最高收盤：寬度 p−3、結束在 i−3 的滑動最大值
    earlier_max = {p: _sliding_max(closes, p - recent) for p in OFFICIAL_HI_PERIODS}
    recent_max = _sliding_max(closes, recent)
    hi_len = hi_lengths(closes)
    for i, (d, o, h, l, c, v) in enumerate(bars):
        if d not in wanted:
            continue
        n = i + 1
        score = score2 = ma_bits = hi_bits = align = None
        if n >= longest:
            mas = {p: (prefix[n] - prefix[n - p]) / p for p in MA_PERIODS}
            score = ma_alignment_score(mas)          # 本站算法只看 MA_PERIODS，350 日線要加在後面
            span = min(ALIGN_LONG, n)
            mas[ALIGN_LONG] = (prefix[n] - prefix[n - span]) / span
            ma_bits = sum(1 << k for k, p in enumerate(MA_PERIODS) if c > mas[p])
            hi_bits = sum(1 << k for k, p in enumerate(OFFICIAL_HI_PERIODS) if i >= recent and recent_max[i] > earlier_max[p][i - recent])
            align = sum(1 for short, long in OFFICIAL_ALIGN_PAIRS if mas[short] > mas[long])
            score2 = bin(ma_bits).count("1") + bin(hi_bits).count("1") + align
        hits = big[i + 1] - big[max(1, i - HITS_DAYS + 1)]
        values = [bars[j][4] * bars[j][5] * 1000 / 1e8 for j in range(max(0, i - VALUE_DAYS + 1), i + 1)]
        bias = None
        if n >= BIAS_LONG:
            mid = ((prefix[n] - prefix[n - BIAS_SHORT]) / BIAS_SHORT + (prefix[n] - prefix[n - BIAS_LONG]) / BIAS_LONG) / 2
            bias = _r2((c / mid - 1) * 100) if mid > 0 else None
        out[d] = {
            "open": o, "high": h, "low": l, "close": c, "volume": v,
            "prevClose": closes[i - 1] if i else None,
            "changePct": _r2(changes[i]),
            "score": score,
            "score2": score2,
            "hits20": hits,
            "val5": _r2(_mean(values)),
            "bias": bias,
            "hiLen": hi_len[i],
            "maBits": ma_bits, "hiBits": hi_bits, "align": align,
        }
    return out


def _group_members() -> dict[str, list[str]]:
    return {name: [str(c).strip().upper() for c, _n in members] for name, members in STOCK_GROUPS.items() if name not in SPECIAL_GROUP_NAMES}


# ------------------------------------------------------------------ 建表

_state: dict[str, Any] = {"builtAt": None, "lastDate": None, "dates": 0, "rows": 0, "lastRebuilt": [], "lastError": None}
_lock = threading.Lock()
_build_lock = threading.Lock()


def collector_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def rebuild(*, force: bool = False) -> dict[str, Any]:
    """補齊近 60 個交易日的表；最新 3 天一定重算。force＝全部重算。"""
    with _build_lock:
        try:
            result = _rebuild(force=force)
            with _lock:
                _state.update({"builtAt": datetime.now(TW_TZ).isoformat(timespec="seconds"), "lastDate": result.get("date"),
                               "dates": result.get("dates", 0), "rows": result.get("rows", 0), "lastRebuilt": result.get("rebuilt", []), "lastError": None})
            if result.get("rebuiltCount"):
                _warm_picker()
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("heilong rebuild failed")
            with _lock:
                _state["lastError"] = str(exc)
            raise


def _meta_get(connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM heilong_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _meta_set(connection, key: str, value: str) -> None:
    connection.execute("INSERT INTO heilong_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


INSERT_COLUMNS = ("trade_date, stock_code, open, high, low, close, volume, prev_close, change_pct, score, hits20, val5, group_name, group_avg, "
                  "week_pct, week_date, disposed, score2, group_avg2, bias, out_days, attention, in_group, hi_len, ma_bits, hi_bits, align_n")


def _row_tuple(d: str, code: str, f: dict[str, Any]) -> tuple:
    return (d, code, f["open"], f["high"], f["low"], f["close"], f["volume"], f["prevClose"], f["changePct"], f["score"], f["hits20"], f["val5"],
            f["group"], f["groupAvg"], f["weekPct"], f["weekDate"], 1 if f["disposed"] else 0, f["score2"], f["groupAvg2"],
            f["bias"], f["outDays"], 1 if f["attention"] else 0, 1 if f["inGroup"] else 0, f.get("hiLen"),
            f.get("maBits"), f.get("hiBits"), f.get("align"))


def _warm_picker() -> None:
    """表重算過：有人用過創高黑選股（記憶體裡已經有一份特徵）才在背景換成新的，下一個打開的人不用等；
    沒人用過就不載（省記憶體，測試也不會留下背景執行緒）。延遲匯入：選股模組會 import 這個模組。"""
    try:
        import heilong_picker
    except Exception:  # noqa: BLE001
        return
    if heilong_picker._panel is None:
        return
    threading.Thread(target=heilong_picker.warm, name="hanstock-picker-warm", daemon=True).start()


def _rebuild(*, force: bool) -> dict[str, Any]:
    initialize_database()
    latest = _latest_bar_date()
    if not latest:
        return {"date": None, "rebuilt": [], "dates": 0, "rows": 0}
    target = sorted(_bar_dates(HISTORY_DAYS))
    version = events_version()
    with get_connection() as connection:
        _schema(connection)
        have = {str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM heilong_daily").fetchall()}
        missing = connection.execute(
            "SELECT COUNT(*) FROM heilong_daily WHERE score IS NOT NULL AND (score2 IS NULL OR bias IS NULL OR hi_len IS NULL OR ma_bits IS NULL)"
        ).fetchone()[0]
        old_version = _meta_get(connection, "adjust_version")
        old_score_version = _meta_get(connection, "score_version")
    reason = "force" if force else None
    if missing:
        force, reason = True, "新欄位補算"     # 官網式分數／月季乖離／創高天數剛加上：整張表補算一次
    if old_version != version:
        force, reason = True, "還原事件有變"   # 分割減資還原的事件變了：以前的價格都要重算
    if have and old_score_version != SCORE_VERSION:
        force, reason = True, "均線分數算法更新"   # 2026-10-10：排列第三項改 120＞350、創新高平手不算
    tail = set(target[-RECOMPUTE_TAIL:])
    todo = [d for d in target if force or d not in have or d in tail]
    result: dict[str, Any] = {"date": latest, "rebuilt": todo, "rebuiltCount": len(todo), "dates": len(target), "reason": reason}
    if not todo:
        with get_connection() as connection:
            result["rows"] = connection.execute("SELECT COUNT(*) FROM heilong_daily").fetchone()[0]
        return result
    skipped = skipped_codes()
    group_set = {c for c in group_codes() if c not in skipped}
    since = (datetime.strptime(todo[0], "%Y-%m-%d") - timedelta(days=LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    # 全市場：族群表內之外的 4 位數代號也建列（in_group=0），日K不足的自然算不出分數
    codes = sorted(group_set | set(_market_codes(todo[0])))
    spans = _disposition_spans((datetime.strptime(todo[0], "%Y-%m-%d") - timedelta(days=DISPOSITION_LOOKBACK_DAYS)).strftime("%Y-%m-%d"))
    trading_dates = sorted(_bar_dates(HISTORY_DAYS + 70))   # 出關天數要往前多看一點
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    if latest in todo and latest == today:
        _log_attention(latest, codes)   # 今天收盤後整表：順便記下今天的注意股
    attention = _attention_pairs(todo)
    wanted = set(todo)
    members = _group_members()

    # 第一輪：只算族群成員的分數，先把每天各族群的平均分算好
    member_codes = sorted({c for ms in members.values() for c in ms} & set(codes))
    scores: dict[str, dict[str, tuple[Any, Any]]] = {}
    for i in range(0, len(member_codes), CODE_BATCH):
        batch = member_codes[i:i + CODE_BATCH]
        for code, bars in _load_bars(batch, since=since, until=todo[-1], adjusted=True).items():
            scores[code] = {d: (f["score"], f["score2"]) for d, f in compute_features(bars, wanted).items()}
    group_avg: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for name, member_list in members.items():
        for d in todo:
            s1 = [scores[c][d][0] for c in member_list if c in scores and d in scores[c] and scores[c][d][0] is not None]
            s2 = [scores[c][d][1] for c in member_list if c in scores and d in scores[c] and scores[c][d][1] is not None]
            group_avg[(name, d)] = (round(_mean(s1), 1) if s1 else None, round(_mean(s2), 1) if s2 else None)
    scores.clear()

    # 第二輪：全部股票分批算完整特徵，寫進暫存表；最後一次換上（讀的人不會看到寫一半的表）
    weeks = _tdcc_weeks(codes)
    total = 0
    with get_connection() as connection:
        _schema(connection)
        connection.execute("DROP TABLE IF EXISTS heilong_daily_new")
        connection.execute("CREATE TABLE heilong_daily_new AS SELECT * FROM heilong_daily WHERE 0")
    for i in range(0, len(codes), CODE_BATCH):
        batch = codes[i:i + CODE_BATCH]
        rows: list[tuple] = []
        for code, bars in _load_bars(batch, since=since, until=todo[-1], adjusted=True).items():
            group, _name = group_and_name(code)
            for d, f in compute_features(bars, wanted).items():
                f["group"] = group
                f["weekPct"], f["weekDate"] = _week_pct(weeks.get(code, []), d)
                f["disposed"] = _disposed(spans, code, d)
                f["outDays"] = None if f["disposed"] else _out_days(spans, code, d, trading_dates)
                f["attention"] = (d, code) in attention
                f["inGroup"] = code in group_set
                f["groupAvg"], f["groupAvg2"] = group_avg.get((group, d), (None, None))
                rows.append(_row_tuple(d, code, f))
        with get_connection() as connection:
            connection.executemany(f"INSERT INTO heilong_daily_new ({INSERT_COLUMNS}) VALUES ({','.join('?' for _ in INSERT_COLUMNS.split(','))})", rows)
        total += len(rows)
    with get_connection() as connection:
        connection.execute("BEGIN")
        for k in range(0, len(todo), 500):
            chunk = todo[k:k + 500]
            connection.execute(f"DELETE FROM heilong_daily WHERE trade_date IN ({','.join('?' for _ in chunk)})", chunk)
        connection.execute(f"INSERT INTO heilong_daily ({INSERT_COLUMNS}) SELECT {INSERT_COLUMNS} FROM heilong_daily_new")
        if target:
            connection.execute("DELETE FROM heilong_daily WHERE trade_date < ?", (target[0],))
        _meta_set(connection, "adjust_version", version)
        _meta_set(connection, "score_version", SCORE_VERSION)
        connection.execute("COMMIT")
        connection.execute("DROP TABLE IF EXISTS heilong_daily_new")
        result["rows"] = connection.execute("SELECT COUNT(*) FROM heilong_daily").fetchone()[0]
    result["written"] = total
    return result


def table_dates() -> list[str]:
    """表裡的交易日，舊到新。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        return [str(r["trade_date"]) for r in connection.execute("SELECT DISTINCT trade_date FROM heilong_daily ORDER BY trade_date").fetchall()]


def load_rows(days: int | None = None) -> tuple[list[str], dict[str, dict[str, dict[str, Any]]]]:
    """(交易日 舊到新, {交易日: {代號: 列}})；days＝只載最近幾個交易日（表留 250 天，全載太大）。"""
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        if days:
            dates = [str(r["trade_date"]) for r in connection.execute(
                "SELECT DISTINCT trade_date FROM heilong_daily ORDER BY trade_date DESC LIMIT ?", (int(days),)).fetchall()]
            rows = connection.execute("SELECT * FROM heilong_daily WHERE trade_date >= ?", (min(dates),)).fetchall() if dates else []
        else:
            rows = connection.execute("SELECT * FROM heilong_daily").fetchall()
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        code = str(r["stock_code"])
        table.setdefault(str(r["trade_date"]), {})[code] = {
            "code": code, "date": str(r["trade_date"]),
            "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]), "volume": int(r["volume"]),
            "prevClose": r["prev_close"], "changePct": r["change_pct"], "score": r["score"], "hits20": r["hits20"], "val5": r["val5"],
            "group": r["group_name"] or "", "groupAvg": r["group_avg"], "weekPct": r["week_pct"], "weekDate": r["week_date"],
            "disposed": bool(r["disposed"]),
            "score2": r["score2"], "groupAvg2": r["group_avg2"],
            "bias": r["bias"], "outDays": r["out_days"], "attention": bool(r["attention"]), "inGroup": bool(r["in_group"]),
            "hiLen": r["hi_len"], "maBits": r["ma_bits"], "hiBits": r["hi_bits"], "align": r["align_n"],
        }
    return sorted(table), table


# ------------------------------------------------------------------ 參數

def _num(value: Any, name: str, *, integer: bool = False) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必須是數字") from exc


def normalize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    p = dict(DEFAULT_PARAMS)
    for key in ("score", "cap", "days", "hits", "exout", "vmin"):
        if key in raw and raw[key] is not None:
            p[key] = _num(raw[key], key, integer=True)
    for key in ("min", "max", "week", "gavg", "val", "tp", "amt", "bmin", "bmax", "pmin", "pmax", "fee"):
        if key in raw and raw[key] is not None:
            p[key] = _num(raw[key], key)
    for key in ("exattn", "fut"):
        if key in raw and raw[key] is not None:
            p[key] = str(raw[key]).lower() not in ("0", "false", "no", "")
    if "scope" in raw and raw["scope"]:
        p["scope"] = str(raw["scope"])
    if "k" in raw and raw["k"]:
        p["k"] = str(raw["k"])
    if "sort" in raw and raw["sort"]:
        p["sort"] = str(raw["sort"])
    if "mine" in raw and raw["mine"]:
        p["mine"] = str(raw["mine"])
    if "algo" in raw and raw["algo"]:
        p["algo"] = str(raw["algo"])
    if "exdispo" in raw and raw["exdispo"] is not None:
        p["exdispo"] = str(raw["exdispo"]).lower() not in ("0", "false", "no", "")
    if p["score"] is None or not 0 <= p["score"] <= 15:
        raise ValueError("score 必須在 0～15")
    if p["k"] not in K_KINDS:
        raise ValueError("k 必須是 black／red／any")
    if p["min"] is None or p["max"] is None or p["min"] > p["max"]:
        raise ValueError("漲跌幅範圍 min 不能大於 max")
    if p["sort"] not in SORT_KEYS:
        raise ValueError("sort 必須是 " + "／".join(SORT_KEYS))
    if p["mine"] not in MINE_KINDS:
        raise ValueError("mine 必須是 close／tp／sl／both")
    if p["algo"] not in ALGOS:
        raise ValueError("algo 必須是 site（本站）或 official（內定）")
    if p["tp"] is None or p["tp"] <= 0 or p["tp"] > 50:
        raise ValueError("tp 必須在 0～50")
    if p["cap"] is None or p["cap"] < 0:
        raise ValueError("cap 不能是負數")
    if p["days"] is None or p["days"] < 0:
        raise ValueError("days 不能是負數")
    if p["days"] > MAX_DAYS_PARAM:
        p["days"] = 0
    if p["amt"] is None or p["amt"] <= 0:
        raise ValueError("amt 必須大於 0")
    if p["bmin"] is not None and p["bmax"] is not None and p["bmin"] > p["bmax"]:
        raise ValueError("月季乖離 bmin 不能大於 bmax")
    if p["pmin"] is not None and p["pmax"] is not None and p["pmin"] > p["pmax"]:
        raise ValueError("收盤價 pmin 不能大於 pmax")
    if p["exout"] is None or p["exout"] < 0:
        raise ValueError("exout 不能是負數")
    if p["vmin"] is not None and p["vmin"] < 0:
        raise ValueError("vmin 不能是負數")
    if p["scope"] not in SCOPES:
        raise ValueError("scope 必須是 groups（族群表內）或 market（全市場）")
    if p["fee"] is None or p["fee"] < 0 or p["fee"] > 1:
        raise ValueError("fee（手續費折數）必須在 0～1，0＝不扣費用")
    if p["hits"] is not None and p["hits"] < 0:
        raise ValueError("hits 不能是負數")
    return p


# ------------------------------------------------------------------ 選股與出場

def score_keys(algo: str) -> tuple[str, str]:
    """(個股分數欄, 族群平均欄)：本站 score／groupAvg，官網式 score2／groupAvg2。"""
    return ("score2", "groupAvg2") if algo == "official" else ("score", "groupAvg")


def _sort_value(row: dict[str, Any], key: str, algo: str = "site") -> float:
    if key == "drop":
        return row["changePct"] if row["changePct"] is not None else float("inf")
    score_key, gavg_key = score_keys(algo)
    field = {"score": score_key, "week": "weekPct", "gavg": gavg_key, "hits": "hits20", "val": "val5"}[key]
    value = row.get(field)
    return float("-inf") if value is None else -float(value)


def select_rows(rows: dict[str, dict[str, Any]], p: dict[str, Any]) -> list[dict[str, Any]]:
    """一天裡符合參數的股，依排序取前 cap 檔（0＝不限）。"""
    picked: list[dict[str, Any]] = []
    score_key, gavg_key = score_keys(p["algo"])
    for r in rows.values():
        if r.get(score_key) is None or r[score_key] < p["score"]:
            continue
        if p["k"] == "black" and not (r["open"] > 0 and r["close"] < r["open"]):
            continue
        if p["k"] == "red" and not (r["open"] > 0 and r["close"] > r["open"]):
            continue
        cp = r["changePct"]
        if cp is None or cp < p["min"] or cp > p["max"]:
            continue
        if p["week"] is not None and (r["weekPct"] is None or r["weekPct"] < p["week"]):
            continue
        if p["gavg"] is not None and (r.get(gavg_key) is None or r[gavg_key] < p["gavg"]):
            continue
        if p["hits"] is not None and (r["hits20"] is None or r["hits20"] < p["hits"]):
            continue
        if p["val"] is not None and (r["val5"] is None or r["val5"] < p["val"]):
            continue
        if p["exdispo"] and r["disposed"]:
            continue
        if p["scope"] == "groups" and not r.get("inGroup", True):
            continue
        if p["bmin"] is not None or p["bmax"] is not None:
            b = r.get("bias")
            if b is None or (p["bmin"] is not None and b < p["bmin"]) or (p["bmax"] is not None and b > p["bmax"]):
                continue
        if p["exattn"] and r.get("attention"):
            continue
        if p["exout"] > 0 and r.get("outDays") is not None and r["outDays"] <= p["exout"]:
            continue
        if p["pmin"] is not None and r["close"] < p["pmin"]:
            continue
        if p["pmax"] is not None and r["close"] > p["pmax"]:
            continue
        if p["vmin"] is not None and r["volume"] < p["vmin"]:
            continue
        if p["fut"] and not _has_futures(r["code"]):
            continue
        picked.append(r)
    picked.sort(key=lambda r: (_sort_value(r, p["sort"], p["algo"]), -(r.get(score_key) or 0), r["code"]))
    if p["cap"] > 0:
        picked = picked[:p["cap"]]
    return picked


def _pct(entry: float, price: float) -> float:
    return round((price / entry - 1) * 100, 2)


def exits(entry: float, low_k: float, nxt: tuple[float, float, float, float], tp: float | None) -> dict[str, Any]:
    """D+1（開、高、低、收）各種出場方式的報酬％，以及盤中有沒有碰到停利／跌破黑K低。"""
    o, h, l, c = nxt
    close_x = _pct(entry, c)
    target = entry * (1 + tp / 100) if tp else None
    hit_tp = (h >= target) if target is not None else None
    hit_sl = l < low_k
    if target is None:
        tp_x = close_x
    elif o >= target:
        tp_x = _pct(entry, o)
    elif h >= target:
        tp_x = _pct(entry, target)
    else:
        tp_x = close_x
    if o < low_k:
        sl_x = _pct(entry, o)
    elif l < low_k:
        sl_x = _pct(entry, low_k)
    else:
        sl_x = close_x
    if target is None:
        both_x = sl_x
    elif o >= target:
        both_x = _pct(entry, o)
    elif o < low_k:
        both_x = _pct(entry, o)
    elif l < low_k:
        both_x = _pct(entry, low_k)      # 同一天兩個都碰到（或只破低）保守算停損
    elif h >= target:
        both_x = _pct(entry, target)
    else:
        both_x = close_x
    open_x = _pct(entry, o)
    ohl_x = open_x if o > entry else close_x
    return {"close": close_x, "tp": tp_x, "sl": sl_x, "both": both_x, "open": open_x, "ohl": ohl_x, "hitTp": hit_tp, "hitSl": hit_sl}


def _gap(entry: float, row: dict[str, Any] | None) -> bool:
    if row is None or entry <= 0:
        return False
    return abs(row["open"] / entry - 1) * 100 > GAP_PCT or abs(row["close"] / entry - 1) * 100 > GAP_PCT


def _row_at(table: dict[str, dict[str, dict[str, Any]]], dates: list[str], j: int, code: str) -> dict[str, Any] | None:
    if j < 0 or j >= len(dates):
        return None
    return table.get(dates[j], {}).get(code)


def trade_cost_pct(fee: float | None) -> float:
    """來回成本％：手續費 0.1425%×折數×2 ＋ 證交稅 0.3%；折數 0＝不扣。"""
    if not fee or fee <= 0:
        return 0.0
    return round(FEE_PCT * fee * 2 + TAX_PCT, 4)


def evaluate(row: dict[str, Any], dates: list[str], index: dict[str, int], table: dict[str, dict[str, dict[str, Any]]], tp: float, cost: float = 0.0) -> dict[str, Any]:
    """一筆進場的完整紀錄：參數欄位、進場價、黑K低、停利價、D+1 開高低收、各種出場（扣 cost％ 費用）、爆發力（不扣）。"""
    code = row["code"]
    entry, low_k = row["close"], row["low"]
    group, name = group_and_name(code)
    out: dict[str, Any] = {
        "code": code, "name": name, "group": row["group"] or group, "date": row["date"],
        "score": row["score"], "groupAvg": row["groupAvg"], "score2": row.get("score2"), "groupAvg2": row.get("groupAvg2"),
        "weekPct": row["weekPct"], "weekDate": row["weekDate"],
        "changePct": row["changePct"], "hits20": row["hits20"], "val5": row["val5"], "disposed": row["disposed"],
        "bias": row.get("bias"), "outDays": row.get("outDays"), "attention": bool(row.get("attention")), "inGroup": row.get("inGroup", True),
        "entry": entry, "lowK": low_k, "target": _r2(entry * (1 + tp / 100)),
        "next": None, "gap": False, "hitTp": None, "hitSl": None, "exits": None, "burst": None,
    }
    i = index[row["date"]]
    nxt = _row_at(table, dates, i + 1, code)
    if nxt is None or entry <= 0:
        return out
    if _gap(entry, nxt):
        out["gap"] = True
        return out
    out["next"] = {"date": nxt["date"], "open": nxt["open"], "high": nxt["high"], "low": nxt["low"], "close": nxt["close"]}
    x = exits(entry, low_k, (nxt["open"], nxt["high"], nxt["low"], nxt["close"]), tp)
    out["hitTp"], out["hitSl"] = x.pop("hitTp"), x.pop("hitSl")
    d2 = _row_at(table, dates, i + 2, code)
    d2 = None if _gap(entry, d2) else d2
    d3 = _row_at(table, dates, i + 3, code) if d2 else None
    d3 = None if _gap(entry, d3) else d3
    x["d2"] = _pct(entry, d2["close"]) if d2 else None
    if cost:
        x = {k: (_r2(v - cost) if v is not None else None) for k, v in x.items()}
    out["exits"] = x
    high2 = max(nxt["high"], d2["high"]) if d2 else None
    high3 = max(high2, d3["high"]) if d2 and d3 else None
    out["burst"] = {
        "max1": _pct(entry, nxt["high"]), "max2": _pct(entry, high2) if high2 else None, "max3": _pct(entry, high3) if high3 else None,
        "open1": _pct(entry, nxt["open"]), "close1": _pct(entry, nxt["close"]),
        "close2": _pct(entry, d2["close"]) if d2 else None, "close3": _pct(entry, d3["close"]) if d3 else None,
    }
    return out


# ------------------------------------------------------------------ 統計

def stats(values: list[float], amount: float) -> dict[str, Any]:
    """筆數、平均、中位數、勝率（>0）、總損益（每檔 amount 萬）、最差／最佳單筆。"""
    if not values:
        return {"count": 0, "avg": None, "median": None, "win": None, "wins": 0, "total": None, "worst": None, "best": None}
    wins = sum(1 for v in values if v > 0)
    return {"count": len(values), "avg": _r2(_mean(values)), "median": _r2(median(values)), "win": round(wins / len(values) * 100), "wins": wins,
            "total": round(sum(values) / 100 * amount, 1), "worst": min(values), "best": max(values)}


def _burst_stats(trades: list[dict[str, Any]], n: int) -> dict[str, Any]:
    key = f"max{n}"
    values = [t["burst"][key] for t in trades if t["burst"] and t["burst"].get(key) is not None]
    closes = [t["burst"][f"close{n}"] for t in trades if t["burst"] and t["burst"].get(f"close{n}") is not None]
    opens = [t["burst"]["open1"] for t in trades if t["burst"]] if n == 1 else []
    if not values:
        return {"samples": 0, "avg": None, "win": None, "ge5": None, "ge10": None, "best": None, "worst": None, "closeAvg": None, "openAvg": None}
    count = len(values)
    return {"samples": count, "avg": _r2(_mean(values)), "win": round(sum(1 for v in values if v > 0) / count * 100),
            "ge5": round(sum(1 for v in values if v >= 5) / count * 100), "ge10": round(sum(1 for v in values if v >= 10) / count * 100),
            "best": max(values), "worst": min(values), "closeAvg": _r2(_mean(closes)) if closes else None,
            "openAvg": _r2(_mean(opens)) if opens else None}


def backtest(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """前端要的整包：參數、出場方式績效、累積曲線、爆發力、今日名單、每日明細。"""
    p = normalize_params(params)
    dates, table = load_rows(p["days"] or MAX_DAYS_PARAM)
    if not dates:
        return {"status": "empty", "reason": "還沒有黑龍名單（收盤後會自動建）", "params": p, "rules": RULES, "collector": collector_status()}
    latest = dates[-1]
    index = {d: i for i, d in enumerate(dates)}
    window = dates[-p["days"]:] if p["days"] > 0 else list(dates)
    cost = trade_cost_pct(p["fee"])
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    cum = {m: 0.0 for m in CURVE_METHODS}
    today: dict[str, Any] = {"date": latest, "count": 0, "rows": []}
    for d in window:
        rows = [evaluate(r, dates, index, table, p["tp"], cost) for r in select_rows(table.get(d, {}), p)]
        with_next = [x for x in rows if x["next"]]
        avg = {m: _r2(_mean([x["exits"][m] for x in with_next])) if with_next else None for m in CURVE_METHODS}
        daily.append({"date": d, "count": len(rows), "withNext": len(with_next), "avg": avg, "rows": rows})
        if d == latest:
            today = {"date": d, "count": len(rows), "rows": rows}
        if with_next:
            for m in CURVE_METHODS:
                cum[m] += avg[m] or 0.0
            point: dict[str, Any] = {"date": d, "count": len(with_next), **avg, "cum": {m: _r2(cum[m]) for m in CURVE_METHODS}}
            for n in (1, 2, 3):
                values = [x["burst"][f"max{n}"] for x in with_next if x["burst"] and x["burst"].get(f"max{n}") is not None]
                point[f"max{n}"] = _r2(_mean(values)) if values else None
                point[f"n{n}"] = len(values)
            curve.append(point)
            trades.extend(with_next)
    daily.reverse()
    methods = []
    for key in METHODS:
        values = [x["exits"][key] for x in trades if x["exits"] and x["exits"].get(key) is not None]
        label = METHOD_LABELS[key] + (f"（+{p['tp']:g}%）" if key in ("tp", "both") else "")
        methods.append({"key": key, "label": label, **stats(values, p["amt"])})
    hit_tp = [x for x in trades if x["hitTp"]]
    hit_sl = [x for x in trades if x["hitSl"]]
    back_days = len(curve)
    result = {
        "status": "ok", "date": latest, "dates": dates, "params": p, "cost": cost, "attentionSince": attention_log_since(),
        "window": {"days": p["days"], "from": window[0], "to": latest, "backtestDays": back_days},
        "stats": {
            "days": back_days, "trades": len(trades), "perDay": _r2(len(trades) / back_days) if back_days else None,
            "hitTp": round(len(hit_tp) / len(trades) * 100) if trades else None,
            "hitSl": round(len(hit_sl) / len(trades) * 100) if trades else None,
            "mine": p["mine"], "methods": methods,
        },
        "burst": {f"d{n}": _burst_stats(trades, n) for n in (1, 2, 3)},
        "curve": curve,
        "today": today,
        "noon": _noon(p, latest),
        "daily": daily,
        "rules": RULES,
        "collector": collector_status(),
    }
    return result


def _noon(p: dict[str, Any], latest: str) -> dict[str, Any] | None:
    """今天 12:00 的暫定名單（比收盤整表新才給）；延遲匯入：heilong_noon 會 import 這個模組。"""
    try:
        from heilong_noon import noon_section

        return noon_section(p, latest)
    except Exception:  # noqa: BLE001
        logger.exception("heilong noon section failed")
        return None
