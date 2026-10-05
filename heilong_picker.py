"""創高黑選股（2026-10-04 使用者：照莊爸 App「創高黑」做一個選股程式，跟創高黑龍分開放）。

四個頁面共用一份收盤後整理好的特徵表（heilong_daily：還原後的日K、內定均線分數、創高天數、5 日均成交值…），
載進記憶體（一檔一串陣列）後依參數即時算：

① 條件池：最近 within 個交易日內創過 hi 日新高（創高天數 ≥ hi）、今天均線分數 ≥ score、5 日均成交值 ≥ val 億、
   市值（最新已發行股數 × 收盤）≥ mcap 億、近 sdays 天有 stimes 次以上單日漲幅 > spct%（sdays＝0 不看）、
   處置中不抓（exdispo）；範圍全市場或族群表內。
   每日新進＝今天符合、前一個交易日不符合；上榜日＝這次連續符合的第一天。
② 每週名單：每週最後一個交易日收盤，從條件池依 wsort 排序取前 weekN 檔，當下週名單（使用者可以自己改）；
   名單外的條件池股票是週選備選。
③ 收黑進場：收黑＝收盤 < 開盤（black=range 再加當天漲跌 −10%～+3%）、收黑那天均線分數 ≥ bscore（0＝不要求）。
   名單（或可換股的候選）收黑就在收盤買第 1 批，每檔分 lots 批；持有中再收黑加一批（addon）；
   收盤跌破 rma 日線減 1 批（reduce，跌破那天算一次，站回去才會再算）；收盤漲到均價 +tpr% 減 1 批（停利減碼，一次）；
   收盤跌破 xma 日線全部出；週五（一週最後一個交易日）賠錢的持股全部出（fri，週五汰弱）。
④ 資金：每檔 per 萬，同時最多 maxpos 檔（額度＝per×maxpos）；滿檔時可以換掉最弱且賠錢的一檔（full），
   跟從每日新進／週選備選補進來一樣算一次換股，一週最多 swaps 次；出場後同一週不再買回（norebuy）。
   費用＝手續費 0.1425%×折數（fee）買賣各一次＋證交稅 0.3%；fee＝0 不扣。

實績＝每個每日新進當一個訊號，追蹤期（track 天，從隔天起算）內第一次收黑就收盤進場，比較各種出場方式；
今天＝照模組規則從某一週開始模擬每天進出（同額度買 0050 當比較）。全部是試算，不是買賣建議。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from array import array
from bisect import bisect_right
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from database import get_connection, initialize_database
from heilong_backtest import FEE_PCT, TAX_PCT, _schema as _heilong_schema
from price_adjust import adjust_bars, load_events

logger = logging.getLogger(__name__)
TW_TZ = timezone(timedelta(hours=8))
NAN = float("nan")
BENCHMARK = "0050"
WEEKDAY = "一二三四五六日"

DEFAULTS: dict[str, Any] = {
    # ① 條件池
    "hi": 20, "within": 6, "score": 10, "sdays": 10, "spct": 8.0, "stimes": 2, "val": 1.0, "mcap": 50.0, "track": 10,
    "exdispo": True, "scope": "market",
    # ② 每週名單與換股
    "weekN": 6, "wsort": "score", "swaps": 2, "swapFrom": "daily", "fri": True, "full": True, "norebuy": True,
    # ③ 收黑進場・加減碼・出場
    "black": "oc", "bscore": 0, "lots": 3, "addon": True, "reduce": True, "rma": 5, "xma": 10, "tpr": 0,
    # ④ 資金與開始日
    "per": 50.0, "maxpos": 6, "start": 4, "fee": 0.28,
    # 實績
    "range": 60, "ptp": 8.0,
}
CHOICES: dict[str, tuple] = {
    "hi": (20, 40, 60, 120, 240), "within": (1, 3, 6, 11), "sdays": (0, 5, 10, 20, 40, 60), "spct": (5.0, 7.0, 8.0, 9.5),
    "stimes": (1, 2, 3, 5), "val": (0.3, 1.0, 3.0, 5.0), "mcap": (20.0, 50.0, 100.0, 300.0), "track": (5, 10, 20),
    "weekN": (3, 4, 5, 6, 8, 10), "wsort": ("score", "val", "hilen", "mcap"), "swaps": (0, 1, 2, 3, 5),
    "swapFrom": ("daily", "week"), "black": ("oc", "range"), "bscore": (0, 8, 9, 10), "lots": (1, 2, 3, 4, 5),
    "rma": (3, 5, 10, 20), "xma": (5, 10, 20), "tpr": (0, 10, 15, 20), "start": (0, 1, 2, 4, 8, -1),
    "range": (20, 40, 60, 120, 0), "ptp": (5.0, 8.0, 10.0, 15.0), "scope": ("market", "groups"),
}
INT_KEYS = ("hi", "within", "score", "sdays", "stimes", "track", "weekN", "swaps", "bscore", "lots", "rma", "xma", "tpr", "maxpos", "start", "range")
FLOAT_KEYS = ("spct", "val", "mcap", "per", "fee", "ptp")
BOOL_KEYS = ("exdispo", "fri", "full", "norebuy", "addon", "reduce")
STR_KEYS = ("scope", "wsort", "swapFrom", "black")
SORT_LABELS = {"score": "均線分", "val": "成交值", "hilen": "創高天數", "mcap": "市值"}
SOURCE_LABELS = {"list": "名單", "daily": "每日新進", "week": "週選備選"}

RULES = [
    "條件池：最近 N 天內創過「創幾日新高」（今天收盤是近 X 日最高收盤，平手也算），今天內定均線分數（滿分 15）達門檻、"
    "5 日平均成交值（收盤×量估算）、市值（最新已發行股數×收盤）都夠，近幾天內有幾次單日漲幅超過門檻；處置中的股票不抓。",
    "每日新進＝今天剛符合條件池、前一個交易日還不符合；從隔天起追蹤幾個交易日，收黑才進場，沒等到收黑就移除。上榜日＝這次連續符合條件池的第一天。",
    "每週名單＝每週最後一個交易日收盤，從條件池照你選的排序取前幾檔，當下一週的名單；名單外的條件池股票是週選備選。",
    "收黑＝收盤低於開盤（也可以再加當天漲跌在 −10%～+3% 之間）。名單裡的股票收黑那天收盤買第 1 批，每檔資金分幾批；持有中再收黑加一批。",
    "收盤跌破減碼線（短均線）減 1 批，跌破那天算一次、站回去以後再跌破才會再減；收盤跌破出場線全部賣；停利減碼＝收盤漲到均價 +X% 減 1 批（一次）。"
    "週五汰弱＝一週最後一個交易日收盤，還在賠錢的持股全部賣。",
    "同時最多幾檔滿了就不買；勾「滿檔換弱」時，會賣掉最弱而且賠錢的一檔換新的進來，跟從每日新進（或週選備選）補進來一樣算一次換股，一週最多換股幾次。",
    "價格用分割減資還原後的日K（除權息不還原）；均線分數同創高黑龍的內定算法。買賣都用收盤價，零股照算；費用＝手續費 0.1425%×折數（買賣各一次）＋證交稅 0.3%。",
    "實績：每個每日新進當一個訊號，追蹤期內第一次收黑就收盤進場。抱 N 天＝進場後第 N 個交易日收盤賣；跌破 N 日線＝進場後第一天收盤低於 N 日均線就收盤賣，"
    "還沒跌破的用最新收盤算；停利／破黑K低用日K：開盤就超過→開盤價，盤中碰到→停利價或黑K低，同一天兩個都碰到保守算停損；抱到今天＝最新收盤（未實現）。",
    "模擬帳戶照上面的規則從選的那一週開始每天跑一次，同額度買 0050 抱著當比較。條件是你設的，全部是試算，不是買賣建議。",
]


# ------------------------------------------------------------------ 參數

def _flag(value: Any) -> bool:
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def normalize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    p = dict(DEFAULTS)
    for key in INT_KEYS:
        if raw.get(key) not in (None, ""):
            try:
                p[key] = int(float(raw[key]))
            except (TypeError, ValueError):
                raise ValueError(f"{key} 要是整數") from None
    for key in FLOAT_KEYS:
        if raw.get(key) not in (None, ""):
            try:
                p[key] = float(raw[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} 要是數字") from None
    for key in BOOL_KEYS:
        if raw.get(key) not in (None, ""):
            p[key] = _flag(raw[key])
    for key in STR_KEYS:
        if raw.get(key) not in (None, ""):
            p[key] = str(raw[key]).strip()
    if not 2 <= p["hi"] <= 500:
        raise ValueError("創幾日新高要在 2～500")
    if not 1 <= p["within"] <= 30:
        raise ValueError("創高後幾天內要在 1～30")
    if not 0 <= p["score"] <= 15:
        raise ValueError("均線分數要在 0～15")
    if not 0 <= p["sdays"] <= 120 or not 1 <= p["stimes"] <= 20 or not 0 < p["spct"] <= 10:
        raise ValueError("強勢股條件超出範圍")
    if p["val"] < 0 or p["mcap"] < 0:
        raise ValueError("成交值、市值不能是負的")
    if not 1 <= p["track"] <= 60:
        raise ValueError("追蹤天數要在 1～60")
    if not 1 <= p["weekN"] <= 20 or not 0 <= p["swaps"] <= 20:
        raise ValueError("每週檔數、換股次數超出範圍")
    for key in ("wsort", "swapFrom", "black", "scope"):
        if p[key] not in CHOICES[key]:
            raise ValueError(f"{key} 只能是 " + "／".join(CHOICES[key]))
    if not 0 <= p["bscore"] <= 15 or not 1 <= p["lots"] <= 5:
        raise ValueError("收黑均線分數或批數超出範圍")
    if p["rma"] not in (3, 5, 10, 20) or p["xma"] not in (5, 10, 20, 60):
        raise ValueError("減碼線只能 3／5／10／20 日線，出場線只能 5／10／20／60 日線")
    if not 0 <= p["tpr"] <= 100:
        raise ValueError("停利減碼要在 0～100%")
    if not 1 <= p["per"] <= 10000 or not 1 <= p["maxpos"] <= 30:
        raise ValueError("每檔資金或同時最多幾檔超出範圍")
    if p["start"] < -1 or p["start"] > 104:
        raise ValueError("開始週數超出範圍")
    if not 0 <= p["fee"] <= 1:
        raise ValueError("手續費折數要在 0～1（0＝不扣費用）")
    if p["range"] < 0 or not 0 < p["ptp"] <= 50:
        raise ValueError("實績天數或停利超出範圍")
    return p


def fee_rates(fee: float) -> tuple[float, float]:
    """(買進費率, 賣出費率)；fee＝手續費折數，0＝不扣。"""
    if not fee or fee <= 0:
        return 0.0, 0.0
    commission = FEE_PCT * fee / 100
    return commission, commission + TAX_PCT / 100


def round_trip_pct(fee: float) -> float:
    buy, sell = fee_rates(fee)
    return round((buy + sell) * 100, 4)


# ------------------------------------------------------------------ 特徵（記憶體）

class Series:
    """一檔股票對齊全表交易日的陣列（沒成交的日子是 NaN／-1）。"""

    __slots__ = ("code", "name", "group", "in_group", "o", "h", "l", "c", "v", "chg", "score", "hilen", "val5", "disposed", "week",
                 "shares", "_valid", "_ma", "_big")

    def __init__(self, code: str, size: int) -> None:
        self.code = code
        self.name = code
        self.group = ""
        self.in_group = False
        self.o = array("d", [NAN]) * size
        self.h = array("d", [NAN]) * size
        self.l = array("d", [NAN]) * size
        self.c = array("d", [NAN]) * size
        self.v = array("l", [0]) * size
        self.chg = array("f", [NAN]) * size
        self.score = array("b", [-1]) * size
        self.hilen = array("h", [0]) * size
        self.val5 = array("f", [NAN]) * size
        self.disposed = bytearray(size)
        self.week = array("f", [NAN]) * size
        self.shares = 0
        self._valid: list[int] | None = None
        self._ma: dict[int, array] = {}
        self._big: dict[float, list[int]] = {}

    def valid(self) -> list[int]:
        if self._valid is None:
            self._valid = [i for i, x in enumerate(self.c) if x == x]
        return self._valid

    def ma(self, n: int) -> array:
        """n 日均線（只算這檔有成交的日子，最近 n 根；不足 n 根是 NaN）。"""
        cached = self._ma.get(n)
        if cached is not None:
            return cached
        out = array("d", [NAN]) * len(self.c)
        valid = self.valid()
        total = 0.0
        for k, i in enumerate(valid):
            total += self.c[i]
            if k >= n:
                total -= self.c[valid[k - n]]
            if k >= n - 1:
                out[i] = total / n
        self._ma[n] = out
        return out

    def big_prefix(self, pct: float) -> list[int]:
        """big[i+1]－big[j]＝第 j～i 天單日漲幅 > pct 的天數。"""
        cached = self._big.get(pct)
        if cached is not None:
            return cached
        big = [0]
        for x in self.chg:
            big.append(big[-1] + (1 if x == x and x > pct else 0))
        self._big[pct] = big
        return big

    def mcap(self, i: int) -> float | None:
        c = self.c[i]
        return self.shares * c / 1e8 if self.shares and c == c else None

    def is_black(self, i: int, p: dict[str, Any]) -> bool:
        o, c = self.o[i], self.c[i]
        if not (o == o and c == c and c < o):
            return False
        if p["black"] == "range":
            ch = self.chg[i]
            if not (ch == ch and -10.0 <= ch <= 3.0):
                return False
        return not (p["bscore"] and self.score[i] < p["bscore"])


class Panel:
    def __init__(self, key: str, dates: list[str]) -> None:
        self.key = key
        self.dates = dates
        self.index = {d: i for i, d in enumerate(dates)}
        self.series: dict[str, Series] = {}
        self.bench = array("d", [NAN]) * len(dates)
        self.weeks: list[list[int]] = []        # 每週的交易日 index（舊到新）
        self.week_of: list[int] = []            # 每個交易日在第幾週
        self.pool_cache: OrderedDict[tuple, "Pool"] = OrderedDict()
        self.lock = threading.Lock()
        self.loaded_at = datetime.now(TW_TZ).isoformat(timespec="seconds")

    def build_weeks(self) -> None:
        current: tuple[int, int] | None = None
        for i, d in enumerate(self.dates):
            iso = date.fromisoformat(d).isocalendar()
            key = (iso[0], iso[1])
            if key != current:
                self.weeks.append([])
                current = key
            self.weeks[-1].append(i)
            self.week_of.append(len(self.weeks) - 1)


_panel: Panel | None = None
_panel_lock = threading.Lock()


_fp_cache: dict[str, Any] = {"at": 0.0, "value": None}
FINGERPRINT_TTL = 20.0     # 秒：每個請求都去數 50 萬列太浪費；表一天才整理一次


def _fingerprint() -> str:
    now = time.monotonic()
    if _fp_cache["value"] is not None and now - _fp_cache["at"] < FINGERPRINT_TTL:
        return _fp_cache["value"]
    initialize_database()
    with get_connection() as connection:
        _heilong_schema(connection)
        row = connection.execute("SELECT MAX(trade_date) AS d, COUNT(*) AS n FROM heilong_daily").fetchone()
        meta = connection.execute("SELECT value FROM heilong_meta WHERE key = 'adjust_version'").fetchone()
    value = f"{row['d']}:{row['n']}:{meta['value'] if meta else ''}"
    _fp_cache.update({"at": now, "value": value})
    return value


def load_panel(force: bool = False) -> Panel:
    """收盤後整理好的特徵表載進記憶體；表沒變（最新日期、筆數、還原版本都一樣）就用上次的。"""
    global _panel
    if force:
        _fp_cache["value"] = None
    key = _fingerprint()
    with _panel_lock:
        if _panel is not None and _panel.key == key and not force:
            return _panel
        panel = _build_panel(key)
        _panel = panel
        return panel


def _build_panel(key: str) -> Panel:
    with get_connection() as connection:
        connection.row_factory = None
        dates = [r[0] for r in connection.execute("SELECT DISTINCT trade_date FROM heilong_daily ORDER BY trade_date")]
        panel = Panel(key, dates)
        size = len(dates)
        index = panel.index
        current: Series | None = None
        for (d, code, o, h, l, c, v, chg, s2, val5, hl, disp, ing, grp, wk) in connection.execute(
            """SELECT trade_date, stock_code, open, high, low, close, volume, change_pct, score2, val5, hi_len, disposed, in_group,
                      group_name, week_pct FROM heilong_daily ORDER BY stock_code"""
        ):
            if current is None or current.code != code:
                current = panel.series.get(code) or Series(code, size)
                panel.series[code] = current
            i = index[d]
            current.o[i], current.h[i], current.l[i], current.c[i] = o, h, l, c
            current.v[i] = int(v or 0)
            if chg is not None:
                current.chg[i] = chg
            if s2 is not None:
                current.score[i] = int(s2)
            if hl is not None:
                current.hilen[i] = min(int(hl), 32000)
            if val5 is not None:
                current.val5[i] = val5
            current.disposed[i] = 1 if disp else 0
            if wk is not None:
                current.week[i] = wk
            if ing:
                current.in_group = True
            if grp:
                current.group = grp
        names = {str(r[0]).strip().upper(): str(r[1] or "").strip() for r in connection.execute("SELECT stock_code, stock_name FROM stocks")}
        shares = {str(r[0]).strip().upper(): int(r[1] or 0) for r in connection.execute("SELECT stock_code, shares FROM stock_shares")} \
            if connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_shares'").fetchone() else {}
        bench_rows = connection.execute(
            "SELECT substr(bar_time, 1, 10), open, high, low, close, volume FROM bars_1d WHERE stock_code = ? AND substr(bar_time, 1, 10) >= ? ORDER BY bar_time",
            (BENCHMARK, dates[0] if dates else "9999"),
        ).fetchall()
    for code, s in panel.series.items():
        s.name = names.get(code) or code
        s.shares = shares.get(code, 0)
    bench = adjust_bars([tuple(r) for r in bench_rows], load_events([BENCHMARK]).get(BENCHMARK))
    for d, _o, _h, _l, c, _v in bench:
        i = panel.index.get(d)
        if i is not None:
            panel.bench[i] = c
    panel.build_weeks()
    logger.info("創高黑選股：特徵載入 %s 檔 × %s 天", len(panel.series), len(dates))
    return panel


# ------------------------------------------------------------------ 條件池

POOL_KEYS = ("hi", "within", "score", "sdays", "spct", "stimes", "val", "mcap", "exdispo", "scope")


class Pool:
    """一組條件池參數的結果：{代號: 每天在不在（1／0）}，以及每天的每日新進。"""

    def __init__(self, flags: dict[str, bytearray], size: int) -> None:
        self.flags = flags
        self.new_by_day: list[list[str]] = [[] for _ in range(size)]
        for code, f in sorted(flags.items()):
            for i in range(1, size):
                if f[i] and not f[i - 1]:
                    self.new_by_day[i].append(code)

    def members(self, i: int) -> list[str]:
        return sorted(code for code, f in self.flags.items() if f[i])

    def entrants(self, i: int) -> list[str]:
        """第 i 天的每日新進：今天在、前一個交易日不在（第一天沒有前一天，不算）。"""
        return list(self.new_by_day[i]) if 0 < i < len(self.new_by_day) else []

    def tracked(self, day: int, track: int) -> list[str]:
        """第 day 天收盤要看的每日新進：上榜日在 day−track ～ day−1（上榜隔天起追蹤 track 個交易日）。"""
        out: set[str] = set()
        for k in range(max(1, day - track), min(day, len(self.new_by_day))):
            out.update(self.new_by_day[k])
        return sorted(out)

    def in_pool(self, code: str, i: int) -> bool:
        f = self.flags.get(code)
        return bool(f and f[i])


def pool_flags(panel: Panel, p: dict[str, Any]) -> Pool:
    """條件池；同一組條件池參數算一次就記著（最多 8 組）。"""
    key = tuple(p[k] for k in POOL_KEYS)
    with panel.lock:
        cached = panel.pool_cache.get(key)
        if cached is not None:
            panel.pool_cache.move_to_end(key)
            return cached
    size = len(panel.dates)
    out: dict[str, bytearray] = {}
    hi, within, score_min, val_min, mcap_min = p["hi"], p["within"], p["score"], p["val"], p["mcap"]
    sdays, spct, stimes = p["sdays"], p["spct"], p["stimes"]
    for code, s in panel.series.items():
        if p["scope"] == "groups" and not s.in_group:
            continue
        flags = bytearray(size)
        last_high = -10 ** 9
        big = s.big_prefix(spct) if sdays else None
        any_in = False
        for i in range(size):
            c = s.c[i]
            if c != c:
                continue
            if s.hilen[i] >= hi:
                last_high = i
            if i - last_high >= within:
                continue
            if s.score[i] < score_min:
                continue
            v5 = s.val5[i]
            if not (v5 == v5 and v5 >= val_min):
                continue
            if mcap_min > 0 and (not s.shares or s.shares * c / 1e8 < mcap_min):
                continue
            if p["exdispo"] and s.disposed[i]:
                continue
            if big is not None and big[i + 1] - big[max(0, i - sdays + 1)] < stimes:
                continue
            flags[i] = 1
            any_in = True
        if any_in:
            out[code] = flags
    pool = Pool(out, size)
    with panel.lock:
        panel.pool_cache[key] = pool
        while len(panel.pool_cache) > 8:
            panel.pool_cache.popitem(last=False)
    return pool


def run_start(flags: bytearray, i: int) -> int:
    """這次連續在條件池的第一天（index）。"""
    j = i
    while j > 0 and flags[j - 1]:
        j -= 1
    return j


def sort_key(s: Series, i: int, how: str) -> tuple:
    """排序用（大的在前）：均線分（同分比成交值）、成交值、創高天數、市值。"""
    score = s.score[i]
    val = s.val5[i] if s.val5[i] == s.val5[i] else 0.0
    if how == "val":
        primary: float = val
    elif how == "hilen":
        primary = s.hilen[i]
    elif how == "mcap":
        primary = s.mcap(i) or 0.0
    else:
        primary = score
    return (-primary, -score, -val, s.code)


def week_list(panel: Panel, pool: Pool, p: dict[str, Any], week: int,
              overrides: dict[str, list[str]] | None = None) -> tuple[list[str], list[str], int | None]:
    """第 week 週的名單：前一週最後一個交易日收盤，條件池照排序取前 weekN 檔（使用者改過那一週就用使用者的）。
    回 (名單, 週選備選, 選股那天 index)。week＝len(weeks) 表示「下一週」（今天收盤選）。"""
    if week <= 0 or week > len(panel.weeks):
        return [], [], None
    pick_day = panel.weeks[week - 1][-1]
    ranked = sorted(pool.members(pick_day), key=lambda c: sort_key(panel.series[c], pick_day, p["wsort"]))
    start_date = week_start_date(panel, week)
    manual = (overrides or {}).get(start_date)
    if manual is not None:
        chosen = [c for c in manual if c in panel.series][:10]
    else:
        chosen = ranked[:p["weekN"]]
    alternates = [c for c in ranked if c not in chosen]
    return chosen, alternates, pick_day


def week_start_date(panel: Panel, week: int) -> str:
    """第 week 週的星期一（下一週＝最後一週的星期一 +7 天）。"""
    if week < len(panel.weeks):
        first = date.fromisoformat(panel.dates[panel.weeks[week][0]])
    else:
        first = date.fromisoformat(panel.dates[panel.weeks[-1][0]]) + timedelta(days=7 * (week - len(panel.weeks) + 1))
    return (first - timedelta(days=first.weekday())).isoformat()


def week_label(panel: Panel, week: int) -> dict[str, Any]:
    monday = date.fromisoformat(week_start_date(panel, week))
    friday = monday + timedelta(days=4)
    return {"week": week, "start": monday.isoformat(), "end": friday.isoformat(), "next": week >= len(panel.weeks),
            "label": f"{monday.month}/{monday.day}~{friday.month}/{friday.day}"}


# ------------------------------------------------------------------ 一檔的進出（實績與模擬共用）

def _r2(value: float | None) -> float | None:
    return None if value is None or value != value else round(value, 2)


def _pct(entry: float, price: float) -> float:
    return (price / entry - 1) * 100


def first_black(s: Series, start: int, end: int, p: dict[str, Any]) -> int | None:
    for i in range(start, min(end, len(s.c) - 1) + 1):
        if s.is_black(i, p):
            return i
    return None


def _after(s: Series, e: int) -> list[int]:
    valid = s.valid()
    return valid[bisect_right(valid, e):]


def exit_hold(s: Series, e: int, n: int) -> tuple[int, float] | None:
    days = _after(s, e)
    if len(days) < n:
        return None
    i = days[n - 1]
    return i, s.c[i]


def exit_ma_break(s: Series, e: int, n: int) -> tuple[int, float, bool]:
    """進場後第一天收盤跌破 n 日線就賣；還沒跌破用最新收盤（第三個值＝還抱著）。"""
    ma = s.ma(n)
    days = _after(s, e)
    for i in days:
        if ma[i] == ma[i] and s.c[i] < ma[i]:
            return i, s.c[i], False
    last = days[-1] if days else e
    return last, s.c[last], True


def exit_tp_sl(s: Series, e: int, tp_pct: float) -> tuple[int, float, bool]:
    entry, low_k = s.c[e], s.l[e]
    target = entry * (1 + tp_pct / 100)
    days = _after(s, e)
    for i in days:
        o, h, l = s.o[i], s.h[i], s.l[i]
        if o >= target:
            return i, o, False
        if o < low_k:
            return i, o, False
        if l < low_k:
            return i, low_k, False        # 同一天兩個都碰到也保守算停損
        if h >= target:
            return i, target, False
    last = days[-1] if days else e
    return last, s.c[last], True


class Position:
    """模組的一檔持股：分批買賣、減碼／停利／出場狀態。"""

    __slots__ = ("code", "entry", "source", "lots", "shares", "cost", "bought", "sold_value", "realized", "armed", "tp_done",
                 "batches", "max_batches", "last_action")

    def __init__(self, code: str, entry: int, source: str) -> None:
        self.code = code
        self.entry = entry
        self.source = source
        self.shares: list[tuple[int, float]] = []      # 每批（股數, 買價）
        self.cost = 0.0          # 還抱著的成本（含手續費）
        self.bought = 0.0        # 累計買進金額（含手續費）
        self.sold_value = 0.0    # 累計賣出淨額
        self.realized = 0.0
        self.armed = True
        self.tp_done = False
        self.batches = 0
        self.max_batches = 0
        self.last_action: str | None = None

    def held_shares(self) -> int:
        return sum(n for n, _ in self.shares)

    def avg_price(self) -> float:
        total = self.held_shares()
        return sum(n * px for n, px in self.shares) / total if total else 0.0

    def value(self, price: float) -> float:
        return self.held_shares() * price

    def buy(self, price: float, amount: float, buy_rate: float) -> int:
        shares = int(amount // price) if price > 0 else 0
        if shares <= 0:
            return 0
        cash = shares * price * (1 + buy_rate)
        self.shares.append((shares, price))
        self.cost += cash
        self.bought += cash
        self.batches += 1
        self.max_batches = max(self.max_batches, self.batches)
        return shares

    def sell(self, price: float, sell_rate: float, batches: int | None = None) -> tuple[int, float]:
        """賣 batches 批（None＝全部），先買的先賣；回（股數, 這次實現損益）。"""
        if not self.shares:
            return 0, 0.0
        count = len(self.shares) if batches is None else min(batches, len(self.shares))
        sold = self.shares[:count]
        self.shares = self.shares[count:]
        n = sum(x for x, _ in sold)
        # 成本按股數比例拿掉（含當初的手續費）
        held_before = n + self.held_shares()
        fraction = n / held_before if held_before else 1.0
        cost_removed = self.cost * fraction
        self.cost -= cost_removed
        proceeds = n * price * (1 - sell_rate)
        pnl = proceeds - cost_removed
        self.sold_value += proceeds
        self.realized += pnl
        self.batches -= count
        return n, pnl


def step_position(pos: Position, s: Series, i: int, p: dict[str, Any], rates: tuple[float, float], batch_amount: float,
                  *, allow_add: bool = True) -> list[dict[str, Any]]:
    """第 i 天收盤照規則處理一檔持股（進場當天不處理）：出場線→減碼線→停利減碼；沒賣才看再收黑加碼。回這天的動作。"""
    c = s.c[i]
    if c != c or not pos.shares:
        return []
    actions: list[dict[str, Any]] = []
    buy_rate, sell_rate = rates
    ma_x = s.ma(p["xma"])[i]
    ma_r = s.ma(p["rma"])[i]
    if ma_x == ma_x and c < ma_x:
        n, pnl = pos.sell(c, sell_rate)
        actions.append({"type": "exit", "code": pos.code, "price": c, "shares": n, "pnl": pnl, "reason": f"跌破{p['xma']}日線・全部賣"})
        return actions
    if p["reduce"] and ma_r == ma_r:
        if c < ma_r:
            if pos.armed:
                n, pnl = pos.sell(c, sell_rate, 1)
                pos.armed = False
                actions.append({"type": "exit" if not pos.shares else "reduce", "code": pos.code, "price": c, "shares": n, "pnl": pnl,
                                "reason": f"跌破{p['rma']}日線・減1批" + ("（最後一批）" if not pos.shares else "")})
                return actions
        else:
            pos.armed = True
    if p["tpr"] and not pos.tp_done and pos.shares and c >= pos.avg_price() * (1 + p["tpr"] / 100):
        n, pnl = pos.sell(c, sell_rate, 1)
        pos.tp_done = True
        actions.append({"type": "exit" if not pos.shares else "reduce", "code": pos.code, "price": c, "shares": n, "pnl": pnl,
                        "reason": f"停利+{p['tpr']}%・減1批"})
        return actions
    if allow_add and p["addon"] and pos.batches < p["lots"] and s.is_black(i, p):
        n = pos.buy(c, batch_amount, buy_rate)
        if n:
            actions.append({"type": "add", "code": pos.code, "price": c, "shares": n, "batch": pos.batches,
                            "reason": f"再收黑・加到第{pos.batches}批"})
    return actions


def simulate_signal(s: Series, e: int, p: dict[str, Any], rates: tuple[float, float], batch_amount: float) -> dict[str, Any]:
    """「你的模組」：一個訊號單獨照模組規則跑（不管資金上限）。"""
    pos = Position(s.code, e, "signal")
    pos.buy(s.c[e], batch_amount, rates[0])
    pos.armed = not (s.ma(p["rma"])[e] == s.ma(p["rma"])[e] and s.c[e] < s.ma(p["rma"])[e])
    last = e
    for i in _after(s, e):
        last = i
        step_position(pos, s, i, p, rates, batch_amount)
        if not pos.shares:
            break
    open_value = pos.value(s.c[last]) * (1 - rates[1]) if pos.shares else 0.0
    pnl = pos.realized + (open_value - pos.cost if pos.shares else 0.0)
    return {"exit": last, "pnl": pnl, "pct": pnl / pos.bought * 100 if pos.bought else 0.0, "open": bool(pos.shares),
            "batches": pos.max_batches}


# ------------------------------------------------------------------ 實績

EXIT_METHODS = (
    ("d1", "隔天收盤出"), ("h3", "抱 3 天"), ("h5", "抱 5 天"), ("h10", "抱 10 天"), ("h20", "抱 20 天"),
    ("m5", "跌破 5 日線收盤出"), ("m10", "跌破 10 日線收盤出"), ("m20", "跌破 20 日線收盤出"),
    ("tp", "停利/破黑K低"), ("mine", "★你的模組"), ("now", "抱到今天"),
)


def _stats(trades: list[dict[str, Any]], amount: float) -> dict[str, Any]:
    """筆數、平均、中位數、勝率、總損益（萬；每筆 amount 萬，你的模組用實際分批損益）、最差、最好、平均持有天數。"""
    if not trades:
        return {"count": 0, "avg": None, "median": None, "win": None, "wins": 0, "total": None, "worst": None, "best": None, "days": None, "open": 0}
    values = [t["pct"] for t in trades]
    wins = sum(1 for v in values if v > 0)
    total = sum(t["pnl"] if "pnl" in t else t["pct"] / 100 * amount * 10000 for t in trades) / 10000
    return {"count": len(values), "avg": _r2(sum(values) / len(values)), "median": _r2(median(values)),
            "win": round(wins / len(values) * 100), "wins": wins, "total": round(total, 1),
            "worst": _r2(min(values)), "best": _r2(max(values)), "days": round(sum(t["days"] for t in trades) / len(trades), 1),
            "open": sum(1 for t in trades if t.get("open"))}


def perf(panel: Panel, p: dict[str, Any]) -> dict[str, Any]:
    """每個每日新進當一個訊號：追蹤期內第一次收黑收盤進場，各種出場方式的成績。"""
    pool = pool_flags(panel, p)
    size = len(panel.dates)
    first = max(1, size - p["range"]) if p["range"] else 1
    rates = fee_rates(p["fee"])
    cost = (rates[0] + rates[1]) * 100
    batch_amount = p["per"] * 10000 / p["lots"]
    by_method: dict[str, list[dict[str, Any]]] = {k: [] for k, _ in EXIT_METHODS}
    signals = entered = pending = 0
    recent: list[dict[str, Any]] = []
    for i in range(first, size):
        for code in pool.entrants(i):
            s = panel.series[code]
            signals += 1
            e = first_black(s, i + 1, i + p["track"], p)
            if e is None:
                if i + p["track"] > size - 1:
                    pending += 1      # 追蹤期還沒走完
                continue
            entered += 1
            entry = s.c[e]
            trade: dict[str, Any] = {"code": code, "name": s.name, "signal": panel.dates[i], "entry": panel.dates[e], "price": _r2(entry)}

            def add(key: str, j: int, price: float, held: bool = False) -> None:
                pct = _pct(entry, price) - cost
                by_method[key].append({"pct": pct, "days": j - e, "open": held})
                trade[key] = _r2(pct)

            for n, key in ((1, "d1"), (3, "h3"), (5, "h5"), (10, "h10"), (20, "h20")):
                x = exit_hold(s, e, n)
                if x:
                    add(key, x[0], x[1])
            for n, key in ((5, "m5"), (10, "m10"), (20, "m20")):
                j, price, held = exit_ma_break(s, e, n)
                if j > e:
                    add(key, j, price, held)
            j, price, held = exit_tp_sl(s, e, p["ptp"])
            if j > e:
                add("tp", j, price, held)
            mine = simulate_signal(s, e, p, rates, batch_amount)
            if mine["exit"] > e:
                by_method["mine"].append({"pct": mine["pct"], "pnl": mine["pnl"], "days": mine["exit"] - e, "open": mine["open"]})
                trade["mine"] = _r2(mine["pct"])
            last = s.valid()[-1]
            if last > e:
                add("now", last, s.c[last], True)
            recent.append(trade)
    methods = []
    for key, label in EXIT_METHODS:
        tag = label
        if key == "tp":
            tag = f"停利+{p['ptp']:g}%/破黑K低"
        elif key == "mine":
            tag = f"★你的模組（{p['lots']}批・" + (f"破{p['rma']}日線減・" if p["reduce"] else "") + f"破{p['xma']}日線出）"
        methods.append({"key": key, "label": tag, **_stats(by_method[key], p["per"])})
    real = [m for m in methods if m["key"] != "now" and m["count"]]
    best = max(real, key=lambda m: m["avg"]) if real else None
    days = size - first
    return {
        "from": panel.dates[first], "to": panel.dates[-1], "days": days, "signals": signals,
        "perDay": _r2(signals / days) if days else None, "entered": entered, "pending": pending,
        "entryRate": round(entered / signals * 100) if signals else None,
        "best": {"key": best["key"], "label": best["label"], "avg": best["avg"]} if best else None,
        "cost": round(cost, 4), "methods": methods, "recent": recent[-40:][::-1],
    }


# ------------------------------------------------------------------ 選股頁

def _row(panel: Panel, code: str, i: int, pool: Pool | None = None, **extra: Any) -> dict[str, Any]:
    s = panel.series[code]
    c = s.c[i]
    ch = s.chg[i]
    out = {
        "code": code, "name": s.name, "group": s.group, "inGroup": s.in_group,
        "close": _r2(c), "open": _r2(s.o[i]), "changePct": _r2(ch) if ch == ch else None,
        "black": bool(s.o[i] == s.o[i] and c == c and c < s.o[i]),
        "score": s.score[i] if s.score[i] >= 0 else None, "hiLen": s.hilen[i] or None,
        "val5": _r2(s.val5[i]), "mcap": _r2(s.mcap(i)), "weekPct": _r2(s.week[i]),
    }
    if pool is not None and pool.in_pool(code, i):
        start = run_start(pool.flags[code], i)
        out["since"] = panel.dates[start]
        out["sinceDays"] = i - start + 1
    out.update(extra)
    return out


def _is_week_end(panel: Panel, i: int) -> bool:
    """那天是不是那一週最後一個交易日（最新一天：星期五才算）。"""
    w = panel.week_of[i]
    if i != panel.weeks[w][-1]:
        return False
    return w < len(panel.weeks) - 1 or date.fromisoformat(panel.dates[i]).weekday() == 4


def picks(panel: Panel, p: dict[str, Any], day: int, week: int | None, overrides: dict[str, list[str]] | None) -> dict[str, Any]:
    pool = pool_flags(panel, p)
    size = len(panel.dates)
    in_pool = pool.members(day)
    new_today = pool.entrants(day)
    this_week = panel.week_of[day]
    week_now, _alt, _pick = week_list(panel, pool, p, this_week, overrides)
    tracked = pool.tracked(day, p["track"])
    black_today = sorted({c for c in set(week_now) | set(tracked) if panel.series[c].is_black(day, p)})
    # 每週名單分頁：預設看下一週（那天是一週最後一個交易日，收盤就選下週名單），不然看這一週
    default_week = this_week + 1 if _is_week_end(panel, day) else this_week
    target_week = default_week if week is None else week
    target_week = max(1, min(target_week, len(panel.weeks)))
    chosen, alternates, pick_day = week_list(panel, pool, p, target_week, overrides)
    week_rows = []
    if pick_day is not None:
        for rank, code in enumerate(chosen + alternates, start=1):
            week_rows.append(_row(panel, code, pick_day, pool, rank=rank, listed=code in chosen))
    new_rows = []
    entered = 0
    after5: list[float] = []
    for code in new_today:
        s = panel.series[code]
        e = first_black(s, day + 1, day + p["track"], p)
        if e is not None:
            entered += 1
        later = exit_hold(s, day, 5)
        if later:
            after5.append(_pct(s.c[day], later[1]))
        new_rows.append(_row(panel, code, day, pool, entryDate=panel.dates[e] if e is not None else None,
                             after5=_r2(_pct(s.c[day], later[1])) if later else None))
    new_rows.sort(key=lambda r: (-(r["score"] or 0), -(r["val5"] or 0)))
    pool_rows = sorted((_row(panel, c, day, pool) for c in in_pool), key=lambda r: (-(r["score"] or 0), -(r["val5"] or 0)))
    track_end = day + p["track"]
    return {
        "funnel": {"pool": len(in_pool), "week": len(week_now), "daily": len(new_today), "black": len(black_today)},
        "blackToday": [_row(panel, c, day, pool, source="list" if c in week_now else "daily") for c in black_today],
        "week": {**week_label(panel, target_week), "pickDate": panel.dates[pick_day] if pick_day is not None else None,
                 "count": len(chosen), "rows": week_rows, "thisWeek": this_week, "defaultWeek": default_week,
                 "weeks": len(panel.weeks), "minWeek": 1, "maxWeek": len(panel.weeks)},
        "daily": {"date": panel.dates[day], "count": len(new_today), "rows": new_rows, "entered": entered,
                  "after5Avg": _r2(sum(after5) / len(after5)) if after5 else None,
                  "after5Up": round(sum(1 for x in after5 if x > 0) / len(after5) * 100) if after5 else None,
                  "after5Count": len(after5), "trackUntil": panel.dates[track_end] if track_end < size else None,
                  "maxUse": round(len(new_today) * p["per"], 1)},
        "pool": {"date": panel.dates[day], "count": len(in_pool), "rows": pool_rows},
    }


# ------------------------------------------------------------------ 模擬帳戶

def _parse_overrides(text: str | None) -> dict[str, list[str]]:
    """lists＝『2026-10-05:3037,1727;2026-10-12:2409』→ {週一: [代號]}（使用者在選股頁打勾改過的名單）。"""
    out: dict[str, list[str]] = {}
    for part in str(text or "").split(";"):
        if ":" not in part:
            continue
        day, codes = part.split(":", 1)
        day = day.strip()
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        out[day] = [c.strip().upper() for c in codes.split(",") if c.strip()][:10]
    return out


def _closed_row(panel: Panel, pos: Position, i: int) -> dict[str, Any]:
    return {"code": pos.code, "name": panel.series[pos.code].name, "entry": panel.dates[pos.entry], "exit": panel.dates[i],
            "pnl": round(pos.realized / 10000, 2), "pct": _r2(pos.realized / pos.bought * 100) if pos.bought else None,
            "source": pos.source, "batches": pos.max_batches}


def simulate(panel: Panel, p: dict[str, Any], end: int, overrides: dict[str, list[str]] | None = None,
             stars: list[str] | None = None) -> dict[str, Any]:
    """照模組規則從開始那一週跑到 end 那天收盤：先處理持股（出場線、減碼線、停利減碼、週五汰弱），
    再買今天收黑的候選（名單優先；滿檔換弱、從每日新進或週選備選補都算換股），最後再收黑的持股加一批。"""
    pool = pool_flags(panel, p)
    size = len(panel.dates)
    stars_set = set(stars or [])
    rates = fee_rates(p["fee"])
    batch_amount = p["per"] * 10000 / p["lots"]
    quota = p["per"] * p["maxpos"] * 10000
    end_week = panel.week_of[end]
    if p["start"] == 0:
        start_week = end_week + 1                 # 下週起（實盤）：還沒開始
    elif p["start"] < 0:
        start_week = 1                             # 全部：第一週沒有上一週可以選名單，從第二週開始
    else:
        start_week = max(1, end_week - p["start"] + 1)
    started = start_week <= end_week
    positions: dict[str, Position] = {}
    closed: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    realized = 0.0
    week_codes: list[str] = []
    alternates: list[str] = []
    swaps_used = 0
    exited_week: set[str] = set()
    today_actions: list[dict[str, Any]] = []
    missed: dict[str, tuple[str, str]] = {}      # 最後一天收黑但沒買：代號 →（來源, 原因）
    bench_start: float | None = None
    start_day = panel.weeks[start_week][0] if started else None

    def worst_loser(i: int) -> tuple[float, str] | None:
        worst: tuple[float, str] | None = None
        for code, pos in positions.items():
            if pos.entry == i or not pos.cost:
                continue
            c = panel.series[code].c[i]
            if c != c:
                continue
            ret = (pos.value(c) * (1 - rates[1]) - pos.cost) / pos.cost
            if ret < 0 and (worst is None or ret < worst[0]):
                worst = (ret, code)
        return worst

    def swap_note() -> str:
        return "模組設定不換股" if p["swaps"] <= 0 else f"本週換股 {swaps_used}/{p['swaps']} 次用完"

    def full_reason(bought_now: list[str]) -> str:
        """滿檔買不進來的原因（2026-10-05 使用者：沒進的每檔寫清楚實際原因）。"""
        head = f"滿檔 {len(positions)}/{p['maxpos']}"
        if bought_now:
            head = "空位給了排前面的" + "、".join(panel.series[c].name for c in bought_now) + "，" + head
        if not p["full"]:
            return head + "，模組沒開滿檔換股"
        if p["swaps"] <= 0 or swaps_used >= p["swaps"]:
            return head + "，" + swap_note()
        return head + ("，其他持股都沒賠錢" if bought_now else "，持股都沒賠錢") + "，沒有可以換掉的"

    if started:
        for i in range(start_day, end + 1):
            w = panel.week_of[i]
            if i == panel.weeks[w][0]:
                week_codes, alternates, _pick = week_list(panel, pool, p, w, overrides)
                swaps_used = 0
                exited_week = set()
            if bench_start is None and panel.bench[i] == panel.bench[i]:
                bench_start = panel.bench[i]
            actions: list[dict[str, Any]] = []
            week_end = _is_week_end(panel, i)
            # 1) 持股：出場線、減碼線、停利減碼；週五還在賠錢的全部出
            for code in list(positions):
                pos = positions[code]
                if pos.entry == i:
                    continue
                s = panel.series[code]
                acts = step_position(pos, s, i, p, rates, batch_amount, allow_add=False)
                c = s.c[i]
                if not acts and p["fri"] and week_end and c == c and pos.shares and pos.value(c) * (1 - rates[1]) < pos.cost:
                    n, pnl = pos.sell(c, rates[1])
                    acts.append({"type": "exit", "code": code, "price": c, "shares": n, "pnl": pnl, "reason": "週五汰弱・全部賣"})
                for a in acts:
                    realized += a.get("pnl", 0.0)
                actions.extend(acts)
                if not pos.shares:
                    closed.append(_closed_row(panel, pos, i))
                    exited_week.add(code)
                    del positions[code]
            touched = {a["code"] for a in actions}
            # 2) 今天收黑的候選
            swap_pool = pool.tracked(i, p["track"]) if p["swapFrom"] == "daily" else alternates
            candidates: list[tuple[tuple, str, str]] = []
            seen: set[str] = set()
            for source, codes in (("list", week_codes), (p["swapFrom"], swap_pool)):
                for code in codes:
                    if code in seen or code in positions or code in touched or code not in panel.series:
                        continue
                    if p["norebuy"] and code in exited_week:
                        continue
                    seen.add(code)
                    s = panel.series[code]
                    if not s.is_black(i, p):
                        continue
                    rank = week_codes.index(code) if source == "list" else 0
                    candidates.append(((0 if code in stars_set else 1, 0 if source == "list" else 1, rank, sort_key(s, i, p["wsort"])), code, source))
            candidates.sort()
            bought_now: list[str] = []
            for _prio, code, source in candidates:
                s = panel.series[code]
                is_swap = source != "list"
                if int(batch_amount // s.c[i]) <= 0:      # 一批的錢連 1 股都買不起：先擋，不要為了它把持股換掉
                    if i == end:
                        missed[code] = (source, f"一批 {batch_amount / 10000:.1f} 萬買不到 1 股")
                    continue
                if len(positions) >= p["maxpos"]:
                    worst = worst_loser(i) if p["full"] and swaps_used < p["swaps"] else None
                    if worst is None:
                        if i == end:
                            missed[code] = (source, full_reason(bought_now))
                        continue
                    other = positions.pop(worst[1])
                    so = panel.series[worst[1]]
                    n, pnl = other.sell(so.c[i], rates[1])
                    realized += pnl
                    actions.append({"type": "exit", "code": worst[1], "price": so.c[i], "shares": n, "pnl": pnl,
                                    "reason": f"滿檔換弱・換成 {s.name}"})
                    closed.append(_closed_row(panel, other, i))
                    exited_week.add(worst[1])
                    is_swap = True
                elif is_swap and swaps_used >= p["swaps"]:
                    if i == end:
                        missed[code] = (source, f"{SOURCE_LABELS[source]}買進要算換股，{swap_note()}")
                    continue
                pos = Position(code, i, source)
                n = pos.buy(s.c[i], batch_amount, rates[0])
                if not n:
                    continue
                ma_r = s.ma(p["rma"])[i]
                pos.armed = not (ma_r == ma_r and s.c[i] < ma_r)
                positions[code] = pos
                bought_now.append(code)
                if is_swap:
                    swaps_used += 1
                actions.append({"type": "buy", "code": code, "price": s.c[i], "shares": n, "batch": 1,
                                "reason": ("名單收黑" if source == "list" else SOURCE_LABELS[source] + "收黑") + "・買第1批" + ("（換股）" if is_swap else "")})
            # 3) 再收黑加一批（今天有賣過或才買的不加）
            if p["addon"]:
                for code, pos in positions.items():
                    if pos.entry == i or code in touched or pos.batches >= p["lots"]:
                        continue
                    s = panel.series[code]
                    if s.is_black(i, p):
                        n = pos.buy(s.c[i], batch_amount, rates[0])
                        if n:
                            actions.append({"type": "add", "code": code, "price": s.c[i], "shares": n, "batch": pos.batches,
                                            "reason": f"再收黑・加到第{pos.batches}批"})
            unrealized = 0.0
            for code, pos in positions.items():
                c = panel.series[code].c[i]
                if c == c:
                    unrealized += pos.value(c) * (1 - rates[1]) - pos.cost
            bench = (panel.bench[i] / bench_start - 1) * quota if bench_start and panel.bench[i] == panel.bench[i] else None
            equity.append({"date": panel.dates[i], "pnl": round((realized + unrealized) / 10000, 2),
                           "bench": round(bench / 10000, 2) if bench is not None else None, "holding": len(positions)})
            for a in actions:
                a["name"] = panel.series[a["code"]].name
                a["amount"] = round(a["shares"] * a["price"] / 10000, 1)
                if "pnl" in a:
                    a["pnl"] = round(a["pnl"] / 10000, 2)
                a["price"] = _r2(a["price"])
            if actions:
                log.append({"date": panel.dates[i], "week": w, "actions": actions})
            if i == end:
                today_actions = actions

    i = end
    lookback = max(p["xma"], p["rma"]) - 1
    holding_rows = []
    invested = market = unrealized = 0.0
    for code, pos in sorted(positions.items(), key=lambda kv: kv[1].entry):
        s = panel.series[code]
        valid = [k for k in s.valid() if k <= i]
        c = s.c[valid[-1]] if valid else 0.0
        value = pos.value(c)
        pnl = value * (1 - rates[1]) - pos.cost
        invested += pos.cost
        market += value
        unrealized += pnl
        holding_rows.append({**_row(panel, code, i, pool), "batches": pos.batches, "lots": p["lots"], "entryDate": panel.dates[pos.entry],
                             "source": pos.source, "avg": _r2(pos.avg_price()), "shares": pos.held_shares(), "value": round(value / 10000, 2),
                             "cost": round(pos.cost / 10000, 2), "pnl": round(pnl / 10000, 2), "pct": _r2(pnl / pos.cost * 100) if pos.cost else None,
                             "exitLine": _r2(s.ma(p["xma"])[i]), "reduceLine": _r2(s.ma(p["rma"])[i]), "armed": pos.armed,
                             "lastCloses": [_r2(s.c[k]) for k in valid[-lookback:]], "star": code in stars_set})
    bench_now = panel.bench[i] if panel.bench[i] == panel.bench[i] else None
    bench_pnl = (bench_now / bench_start - 1) * quota if bench_start and bench_now else None
    # 明天收盤要看的：（一週最後一天收盤看下週名單）名單還沒買的＋可換股來源
    next_week = panel.week_of[i] + 1 if _is_week_end(panel, i) else panel.week_of[i]
    if next_week != panel.week_of[i] or not started:
        watch_list, watch_alt, _ = week_list(panel, pool, p, next_week, overrides)
        exited_watch: set[str] = set()
    else:
        watch_list, watch_alt, exited_watch = week_codes, alternates, exited_week
    swap_source = pool.tracked(i + 1, p["track"]) if p["swapFrom"] == "daily" else watch_alt
    watch_rows = []
    seen: set[str] = set()
    for source, codes in (("list", watch_list), (p["swapFrom"], swap_source)):
        for code in codes:
            if code in seen or code in positions or code not in panel.series:
                continue
            if p["norebuy"] and code in exited_watch:
                continue
            seen.add(code)
            s = panel.series[code]
            valid = [k for k in s.valid() if k <= i]
            watch_rows.append(_row(panel, code, i, pool, source=source, star=code in stars_set,
                                   lastCloses=[_r2(s.c[k]) for k in valid[-lookback:]]))
    tracked_now = pool.tracked(i, p["track"])
    black_all = sorted({c for c in set(week_codes or watch_list) | set(tracked_now) if panel.series[c].is_black(i, p)})
    bought_today = {a["code"] for a in today_actions if a["type"] == "buy"}
    return {
        "started": started, "startDate": panel.dates[start_day] if started else None,
        "startWeek": start_week, "weekNo": (panel.week_of[i] - start_week + 1) if started else 0,
        "swapsUsed": swaps_used, "swaps": p["swaps"], "nextWeek": week_label(panel, next_week),
        "account": {
            "quota": round(quota / 10000, 1), "invested": round(invested / 10000, 1), "cash": round((quota - invested) / 10000, 1),
            "market": round(market / 10000, 1), "pnl": round((realized + unrealized) / 10000, 1),
            "realized": round(realized / 10000, 1), "unrealized": round(unrealized / 10000, 1),
            "bench": round(bench_pnl / 10000, 1) if bench_pnl is not None else None, "benchCode": BENCHMARK,
        },
        "holdings": holding_rows, "full": len(positions) >= p["maxpos"], "maxpos": p["maxpos"],
        "watch": watch_rows, "today": today_actions,
        "missed": [_row(panel, c, i, pool, source=src, reason=why) for c, (src, why) in missed.items() if c not in bought_today],
        "blackAll": [_row(panel, c, i, pool, source="list" if c in week_codes else "daily") for c in black_all],
        "log": log[::-1][:40], "closed": closed[::-1][:60], "equity": equity,
    }


# ------------------------------------------------------------------ 對外

def _day_index(panel: Panel, day: str | None) -> int:
    if not panel.dates:
        raise ValueError("還沒有資料")
    if not day:
        return len(panel.dates) - 1
    return max(0, bisect_right(panel.dates, str(day)[:10]) - 1)


def data_info(panel: Panel) -> dict[str, Any]:
    return {"from": panel.dates[0] if panel.dates else None, "to": panel.dates[-1] if panel.dates else None, "days": len(panel.dates),
            "stocks": len(panel.series), "loadedAt": panel.loaded_at, "weeks": len(panel.weeks)}


def _attach_revenue(rows: list[dict[str, Any]]) -> None:
    """觀察中、持股加上最新月營收年增（基本面排序用）。"""
    codes = sorted({r["code"] for r in rows})
    if not codes:
        return
    try:
        from fundamentals_daily import latest_revenue

        revenue = latest_revenue(codes)
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        rev = revenue.get(r["code"])
        r["revYoy"] = _r2(rev.get("yoy")) if rev and rev.get("yoy") is not None else None
        r["revYm"] = rev.get("ym") if rev else None


def payload(view: str, raw: dict[str, Any] | None = None, *, day: str | None = None, week: int | None = None,
            lists: str | None = None, stars: str | None = None) -> dict[str, Any]:
    """前端要的：view＝picks（選股）／perf（實績）／today（今天）／rules（模組說明）。"""
    p = normalize_params(raw)
    if view not in ("picks", "perf", "today", "rules"):
        raise ValueError("view 只能是 picks／perf／today／rules")
    panel = load_panel()
    base: dict[str, Any] = {
        "status": "ok", "view": view, "params": p, "defaults": DEFAULTS, "choices": {k: list(v) for k, v in CHOICES.items()},
        "rules": RULES, "data": data_info(panel), "cost": round_trip_pct(p["fee"]), "sortLabels": SORT_LABELS, "sourceLabels": SOURCE_LABELS,
    }
    if len(panel.dates) < 2:
        return {**base, "status": "empty", "reason": "特徵表還沒整理好（收盤後會自動整理；日K補到三年後會重算）"}
    i = _day_index(panel, day)
    base["date"] = panel.dates[i]
    base["weekday"] = WEEKDAY[date.fromisoformat(panel.dates[i]).weekday()]
    base["dates"] = panel.dates[-250:]
    base["thisWeek"] = week_label(panel, panel.week_of[i])
    if view == "rules":
        return base
    overrides = _parse_overrides(lists)
    star_list = [c.strip().upper() for c in str(stars or "").split(",") if c.strip()][:20]
    if view == "picks":
        return {**base, **picks(panel, p, i, week, overrides)}
    if view == "perf":
        return {**base, "perf": perf(panel, p)}
    sim = simulate(panel, p, i, overrides, star_list)
    _attach_revenue(sim["watch"] + sim["holdings"])
    return {**base, "sim": sim}


def pick_one(panel: Panel, codes: list[str], day: int, how: str, *, seed: int | None = None) -> str | None:
    """系統挑一檔（均線分最高，同分比成交值）／擲骰子（隨機一檔）。"""
    candidates = [c for c in codes if c in panel.series]
    if not candidates:
        return None
    if how == "dice":
        return random.Random(seed).choice(candidates)
    return sorted(candidates, key=lambda c: sort_key(panel.series[c], day, "score"))[0]


def warm() -> None:
    """收盤後整表完先載進記憶體，第一個打開的人不用等（表剛換過：不看快取的指紋）。"""
    try:
        load_panel(force=True)
    except Exception:  # noqa: BLE001
        logger.exception("picker warm failed")
