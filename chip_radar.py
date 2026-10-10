"""籌碼暴增雷達（2026-10-04 使用者：照莊爸 zhuang.tw/radar「籌碼暴增雷達」做，放在下方導覽列「盤後籌碼排行」）。

看的是「大戶手上的股票變多還是變少」：集保股權分散表（每週五結算，tdcc_weekly，fundamentals_daily 從 tw-groups 鏡像匯入）。

籌碼%（跟莊爸一樣；2026-10-04 用集保 6/5～10/2 的資料逐一比對截圖：買超榜、賣超榜、個股九週卡片、連續增排行
全部吻合到小數第二位）：
  x ＝（這週 400 張以上大戶（第 12～15 級）持股股數 − 上週）÷ 這週總股數（第 17 級合計）× 100，取兩位小數
  籌碼% ＝ 3 × √x（x > 0，大戶變多）；x ≤ 0（大戶變少）照原樣。兩種都取兩位小數。
  （分母用這週的總股數：增資那週，例如環球晶 10/02 增資 5 萬張，算出來才對得上。）

畫面上的各區塊：
- 本週籌碼暴增榜：買超榜（籌碼% ≥ 4）、賣超榜（≤ −1.5）；連兩週都在同一邊的榜上打 ⭐；可以切最近 8 週。
- 上榜累積榜：每週買超榜前十名記一筆；視窗內（全部／近 6 週／近 4 週，跟莊爸的選單一樣）擠進前十的次數、近 4 週次數、平均籌碼%、
  分數（＝那幾週籌碼% 加總＝次數 × 平均）、最佳名次。「全部」跟莊爸一樣從 06/18 那週算起（他寫「全部 17 週
  06/18～10/08」，2026-10-10），之後每週多一週，最多一年。
- 族群排名比較：族群裡籌碼% 最高的 5 檔平均（莊爸的石英 +3.46% ＝ 前 5 檔平均，對得上），前十族群；「千元」是價格帶，
  算進前十但不列（莊爸：千元是價格帶不列，所以他常常只列 9 族）；金融股、功率半導體他沒有，不排。
  （2026-10-10 照他的族群清單補齊族群表後，9/24、10/02、10/08 的前十跟他一模一樣。）
- 連續增排行：六週內至少五週增（可漏一週）、連三週都增；都用窗口平均排序（同分照沒四捨五入的 3√x 平均，
  莊爸的聯傑／穩懋都是 3.01、精誠／天二…的先後這樣才對得上），各取前 20。
- 熱門股：本週前十名＋前五大族群的第一名，附九週軌跡。
- 個股查詢：九週軌跡（前十名附名次）、同族群當週排名、三大法人（每週加總、近 5 日，佔成交量 %）。
每列最後的數字＝均線分數（官網那套 15 分，heilong_daily.score2，最近一個交易日）。

股本變動：那週總股數變了（增資、減資、轉換公司債），變動 ≥1% 名單上標「股本 ±x%」。
上一週集保沒有這檔（減資、停止過戶那週集保不出資料，例如方土霖 9/30～10/07 減資換股，10/02 那週查無資料），
改跟更早一週比（莊爸 10/08 的賣超榜有方土霖，只能是跟 9/24 比），名單上標「比 9/24」。

增資、減資都照算（跟莊爸一樣：環球晶 10/02 增資 10.5% 算出 +9.53、奇偶 9/18 減資 15% 算出 +23.1、方土霖 10/08 減資 10%
跟 9/24 比 −8.6，他都照列）。

範圍（2026-10-10）：莊爸只算他追蹤的股票——他的族群表，加上一些族群外的股票（他的週報寫「不在族群表」）。範圍在他的
伺服器端看不到，用他 9/18、9/24、10/02、10/08 四週的籌碼週報（買超全部、賣超前 10 名或全部）跟 10/08 的連續增排行學出來：
我們的族群表（不含股期標的、金融股）＋ ZHUANG_EXTRA − ZHUANG_SKIP。四週的買超、賣超他列的每一檔我們都有、數字一樣；
照這個範圍，我們每週多 1～2 檔（他後來才開始追蹤的股票，例如宏齊 9/18、台亞 10/02）。範圍外的股票不進任何排行，
名單下面另外列「不在範圍內」的（買超、賣超各自），個股查詢照查。以後他名單上出現新的族群外股票，加進 ZHUANG_EXTRA。
連續增排行另外用 ZHUANG_STREAK_EXTRA／ZHUANG_STREAK_SKIP（他有幾檔只出現在連續增、或週榜有但連續增沒有）。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from database import get_connection, initialize_database
from fundamentals_daily import _schema as _fundamentals_schema, tdcc_dates
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS

logger = logging.getLogger("hanstock.chip_radar")

BIG_LEVELS = (12, 13, 14, 15)     # 400 張以上
TOTAL_LEVEL = 17                  # 合計
BUY_MIN = 4.0                     # 買超榜門檻（籌碼%）
SELL_MAX = -1.5                   # 賣超榜門檻
TOP_N = 10                        # 累積榜只算每週前十名（名次也只給到第 10）
GROUP_TOP = 5                     # 族群分數＝族群裡最高的 5 檔平均
GROUP_SHOW = 10
STREAK_LONG = (6, 5)              # 六週內至少五週增
STREAK_SHORT = 3                  # 連三週增
STREAK_TOP = 20
LIST_WEEKS = 9                    # 本週榜可以切幾週（莊爸：本週＋往前 8 週）
CARD_WEEKS = 9                    # 個股卡片的軌跡週數
WINDOW_MAX = 16                   # 累積榜最多回看幾週
WINDOWS = (6, 4)                  # 累積榜「全部」以外的選項（莊爸：全部／近 6 週／近 4 週）
ALL_SINCE = "2026-06-18"          # 累積榜「全部」從這週算起（莊爸：全部 17 週 06/18～10/08）
ALL_MAX = 52                      # 「全部」最多一年
HOT_GROUPS = 5
RANK_SKIP_GROUPS = ("金融股", "功率半導體")   # 族群排名不算（莊爸的族群表沒有：他的漢磊、穩懋只標矽晶圓、PA；功率半導體 9/24 算起來第一）
PRICE_BAND_GROUPS = ("千元",)                          # 算進前十、但不列（莊爸：千元是價格帶不列）
INST_WEEKS = 5
INST_DAYS = 5
CACHE_SECONDS = 60.0
MAX_WEEK_GAP_DAYS = 10            # 兩個集保結算日最多差幾天還算「上一週」（遇到連假會差 6～8 天）
MAX_SKIP_GAP_DAYS = 2 * MAX_WEEK_GAP_DAYS   # 上一週集保沒有這檔時，往前再找一週，最多差這麼多天
CAPITAL_TAG = 1.0                 # 這週總股數變動 ≥1%（增資、減資、轉換公司債）：名單上標「股本 ±x%」

# 莊爸的範圍（見最上面說明）：族群表以外、他名單上出現過的（9/18～10/08 籌碼週報＋10/08 雷達）
ZHUANG_EXTRA = frozenset({
    "1617", "1709", "2033", "2399", "2468", "2493", "3013", "3094", "3128", "3219",   # 榮星 和益 佳大 映泰 華經 揚博 晟銘電 聯傑 昇銳 倚強科
    "6485",                                                                           # 點序（10/02 不在族群表；我們族群表掛低軌衛星，雷達拿掉）
    "3265", "3311", "3356", "3518", "3543", "3581", "3701", "3706", "4527", "4543",   # 台星科 閎暉 奇偶 柏騰 州巧 博磊 大眾控 神達 方土霖 萬在
    "4551", "4566", "4927", "4939", "6133", "6138", "6168", "6179", "6205", "6214",   # 智伸科 時碩工業 泰鼎-KY 亞電 金橋 茂達 宏齊 亞通 詮欣 精誠
    "6226", "6227", "6228", "6257", "6517", "6532", "6584", "6603", "6667", "6669",   # 光鼎 茂綸 全譜 矽格 保勝光學 瑞耘 南俊國際 富強鑫 信紘科 緯穎
    "6691", "6933", "7777", "8103", "8150", "8155", "8210", "8431", "9958",           # 洋基工程 AMAX-KY 能率亞洲 瀚荃 南茂 博智 勤誠 匯鑽科 世紀鋼
})
ZHUANG_SKIP = frozenset({"2241"})               # 我們族群表有、他從來不列的（艾姆勒 10/08 +5.13）
ZHUANG_SKIP_GROUPS = ("金融股",)               # 整組不算（元大金 9/24 +5.5、群益證他都沒列）
ZHUANG_STREAK_EXTRA = frozenset({"2340", "3035", "3680", "6225"})   # 只出現在他 10/08 連續增排行（台亞 智原 家登 天瀚，週榜當時沒算）
ZHUANG_STREAK_SKIP = frozenset({"3543", "4927", "6691"})            # 週榜有、10/08 連續增排行沒有（州巧 泰鼎-KY 洋基工程）
# 莊爸週報卡片上的族群標籤（2026-10-10 使用者補 10/08、10/02、9/24、9/18 放大截圖，123 檔裡 118 檔一樣），只用在籌碼週報／雷達：
ZHUANG_GROUP_ADD = {"6226": "光電", "6168": "光電"}   # 他有掛、我們沒掛（光鼎 10/08 賣超、宏齊 10/02 整族一起動都標「光電」）
ZHUANG_GROUP_DROP = {            # 他沒掛的（點序 10/02「不在族群表」、東捷 9/18 只標設備股）
    "6485": ("低軌衛星",), "8064": ("玻璃基板", "扇形封裝"),
}
# 一檔掛好幾族時「第一個族群」（他的標籤寫在前面那個；整族一起動只算第一個族群）：
ZHUANG_PRIMARY = {
    "6442": "千元",       # 光聖「千元/矽光子」：10/08 矽光子整族只算前鼎、聯亞
    "6207": "玻璃基板",   # 雷科「玻璃基板/設備股」：10/02 設備股只算由田一檔
    "4916": "軍工",       # 事欣科「軍工/D電腦」
}


def chip_value(big_now: float, big_prev: float, total_now: float) -> tuple[float, float] | None:
    """(x, 籌碼%)；x＝大戶股數變化 ÷ 這週總股數 ×100（兩位小數），籌碼%＝x>0 時 3√x。"""
    if not total_now or total_now <= 0:
        return None
    x = round((big_now - big_prev) / total_now * 100, 2)
    chip = round(3 * math.sqrt(x), 2) if x > 0 else x
    return x, chip


def chip_one_decimal(x: float) -> float:
    """榜單上一位小數的籌碼%：用還沒取兩位的 3√x 直接取一位（莊爸的眾達 x＝4.20 → 6.148 → 6.1；
    先取兩位 6.15 再取一位會變 6.2）。"""
    return round(3 * math.sqrt(x), 1) if x > 0 else round(x, 1)


def _eligible(code: str) -> bool:
    """一般股票：四碼數字、不是 0 開頭（ETF）。"""
    return len(code) == 4 and code.isdigit() and not code.startswith("0")


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _short(day: str) -> str:
    return f"{day[5:7]}/{day[8:10]}" if len(day) >= 10 else day


# ------------------------------------------------------------------ 載入

def _load_tdcc(weeks: int) -> tuple[list[str], dict[str, dict[str, list[int]]]]:
    """最近 weeks 個集保結算日（新到舊）與 {代號: {日期: [大戶股數, 總股數]}}。"""
    dates = tdcc_dates(weeks)
    if not dates:
        return [], {}
    initialize_database()
    out: dict[str, dict[str, list[int]]] = {}
    pct: dict[tuple[str, str], float] = {}
    levels = ",".join(str(x) for x in (*BIG_LEVELS, TOTAL_LEVEL))
    with get_connection() as connection:
        _fundamentals_schema(connection)
        # +level：按日期走主鍵（只讀這幾週）；不加的話會改走 (級距, 代號, 日期) 索引、把 60 週都掃過
        rows = connection.execute(
            f"""SELECT data_date, stock_code, level, shares, pct FROM tdcc_weekly
                WHERE data_date IN ({','.join('?' for _ in dates)}) AND +level IN ({levels})""",
            tuple(dates),
        ).fetchall()
    for r in rows:
        code = str(r["stock_code"]).strip().upper()
        if not _eligible(code):
            continue
        day = str(r["data_date"])
        entry = out.setdefault(code, {}).setdefault(day, [0, 0])
        if int(r["level"]) == TOTAL_LEVEL:
            entry[1] = int(r["shares"])
        else:
            entry[0] += int(r["shares"])
            pct[(code, day)] = pct.get((code, day), 0.0) + float(r["pct"] or 0)
    # 舊鏡像只給族群股票留合計（第 17 級）：沒有的用大戶股數 ÷ 大戶持股比例回推（比例兩位小數，誤差約千分之一）
    for (code, day), p in pct.items():
        entry = out[code][day]
        if not entry[1] and p >= 1.0 and entry[0] > 0:
            entry[1] = round(entry[0] / (p / 100))
    return dates, out


def _names() -> dict[str, tuple[str, str | None]]:
    initialize_database()
    with get_connection() as connection:
        rows = connection.execute("SELECT stock_code, stock_name, market FROM stocks").fetchall()
    return {str(r["stock_code"]).strip().upper(): (str(r["stock_name"] or ""), r["market"]) for r in rows}


def _ma_scores() -> tuple[str | None, dict[str, int]]:
    """最近一個交易日的均線分數（官網那套，score2）。黑龍表還沒建好就是空的。"""
    initialize_database()
    try:
        with get_connection() as connection:
            row = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily").fetchone()
            day = row["d"] if row else None
            if not day:
                return None, {}
            rows = connection.execute("SELECT stock_code, score2 FROM heilong_daily WHERE trade_date = ?", (day,)).fetchall()
    except Exception:  # noqa: BLE001  （黑龍表不存在）
        return None, {}
    return str(day), {str(r["stock_code"]).upper(): int(r["score2"]) for r in rows if r["score2"] is not None}


def _groups() -> tuple[dict[str, list[str]], dict[str, str]]:
    """({族群: [代號…]}, {代號: 第一個族群})，不含股期標的那份清單；照莊爸的標籤加減（ZHUANG_GROUP_ADD／DROP），
    第一個族群照 ZHUANG_PRIMARY。"""
    members: dict[str, list[str]] = {}
    first: dict[str, str] = {}
    for name, stocks in STOCK_GROUPS.items():
        if name in SPECIAL_GROUP_NAMES:
            continue
        codes = []
        for code, _name in stocks:
            code = str(code).strip().upper()
            codes.append(code)
            first.setdefault(code, name)
        members[name] = codes
    for code, names in ZHUANG_GROUP_DROP.items():
        for name in names:
            if code in members.get(name, []):
                members[name] = [c for c in members[name] if c != code]
        rest = [n for n, codes in members.items() if code in codes]
        if rest:
            first[code] = rest[0]
        else:
            first.pop(code, None)
    for code, name in ZHUANG_GROUP_ADD.items():
        if code not in members.setdefault(name, []):
            members[name].append(code)
        first.setdefault(code, name)
    first.update(ZHUANG_PRIMARY)
    return members, first


# ------------------------------------------------------------------ 計算

class Radar:
    """一份算好的雷達（快取用）。chips[代號][日期] ＝ 籌碼%；dates 新到舊、都有上一週可以比。"""

    def __init__(self) -> None:
        raw_dates, tdcc = _load_tdcc(max(ALL_MAX, WINDOW_MAX + LIST_WEEKS) + 2)
        self.names = _names()
        self.score_date, self.scores = _ma_scores()
        self.group_members, self.group_of = _groups()
        grouped = {c for name, codes in self.group_members.items() if name not in ZHUANG_SKIP_GROUPS for c in codes}
        self.universe = (grouped | ZHUANG_EXTRA) - ZHUANG_SKIP
        self.streak_universe = (self.universe | ZHUANG_STREAK_EXTRA) - ZHUANG_STREAK_SKIP
        self.chips: dict[str, dict[str, float]] = {}
        self.xs: dict[str, dict[str, float]] = {}
        self.capital: dict[str, dict[str, float]] = {}      # 那週總股數變動 %（≥1% 才記）
        self.skipped: dict[str, dict[str, str]] = {}        # 上一週集保沒有這檔，改跟更早那週比：{代號: {這週: 比的那週}}
        # 只跟「上一週」比：中間缺了一週（鏡像漏抓）那週就不算，不然會變成兩週的變化
        pairs = [i for i in range(len(raw_dates) - 1) if _days_between(raw_dates[i + 1], raw_dates[i]) <= MAX_WEEK_GAP_DAYS]
        for code, per in tdcc.items():
            for i in pairs:
                now, prev = per.get(raw_dates[i]), per.get(raw_dates[i + 1])
                if now and not prev and i + 2 < len(raw_dates) and _days_between(raw_dates[i + 2], raw_dates[i]) <= MAX_SKIP_GAP_DAYS:
                    prev = per.get(raw_dates[i + 2])
                    if prev:
                        self.skipped.setdefault(code, {})[raw_dates[i]] = raw_dates[i + 2]
                if not now or not prev or not now[1]:
                    continue
                value = chip_value(now[0], prev[0], now[1])
                if value is None:
                    continue
                change = round((now[1] / prev[1] - 1) * 100, 1) if prev[1] else 0.0
                if abs(change) >= CAPITAL_TAG:
                    self.capital.setdefault(code, {})[raw_dates[i]] = change
                self.xs.setdefault(code, {})[raw_dates[i]] = value[0]
                self.chips.setdefault(code, {})[raw_dates[i]] = value[1]
        self.dates = [d for d in raw_dates[:-1] if any(d in per for per in self.chips.values())] if len(raw_dates) > 1 else []
        self.raw_dates = raw_dates
        self._lists: dict[tuple[str, bool], tuple[list[str], list[str]]] = {}

    # 名單 -------------------------------------------------------------
    def lists(self, day: str, outside: bool = False) -> tuple[list[str], list[str]]:
        """那週的買超榜、賣超榜（代號，照籌碼% 排好）；outside＝True 回範圍外、過門檻的那些。"""
        key = (day, outside)
        if key not in self._lists:
            values = [(code, per[day]) for code, per in self.chips.items() if day in per and (code in self.universe) != outside]
            buy = [c for c, v in sorted(values, key=lambda cv: (-cv[1], cv[0])) if v >= BUY_MIN]
            sell = [c for c, v in sorted(values, key=lambda cv: (cv[1], cv[0])) if v <= SELL_MAX]
            self._lists[key] = (buy, sell)
        return self._lists[key]

    def rank(self, code: str, day: str) -> int | None:
        buy, _ = self.lists(day)
        try:
            i = buy.index(code)
        except ValueError:
            return None
        return i + 1 if i < TOP_N else None

    def prev_date(self, day: str) -> str | None:
        i = self.dates.index(day) if day in self.dates else -1
        return self.dates[i + 1] if 0 <= i < len(self.dates) - 1 else None

    def info(self, code: str) -> dict[str, Any]:
        name, market = self.names.get(code, ("", None))
        return {"code": code, "name": name or code, "market": market, "group": self.group_of.get(code), "score": self.scores.get(code)}

    def week_lists(self, day: str) -> dict[str, Any]:
        buy, sell = self.lists(day)
        prev = self.prev_date(day)
        prev_buy, prev_sell = self.lists(prev) if prev else ([], [])
        prev_buy_set, prev_sell_set = set(prev_buy), set(prev_sell)

        def rows(codes: list[str], prev_set: set[str]) -> list[dict[str, Any]]:
            return [{**self.info(c), "chip": self.chips[c][day], "chip1": chip_one_decimal(self.xs[c][day]), "rank": i + 1,
                     "star": c in prev_set, "capital": (self.capital.get(c) or {}).get(day),
                     "vs": (self.skipped.get(c) or {}).get(day)} for i, c in enumerate(codes)]

        out_buy, out_sell = self.lists(day, outside=True)

        def brief(codes: list[str]) -> list[dict[str, Any]]:
            return [{"code": c, "name": self.info(c)["name"], "chip1": chip_one_decimal(self.xs[c][day])} for c in codes]

        return {"date": day, "label": _short(day), "buy": rows(buy, prev_buy_set), "sell": rows(sell, prev_sell_set),
                "outside": {"buy": brief(out_buy), "sell": brief(out_sell)}}

    def trail(self, code: str, weeks: int = CARD_WEEKS) -> list[dict[str, Any]]:
        per = self.chips.get(code) or {}
        cap = self.capital.get(code) or {}
        out = []
        for d in self.dates[:weeks]:
            row: dict[str, Any] = {"date": d, "chip": per.get(d), "rank": self.rank(code, d) if d in per else None}
            if d in cap:
                row["capital"] = cap[d]
            if d in (self.skipped.get(code) or {}):
                row["vs"] = self.skipped[code][d]
            out.append(row)
        return out

    # 族群 -------------------------------------------------------------
    def group_table(self, day: str) -> list[dict[str, Any]]:
        out = []
        for name, codes in self.group_members.items():
            if name in RANK_SKIP_GROUPS:
                continue
            members = sorted(((c, self.chips[c][day]) for c in dict.fromkeys(codes) if c in self.chips and day in self.chips[c]),
                             key=lambda cv: (-cv[1], cv[0]))
            if not members:
                continue
            top = [v for _, v in members[:GROUP_TOP]]
            out.append({"name": name, "avg": round(sum(top) / len(top), 2), "count": len(members),
                        "members": [{**self.info(c), "chip": v, "rank": self.rank(c, day)} for c, v in members]})
        out.sort(key=lambda g: (-g["avg"], g["name"]))
        return out

    def ranked_groups(self, day: str) -> list[dict[str, Any]]:
        """畫面上的族群排名：前十（含千元）再拿掉千元。"""
        return [g for g in self.group_table(day)[:GROUP_SHOW] if g["name"] not in PRICE_BAND_GROUPS]

    # 連續增 -----------------------------------------------------------
    def _raw_avg(self, code: str, days: list[str]) -> float:
        xs = [self.xs[code][d] for d in days]
        return sum(3 * math.sqrt(x) if x > 0 else x for x in xs) / len(xs)

    def streaks(self, day: str) -> dict[str, Any]:
        i = self.dates.index(day)
        long_span, long_need = STREAK_LONG
        long_dates = self.dates[i:i + long_span]
        short_dates = self.dates[i:i + STREAK_SHORT]
        six, three = [], []
        for code, per in self.chips.items():
            if code not in self.streak_universe:
                continue
            if len(long_dates) == long_span and all(d in per for d in long_dates):
                values = [per[d] for d in long_dates]
                ups = sum(1 for v in values if v > 0)
                if ups >= long_need:
                    six.append({**self.info(code), "ups": ups, "avg": round(sum(values) / long_span, 2), "_k": self._raw_avg(code, long_dates)})
            if len(short_dates) == STREAK_SHORT and all(d in per and per[d] > 0 for d in short_dates):
                three.append({**self.info(code), "avg": round(sum(per[d] for d in short_dates) / STREAK_SHORT, 2),
                              "_k": self._raw_avg(code, short_dates)})
        six.sort(key=lambda r: (-r["_k"], r["code"]))
        three.sort(key=lambda r: (-r["_k"], r["code"]))
        for r in six + three:
            del r["_k"]

        def span(ds: list[str]) -> dict[str, Any]:
            return {"from": ds[-1] if ds else None, "to": ds[0] if ds else None, "weeks": len(ds)}

        return {"six": {**span(long_dates), "need": long_need, "rows": six[:STREAK_TOP]},
                "three": {**span(short_dates), "rows": three[:STREAK_TOP]}}

    # 累積榜 -----------------------------------------------------------
    def cumulative(self, day: str) -> dict[str, Any]:
        """day 往前「全部」（ALL_SINCE 起，最多 ALL_MAX 週；不足 16 週時給 16 週），每週前十名；回每檔每週的
        [名次或 0, 籌碼%]（舊到新）＋ allWeeks（「全部」是最後幾週），前端照選的週數自己加總。"""
        i = self.dates.index(day)
        since = [d for d in self.dates[i:i + ALL_MAX] if d >= ALL_SINCE]
        window = list(reversed(self.dates[i:i + max(len(since), WINDOW_MAX)]))      # 舊到新
        tops = {d: self.lists(d)[0][:TOP_N] for d in window}
        codes = sorted({c for top in tops.values() for c in top})
        rows = []
        for code in codes:
            per = self.chips.get(code) or {}
            grid = [[(tops[d].index(code) + 1) if code in tops[d] else 0, per.get(d)] for d in window]
            rows.append({**self.info(code), "grid": grid})
        return {"dates": window, "rows": rows, "allWeeks": len(since), "allFrom": since[-1] if since else None}

    # 熱門股 -----------------------------------------------------------
    def hot(self, day: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
        buy, _ = self.lists(day)
        picks = list(buy[:TOP_N])
        leaders = []
        for g in groups[:HOT_GROUPS]:
            lead = g["members"][0]
            leaders.append({"group": g["name"], "avg": g["avg"], "code": lead["code"], "name": lead["name"], "chip": lead["chip"]})
            if lead["code"] not in picks:
                picks.append(lead["code"])
        cards = [{**self.info(c), "chip": self.chips[c][day], "rank": self.rank(c, day), "trail": self.trail(c)} for c in picks]
        return {"leaders": leaders, "cards": cards}


_cache: dict[str, Any] = {"at": 0.0, "key": None, "radar": None}
_cache_lock = threading.Lock()


def _fingerprint() -> str:
    initialize_database()
    with get_connection() as connection:
        _fundamentals_schema(connection)
        row = connection.execute("SELECT MAX(data_date) AS d, COUNT(*) AS n FROM tdcc_weekly").fetchone()
        try:
            h = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily").fetchone()
        except Exception:  # noqa: BLE001
            h = None
    return f"{row['d']}:{row['n']}:{h['d'] if h else ''}"


def load_radar(force: bool = False) -> Radar:
    with _cache_lock:
        now = time.monotonic()
        if not force and _cache["radar"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return _cache["radar"]
        key = _fingerprint()
        if not force and _cache["radar"] is not None and _cache["key"] == key:
            _cache["at"] = now
            return _cache["radar"]
        radar = Radar()
        _cache.update({"at": now, "key": key, "radar": radar})
        return radar


def rules() -> dict[str, Any]:
    return {
        "buyMin": BUY_MIN, "sellMax": SELL_MAX, "topN": TOP_N, "groupTop": GROUP_TOP, "groupShow": GROUP_SHOW,
        "capitalTag": CAPITAL_TAG, "skipGroups": list(ZHUANG_SKIP_GROUPS), "extraCount": len(ZHUANG_EXTRA),
        "streakLong": list(STREAK_LONG), "streakShort": STREAK_SHORT, "streakTop": STREAK_TOP,
        "windows": list(WINDOWS), "allSince": ALL_SINCE, "cardWeeks": CARD_WEEKS, "listWeeks": LIST_WEEKS,
        "formula": "x＝（這週 400 張以上大戶持股股數 − 上週）÷ 這週總股數 × 100；籌碼%＝3×√x（大戶增加）或 x（大戶減少）",
    }


def payload(week: str | None = None) -> dict[str, Any]:
    radar = load_radar()
    if not radar.dates:
        return {"status": "empty", "message": "還沒有集保週資料（至少要兩週才能比）", "dates": [], "rules": rules()}
    if week and week not in radar.dates:
        raise ValueError(f"沒有 {week} 這週的集保資料")
    day = week or radar.dates[0]
    groups = radar.ranked_groups(day)
    list_dates = radar.dates[:LIST_WEEKS]
    if day not in list_dates:
        list_dates = [day, *list_dates[:LIST_WEEKS - 1]]
    return {
        "status": "ok",
        "week": day,
        "prevWeek": radar.prev_date(day),
        "dates": radar.dates,
        "latest": radar.dates[0],
        "scoreDate": radar.score_date,
        "universe": sum(1 for code, per in radar.chips.items() if day in per and code in radar.universe),
        "market": sum(1 for per in radar.chips.values() if day in per),
        "rules": rules(),
        "lists": [radar.week_lists(d) for d in list_dates],
        "groups": [{**g, "members": g["members"][:30]} for g in groups],
        "streaks": radar.streaks(day),
        "cumulative": radar.cumulative(day),
        "hot": radar.hot(day, groups),
    }


def stock(code: str) -> dict[str, Any]:
    radar = load_radar()
    code = str(code or "").strip().upper()
    if not radar.dates:
        return {"status": "empty", "message": "還沒有集保週資料", "code": code}
    if code not in radar.chips and code not in radar.names:
        raise ValueError(f"找不到 {code}")
    day = radar.dates[0]
    info = radar.info(code)
    per = radar.chips.get(code) or {}
    group = info.get("group")
    entry = next((g for g in radar.group_table(day) if g["name"] == group), None) if group else None
    return {
        "status": "ok",
        **info,
        "week": day,
        "chip": per.get(day),
        "capital": (radar.capital.get(code) or {}).get(day),
        "outside": code not in radar.universe,
        "rank": radar.rank(code, day),
        "trail": radar.trail(code),
        "groupWeek": day,
        "groupMembers": (entry or {}).get("members") or [],
        "groupAvg": (entry or {}).get("avg"),
        "inst": institutional(code, radar.raw_dates),
    }


def institutional(code: str, tdcc_week_dates: list[str]) -> dict[str, Any]:
    """三大法人：近 5 週（集保結算週，上週結算日之後到這週結算日）加總與近 5 個交易日明細；張，佔成交量 %。"""
    initialize_database()
    with get_connection() as connection:
        try:
            rows = connection.execute(
                """SELECT trade_date, foreign_net, trust_net, dealer_net, total_net, market FROM institutional_daily
                   WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 60""",
                (code,),
            ).fetchall()
        except Exception:  # noqa: BLE001  （法人表還沒建）
            rows = []
        vols = connection.execute(
            "SELECT substr(bar_time, 1, 10) AS d, volume FROM bars_1d WHERE stock_code = ? ORDER BY bar_time DESC LIMIT 80", (code,)
        ).fetchall()
    volume = {str(v["d"]): int(v["volume"] or 0) for v in vols}
    daily = [{"date": str(r["trade_date"]), "foreign": round(r["foreign_net"] / 1000), "trust": round(r["trust_net"] / 1000),
              "dealer": round(r["dealer_net"] / 1000), "total": round(r["total_net"] / 1000), "market": r["market"]} for r in rows]
    for d in daily:
        vol = volume.get(d["date"])
        d["pct"] = round(d["total"] / vol * 100, 1) if vol else None
    weeks = []
    ordered = sorted(tdcc_week_dates, reverse=True)
    for i, end in enumerate(ordered[:INST_WEEKS]):
        start = ordered[i + 1] if i + 1 < len(ordered) else None
        days = [d for d in daily if d["date"] <= end and (start is None or d["date"] > start)]
        if not days:
            continue
        total = sum(d["total"] for d in days)
        vol = sum(volume.get(d["date"], 0) for d in days)
        weeks.append({"date": end, "total": total, "days": len(days), "pct": round(total / vol * 100, 1) if vol else None})
    market = daily[0]["market"] if daily else None
    return {"market": market, "weeks": list(reversed(weeks)), "days": daily[:INST_DAYS]}


# ------------------------------------------------------------------ 籌碼週報

# 2026-10-10 使用者：照莊爸「籌碼週報」（雷達頁上面那顆「籌碼週報・可回看 4 週」）做，每週一份：本週摘要、上週榜對帳、
# 族群排名（名次變化）、整族一起動（同族 2 檔以上上買超榜；他 10/02 的光電在前十外也算，標「榜外」）、
# 單獨上榜（族群裡只有它一檔；族群表以外的另外列）、
# 賣超前 10 名（加「賣超但股價逆勢漲」提醒）。股價用日K收盤（結算日當天或之前最近一天），大盤用證交所加權指數。
WEEKLY_WEEKS = 4                  # 可回看幾週（莊爸：可回看 4 週）
WEEKLY_SELL_SHOW = 10             # 賣超只列前 10（依籌碼減少最多排）
AGAINST_UP = 3.0                  # 賣超榜上、本週股價漲 ≥3% 的另外提醒
RECON_TOP, RECON_BOTTOM = 5, 3    # 上週榜對帳：最強五、最弱三
WEEKLY_CACHE_SECONDS = 600.0
TWSE_FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ymd}&response=json"

_weekly_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_taiex_failed: dict[str, float] = {}


def _taiex_schema(connection: Any) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS taiex_daily (trade_date TEXT PRIMARY KEY, close REAL NOT NULL)")


def parse_fmtqik(payload: Any) -> dict[str, float]:
    """證交所每日市場成交資訊（FMTQIK）：{日期: 發行量加權股價指數}。日期是民國年 115/10/01。"""
    if not isinstance(payload, dict):
        return {}
    fields = [str(f) for f in payload.get("fields") or []]
    idx = next((i for i, f in enumerate(fields) if "加權" in f), None)
    if idx is None:
        return {}
    out: dict[str, float] = {}
    for row in payload.get("data") or []:
        try:
            y, m, d = str(row[0]).strip().split("/")
            out[f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"] = float(str(row[idx]).replace(",", ""))
        except (ValueError, IndexError):
            continue
    return out


def taiex_close(day: str, fetcher: Callable[[str], Any] | None = None) -> float | None:
    """day 當天或之前最近一個交易日的加權指數收盤；表裡沒有就抓那個月（和上個月）的 FMTQIK 存起來。"""
    initialize_database()

    def lookup() -> float | None:
        with get_connection() as connection:
            _taiex_schema(connection)
            row = connection.execute(
                "SELECT close FROM taiex_daily WHERE trade_date <= ? AND trade_date >= ? ORDER BY trade_date DESC LIMIT 1",
                (day, (date.fromisoformat(day) - timedelta(days=7)).isoformat()),
            ).fetchone()
        return float(row["close"]) if row else None

    found = lookup()
    if found is not None:
        return found
    first = date.fromisoformat(day).replace(day=1)
    months = [first, (first - timedelta(days=1)).replace(day=1)]
    if fetcher is None:
        from chips_daily import _default_fetcher as fetcher  # noqa: PLC0415
    got: dict[str, float] = {}
    for month in months:
        key = month.isoformat()
        if time.monotonic() - _taiex_failed.get(key, -1e9) < 1800:
            continue
        try:
            got.update(parse_fmtqik(fetcher(TWSE_FMTQIK_URL.format(ymd=month.strftime("%Y%m01")))))
        except Exception as exc:  # noqa: BLE001
            _taiex_failed[key] = time.monotonic()
            logger.warning("加權指數抓不到 %s：%s", key, exc)
    if got:
        with get_connection() as connection:
            _taiex_schema(connection)
            connection.executemany("INSERT OR REPLACE INTO taiex_daily (trade_date, close) VALUES (?, ?)", sorted(got.items()))
    return lookup()


def _closes(codes: list[str], since: str) -> dict[str, list[tuple[str, float]]]:
    """{代號: [(日期, 收盤), …]}，舊到新。"""
    out: dict[str, list[tuple[str, float]]] = {}
    if not codes:
        return out
    initialize_database()
    with get_connection() as connection:
        for start in range(0, len(codes), 400):
            batch = codes[start:start + 400]
            rows = connection.execute(
                f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, close FROM bars_1d
                    WHERE bar_time >= ? AND stock_code IN ({','.join('?' for _ in batch)}) ORDER BY bar_time""",
                (since, *batch),
            ).fetchall()
            for r in rows:
                if r["close"]:
                    out.setdefault(str(r["stock_code"]).upper(), []).append((str(r["d"]), float(r["close"])))
    return out


def _close_on(series: list[tuple[str, float]], day: str | None) -> float | None:
    if not day:
        return None
    found = None
    for d, close in series:
        if d > day:
            break
        found = close
    return found


def _pct(a: float | None, b: float | None, digits: int | None = 1) -> float | None:
    if not a or not b:
        return None
    value = (b / a - 1) * 100
    return round(value, digits) if digits is not None else value


def weekly_report(week: str | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    radar = load_radar()
    if not radar.dates:
        return {"status": "empty", "message": "還沒有集保週資料（至少要兩週才能比）", "weeks": []}
    if week and week not in radar.dates:
        raise ValueError(f"沒有 {week} 這週的集保資料")
    day = week or radar.dates[0]
    key = (day, f"{radar.dates[0]}:{len(radar.chips)}")
    hit = _weekly_cache.get(key)
    if hit and time.monotonic() - hit[0] < WEEKLY_CACHE_SECONDS and fetcher is None:
        return hit[1]
    out = _weekly(radar, day, fetcher)
    if fetcher is None:
        _weekly_cache[key] = (time.monotonic(), out)
    return out


def _weekly(radar: Radar, day: str, fetcher: Callable[[str], Any] | None) -> dict[str, Any]:
    i = radar.dates.index(day)
    prev = radar.prev_date(day)
    nxt = radar.dates[i - 1] if i > 0 else None
    buy, sell = radar.lists(day)
    pbuy, psell = radar.lists(prev) if prev else ([], [])
    # ⭐ 連續兩週上榜：買超卡片要上週也在買超榜（禾伸堂 9/24 賣超、10/02 買超，莊爸沒打星）；
    # 賣超卡片上週在哪一邊都算（九豪 10/02 買超、10/08 賣超，他有打星）
    prev_buy, prev_listed = set(pbuy), set(pbuy) | set(psell)
    oldest = radar.prev_date(prev) if prev else None
    since = (date.fromisoformat(oldest or prev or day) - timedelta(days=10)).isoformat()
    closes = _closes(sorted(set(buy) | set(sell) | set(pbuy) | set(psell)), since)

    def chg(code: str, a: str | None, b: str | None, digits: int | None = 1) -> float | None:
        series = closes.get(code) or []
        return _pct(_close_on(series, a), _close_on(series, b), digits) if a and b else None

    def label(code: str) -> str | None:
        """卡片上的族群標籤：第一個族群在前、其他接在後面（莊爸：千元/矽光子）；不排名的族群（功率半導體…）不標。"""
        primary = radar.group_of.get(code)
        names = [primary] if primary else []
        names += [n for n, codes in radar.group_members.items() if code in codes and n not in names and n not in RANK_SKIP_GROUPS]
        return "/".join(names) or None

    def ups(code: str) -> int:
        per, n = radar.chips.get(code) or {}, 0
        for d in radar.dates[i:]:
            if per.get(d, 0) <= 0:
                break
            n += 1
        return n

    def card(code: str, sell_side: bool = False) -> dict[str, Any]:
        return {**radar.info(code), "group": label(code), "chip": radar.chips[code][day], "chip1": chip_one_decimal(radar.xs[code][day]),
                "close": _close_on(closes.get(code) or [], day), "week": chg(code, prev, day), "after": chg(code, day, nxt),
                "ups": ups(code), "star": code in (prev_listed if sell_side else prev_buy), "capital": (radar.capital.get(code) or {}).get(day),
                "vs": (radar.skipped.get(code) or {}).get(day)}

    # 族群排名＋整族一起動
    groups = radar.ranked_groups(day)
    prev_rank = {g["name"]: k for k, g in enumerate(radar.ranked_groups(prev) if prev else [], 1)}
    buy_set = set(buy)
    ranking = []
    # 上榜的股票只算它的第一個族群（莊爸的光聖「千元/矽光子」不算進矽光子的 2 檔）
    by_group: dict[str, list[str]] = {}
    for c in buy:
        if c in radar.group_of:
            by_group.setdefault(radar.group_of[c], []).append(c)
    for k, g in enumerate(groups, 1):
        listed = by_group.get(g["name"], [])
        pr = prev_rank.get(g["name"]) if prev else None
        move = None if not prev else ({"kind": "new"} if pr is None else {"kind": "same"} if pr == k
                                      else {"kind": "up" if pr > k else "down", "n": abs(pr - k)})
        ranking.append({"rank": k, "name": g["name"], "avg": g["avg"], "prevRank": pr, "move": move,
                        "listed": [{"code": c, "name": radar.info(c)["name"]} for c in listed], "together": len(listed) >= 2})
    rank_of = {g["name"]: g["rank"] for g in ranking}
    avg_of = {g["name"]: g["avg"] for g in radar.group_table(day)}
    together = []
    for name, listed in by_group.items():
        if len(listed) >= 2 and name not in PRICE_BAND_GROUPS and name not in RANK_SKIP_GROUPS:
            together.append({"name": name, "rank": rank_of.get(name), "prevRank": prev_rank.get(name), "avg": avg_of.get(name, 0.0),
                             "count": len(listed), "cards": [card(c) for c in listed]})
    together.sort(key=lambda t: (t["rank"] or 99, -t["avg"], t["name"]))
    in_together = {c["code"] for t in together for c in t["cards"]}
    single = [card(c) for c in buy if c not in in_together and c in radar.group_of]
    nogroup = [card(c) for c in buy if c not in radar.group_of]
    sell_cards = [card(c, sell_side=True) for c in sell]
    against = sorted((c for c in sell_cards if c["week"] is not None and c["week"] >= AGAINST_UP), key=lambda c: -c["week"])

    # 上週榜對帳：上週那份榜單，上週結算日收盤 → 這週結算日收盤
    recon = None
    if prev:
        def side(codes: list[str]) -> dict[str, Any]:
            # 平均用沒四捨五入的漲跌（莊爸 10/08 買超 32 檔平均 +3.06%；先取一位再平均會變 +3.05%）
            raw = {c: chg(c, prev, day, None) for c in codes}
            rows = [{**radar.info(c), "group": label(c), "chg": round(raw[c], 1)} for c in codes if raw[c] is not None]
            ranked = sorted(rows, key=lambda r: (-r["chg"], r["code"]))
            priced = [v for v in raw.values() if v is not None]
            return {"count": len(codes), "priced": len(rows), "avg": round(sum(priced) / len(priced), 2) if priced else None,
                    "up": sum(1 for r in rows if r["chg"] > 0), "best": ranked[:RECON_TOP],
                    "worst": ranked[-RECON_BOTTOM:] if len(ranked) > RECON_TOP else []}
        tx = _pct(taiex_close(prev, fetcher), taiex_close(day, fetcher), 2)      # 加權指數取兩位（莊爸：同期加權 +1.73%）
        recon = {"from": prev, "to": day, "buy": side(pbuy), "sell": side(psell), "taiex": tx}

    top = ranking[0] if ranking else None
    prev_names = [g["name"] for g in radar.ranked_groups(prev)] if prev else []
    names = [g["name"] for g in ranking]
    summary = {
        "top": {"name": top["name"], "listed": len(top["listed"]), "together": top["together"]} if top else None,
        "newIn": [n for n in names if n not in prev_names] if prev else [],
        "dropped": [n for n in prev_names if n not in names],
        "together": [{"name": t["name"], "count": t["count"]} for t in together],
        "twoWeeks": [{"code": c, "name": radar.info(c)["name"]} for c in buy if c in set(pbuy)],
        "against": [{"code": c["code"], "name": c["name"], "week": c["week"]} for c in against],
        "recon": {"buyAvg": recon["buy"]["avg"], "buyCount": recon["buy"]["count"], "taiex": recon["taiex"]} if recon else None,
    }
    return {
        "status": "ok",
        "week": day, "prevWeek": prev, "nextWeek": nxt,
        "weeks": radar.dates[:WEEKLY_WEEKS] if day in radar.dates[:WEEKLY_WEEKS] else [day, *radar.dates[:WEEKLY_WEEKS - 1]],
        "latest": radar.dates[0], "scoreDate": radar.score_date,
        "generatedAt": datetime.now().isoformat(timespec="minutes"),
        "counts": {"buy": len(buy), "sell": len(sell), "togetherGroups": len(together), "togetherStocks": len(in_together)},
        "summary": summary,
        "recon": recon,
        "ranking": ranking,
        "together": together,
        "single": single,
        "nogroup": nogroup,
        "sell": sell_cards[:WEEKLY_SELL_SHOW],
        "against": against,
        "rules": {"buyMin": BUY_MIN, "sellMax": SELL_MAX, "againstUp": AGAINST_UP, "sellShow": WEEKLY_SELL_SHOW,
                  "priceBand": list(PRICE_BAND_GROUPS), "weeks": WEEKLY_WEEKS},
    }
