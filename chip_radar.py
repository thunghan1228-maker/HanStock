"""籌碼暴增雷達（2026-10-04 使用者：照莊爸 zhuang.tw/radar「籌碼暴增雷達」做，放在下方導覽列「盤後籌碼排行」）。

看的是「大戶手上的股票變多還是變少」：集保股權分散表（每週五結算，tdcc_weekly，fundamentals_daily 從 tw-groups 鏡像匯入）。

籌碼%（跟莊爸一樣；2026-10-04 用集保 6/5～10/2 的資料逐一比對截圖：買超榜、賣超榜、個股九週卡片、連續增排行
全部吻合到小數第二位）：
  x ＝（這週 400 張以上大戶（第 12～15 級）持股股數 − 上週）÷ 這週總股數（第 17 級合計）× 100，取兩位小數
  籌碼% ＝ 3 × √x（x > 0，大戶變多）；x ≤ 0（大戶變少）照原樣。兩種都取兩位小數。
  （分母用這週的總股數：增資那週，例如環球晶 10/02 增資 5 萬張，算出來才對得上。）

畫面上的各區塊：
- 本週籌碼暴增榜：買超榜（籌碼% ≥ 4）、賣超榜（≤ −1.5）；連兩週都在同一邊的榜上打 ⭐；可以切最近 8 週。
- 上榜累積榜：每週買超榜前十名記一筆；視窗內（16／8／4 週）擠進前十的次數、近 4 週次數、平均籌碼%、
  分數（＝那幾週籌碼% 加總＝次數 × 平均）、最佳名次。
- 族群排名比較：族群裡籌碼% 最高的 5 檔平均（莊爸的石英 +3.46% ＝ 前 5 檔平均，對得上），前十族群。
- 連續增排行：六週內至少五週增（可漏一週）、連三週都增；都用窗口平均排序，各取前 20。
- 熱門股：本週前十名＋前五大族群的第一名，附九週軌跡。
- 個股查詢：九週軌跡（前十名附名次）、同族群當週排名、三大法人（每週加總、近 5 日，佔成交量 %）。
每列最後的數字＝均線分數（官網那套 15 分，heilong_daily.score2，最近一個交易日）。

股本變動：那週總股數變了（增資、減資、轉換公司債），變動 ≥1% 名單上標「股本 ±x%」。
增資照算（跟莊爸一樣：環球晶 10/02 增資 10.5% 算出 +9.53、佳大 9/24 私募 48.7% 算出 +16.81 都照列）；
總股數「減少」≥5%（減資、股份轉換）大戶股數會跟著機械式縮水，不進任何排行（例如桂田文創 10/02 減資 30% 算出 −40.15、
益航股份轉換），個股軌跡照列並註明。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date
from typing import Any

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
LIST_WEEKS = 8                    # 本週榜可以切幾週
CARD_WEEKS = 9                    # 個股卡片的軌跡週數
WINDOW_MAX = 16                   # 累積榜最多回看幾週
WINDOWS = (16, 8, 4)
HOT_GROUPS = 5
INST_WEEKS = 5
INST_DAYS = 5
CACHE_SECONDS = 60.0
MAX_WEEK_GAP_DAYS = 10            # 兩個集保結算日最多差幾天還算「上一週」（遇到連假會差 6～8 天）
CAPITAL_TAG = 1.0                 # 這週總股數變動 ≥1%（增資、減資、轉換公司債）：名單上標「股本 ±x%」
CAPITAL_CUT_EXCLUDE = 5.0         # 總股數「減少」≥5%（減資、股份轉換）：大戶股數跟著機械式縮水，不進排行（軌跡照列並註明）；
                                  # 增資照算（莊爸也照列：佳大 9/24 私募 +48.7% 算出 +16.81，進了他的累積榜和六週平均）


def chip_value(big_now: float, big_prev: float, total_now: float) -> tuple[float, float] | None:
    """(x, 籌碼%)；x＝大戶股數變化 ÷ 這週總股數 ×100（兩位小數），籌碼%＝x>0 時 3√x。"""
    if not total_now or total_now <= 0:
        return None
    x = round((big_now - big_prev) / total_now * 100, 2)
    chip = round(3 * math.sqrt(x), 2) if x > 0 else x
    return x, chip


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
        rows = connection.execute(
            f"""SELECT data_date, stock_code, level, shares, pct FROM tdcc_weekly
                WHERE data_date IN ({','.join('?' for _ in dates)}) AND level IN ({levels})""",
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
    """({族群: [代號…]}, {代號: 第一個族群})，不含股期標的那份清單。"""
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
    return members, first


# ------------------------------------------------------------------ 計算

class Radar:
    """一份算好的雷達（快取用）。chips[代號][日期] ＝ 籌碼%；dates 新到舊、都有上一週可以比。"""

    def __init__(self) -> None:
        raw_dates, tdcc = _load_tdcc(WINDOW_MAX + LIST_WEEKS + 2)
        self.names = _names()
        self.score_date, self.scores = _ma_scores()
        self.group_members, self.group_of = _groups()
        self.chips: dict[str, dict[str, float]] = {}
        self.xs: dict[str, dict[str, float]] = {}
        self.capital: dict[str, dict[str, float]] = {}      # 那週總股數變動 %（≥1% 才記）
        self.distorted: dict[str, dict[str, float]] = {}    # 股本大變那週的籌碼%（不進排行）
        # 只跟「上一週」比：中間缺了一週（鏡像漏抓）那週就不算，不然會變成兩週的變化
        pairs = [i for i in range(len(raw_dates) - 1) if _days_between(raw_dates[i + 1], raw_dates[i]) <= MAX_WEEK_GAP_DAYS]
        for code, per in tdcc.items():
            for i in pairs:
                now, prev = per.get(raw_dates[i]), per.get(raw_dates[i + 1])
                if not now or not prev or not now[1]:
                    continue
                value = chip_value(now[0], prev[0], now[1])
                if value is None:
                    continue
                change = round((now[1] / prev[1] - 1) * 100, 1) if prev[1] else 0.0
                if abs(change) >= CAPITAL_TAG:
                    self.capital.setdefault(code, {})[raw_dates[i]] = change
                if change <= -CAPITAL_CUT_EXCLUDE:
                    self.distorted.setdefault(code, {})[raw_dates[i]] = value[1]
                    continue
                self.xs.setdefault(code, {})[raw_dates[i]] = value[0]
                self.chips.setdefault(code, {})[raw_dates[i]] = value[1]
        self.dates = [d for d in raw_dates[:-1] if any(d in per for per in self.chips.values())] if len(raw_dates) > 1 else []
        self.raw_dates = raw_dates
        self._lists: dict[str, tuple[list[str], list[str]]] = {}

    # 名單 -------------------------------------------------------------
    def lists(self, day: str) -> tuple[list[str], list[str]]:
        """那週的買超榜、賣超榜（代號，照籌碼% 排好）。"""
        if day not in self._lists:
            values = [(code, per[day]) for code, per in self.chips.items() if day in per]
            buy = [c for c, v in sorted(values, key=lambda cv: (-cv[1], cv[0])) if v >= BUY_MIN]
            sell = [c for c, v in sorted(values, key=lambda cv: (cv[1], cv[0])) if v <= SELL_MAX]
            self._lists[day] = (buy, sell)
        return self._lists[day]

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
            return [{**self.info(c), "chip": self.chips[c][day], "rank": i + 1, "star": c in prev_set,
                     "capital": (self.capital.get(c) or {}).get(day)} for i, c in enumerate(codes)]

        return {"date": day, "label": _short(day), "buy": rows(buy, prev_buy_set), "sell": rows(sell, prev_sell_set)}

    def trail(self, code: str, weeks: int = CARD_WEEKS) -> list[dict[str, Any]]:
        per = self.chips.get(code) or {}
        bad = self.distorted.get(code) or {}
        cap = self.capital.get(code) or {}
        out = []
        for d in self.dates[:weeks]:
            row: dict[str, Any] = {"date": d, "chip": per.get(d, bad.get(d)), "rank": self.rank(code, d) if d in per else None}
            if d in cap:
                row["capital"] = cap[d]
            if d in bad:
                row["excluded"] = True
            out.append(row)
        return out

    # 族群 -------------------------------------------------------------
    def group_table(self, day: str) -> list[dict[str, Any]]:
        out = []
        for name, codes in self.group_members.items():
            members = sorted(((c, self.chips[c][day]) for c in dict.fromkeys(codes) if c in self.chips and day in self.chips[c]),
                             key=lambda cv: (-cv[1], cv[0]))
            if not members:
                continue
            top = [v for _, v in members[:GROUP_TOP]]
            out.append({"name": name, "avg": round(sum(top) / len(top), 2), "count": len(members),
                        "members": [{**self.info(c), "chip": v, "rank": self.rank(c, day)} for c, v in members]})
        out.sort(key=lambda g: (-g["avg"], g["name"]))
        return out

    # 連續增 -----------------------------------------------------------
    def streaks(self, day: str) -> dict[str, Any]:
        i = self.dates.index(day)
        long_span, long_need = STREAK_LONG
        long_dates = self.dates[i:i + long_span]
        short_dates = self.dates[i:i + STREAK_SHORT]
        six, three = [], []
        for code, per in self.chips.items():
            if len(long_dates) == long_span and all(d in per for d in long_dates):
                values = [per[d] for d in long_dates]
                ups = sum(1 for v in values if v > 0)
                if ups >= long_need:
                    six.append({**self.info(code), "ups": ups, "avg": round(sum(values) / long_span, 2)})
            if len(short_dates) == STREAK_SHORT and all(d in per and per[d] > 0 for d in short_dates):
                three.append({**self.info(code), "avg": round(sum(per[d] for d in short_dates) / STREAK_SHORT, 2)})
        six.sort(key=lambda r: (-r["avg"], r["code"]))
        three.sort(key=lambda r: (-r["avg"], r["code"]))

        def span(ds: list[str]) -> dict[str, Any]:
            return {"from": ds[-1] if ds else None, "to": ds[0] if ds else None, "weeks": len(ds)}

        return {"six": {**span(long_dates), "need": long_need, "rows": six[:STREAK_TOP]},
                "three": {**span(short_dates), "rows": three[:STREAK_TOP]}}

    # 累積榜 -----------------------------------------------------------
    def cumulative(self, day: str) -> dict[str, Any]:
        """day 往前最多 16 週，每週前十名；回每檔每週的 [名次或 0, 籌碼%]（舊到新），前端照選的週數自己加總。"""
        i = self.dates.index(day)
        window = list(reversed(self.dates[i:i + WINDOW_MAX]))      # 舊到新
        tops = {d: self.lists(d)[0][:TOP_N] for d in window}
        codes = sorted({c for top in tops.values() for c in top})
        rows = []
        for code in codes:
            per = self.chips.get(code) or {}
            grid = [[(tops[d].index(code) + 1) if code in tops[d] else 0, per.get(d)] for d in window]
            rows.append({**self.info(code), "grid": grid})
        return {"dates": window, "rows": rows}

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
        "capitalTag": CAPITAL_TAG, "capitalCutExclude": CAPITAL_CUT_EXCLUDE,
        "streakLong": list(STREAK_LONG), "streakShort": STREAK_SHORT, "streakTop": STREAK_TOP,
        "windows": list(WINDOWS), "cardWeeks": CARD_WEEKS, "listWeeks": LIST_WEEKS,
        "formula": "x＝（這週 400 張以上大戶持股股數 − 上週）÷ 這週總股數 × 100；籌碼%＝3×√x（大戶增加）或 x（大戶減少）",
    }


def payload(week: str | None = None) -> dict[str, Any]:
    radar = load_radar()
    if not radar.dates:
        return {"status": "empty", "message": "還沒有集保週資料（至少要兩週才能比）", "dates": [], "rules": rules()}
    if week and week not in radar.dates:
        raise ValueError(f"沒有 {week} 這週的集保資料")
    day = week or radar.dates[0]
    groups = radar.group_table(day)
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
        "universe": sum(1 for per in radar.chips.values() if day in per),
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
    if code not in radar.chips and code not in radar.distorted and code not in radar.names:
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
        "chip": per.get(day, (radar.distorted.get(code) or {}).get(day)),
        "capital": (radar.capital.get(code) or {}).get(day),
        "excluded": day in (radar.distorted.get(code) or {}),
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
