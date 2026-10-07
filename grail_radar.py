"""飆股雷達（2026-10-07 使用者：照莊爸 App 的「飆股雷達」做一個，條件我們自己算）。

莊爸的飆股雷達是在固定時間跑嗨投資「紫殺」學院的四個選股聖杯（穿山鱷龍／黑龍短沖／小資利器之黑飛舞／飛龍戰法），
共 15 個邏輯，把名單貼上網站。聖杯只給結果、不公開條件，這裡的條件是 2026-10-07 用使用者截圖的嗨投資一個月名單
（9/7～10/6，每檔顯示最早符合那天）加上莊爸網站 10/1～10/6 四天的名單，對全市場日K反推出來的近似條件
（CALIBRATION 是那次對答案的結果：嗨投資選的我們抓到幾成、我們選的有幾成在嗨投資名單裡）。
主力籌碼（嗨投資的「主力買賣超」「1日σ／10日σ」）我們沒有全市場的資料，所以有幾個邏輯準度比較低。

每個邏輯照莊爸網站上的時間點算：
- 盤中時間點：證交所 MIS 即時報價（跟首頁同一個來源）組出全市場今天到那一刻的K棒（開、高、低、現價、累積張數），
  接在官方日K後面算條件；那個時間點過了 SLOT_GRACE_MINUTES 分鐘還沒算到（例如剛好重新部署）就不補，留空。
  13:30 收盤以後的時間點（13:45、14:00、15:00）用的是收盤後的最後報價，晚一點算也一樣，所以當天都可以補。
- 收盤：官方日K（上市證交所、上櫃鏡像／Yahoo）到齊以後再用整根日K算一次「收盤」，往日名單也是這樣回推的。
全部是條件篩選，不是買賣建議。
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from array import array
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from database import get_connection, initialize_database
from stock_groups import SPECIAL_GROUP_NAMES, STOCK_GROUPS
from trading_days import is_trading_day, previous_trading_day

logger = logging.getLogger("hanstock.grail_radar")
TW_TZ = timezone(timedelta(hours=8))
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_CHUNK = 80
MIS_WORKERS = 4
POLL_SECONDS = 15
SLOT_GRACE_MINUTES = 8          # 盤中時間點過了幾分鐘還沒算就不補（報價已經是後來的了）
AFTER_CLOSE_SLOT = "13:30"      # 這之後的時間點用收盤後報價，當天都能補
CLOSE_SLOT = "收盤"
CLOSE_FROM = (14, 30)           # 每天 14:30 起每 CLOSE_RETRY_SECONDS 試一次「收盤」（官方日K到齊才算）
CLOSE_RETRY_SECONDS = 15 * 60
CLOSE_FINAL_AFTER = (18, 0)     # 18:00 以後再算最後一次（上櫃 Yahoo 補的晚到）
HISTORY_CALENDAR_DAYS = 420     # MA240 要 240 根，日曆天抓寬一點
MIN_BARS = 131
BACKFILL_DAYS = max(0, int(os.getenv("HANSTOCK_GRAIL_RADAR_BACKFILL_DAYS", "20")))
TSE_DAY_MIN = 500
OTC_DAY_RATIO = 0.9
MA_PERIODS = (5, 10, 20, 60, 120, 240)

SAINTS = [
    {"id": 57, "name": "穿山鱷龍"},
    {"id": 32, "name": "黑龍短沖"},
    {"id": 24, "name": "小資利器之黑飛舞"},
    {"id": 30, "name": "飛龍戰法"},
]


def _cross2022(f: dict[str, Any]) -> bool:
    return (f["chg"] >= 1.8 and f["pc_ma20"] <= 0 and f["c_ma20"] > 0 and f["c_ma5"] >= 1.9 and f["c_ma120"] >= 10
            and f["dd60"] >= -15.5 and f["run60"] >= 25 and f["ma60_slope5"] >= -0.5 and f["vol"] >= 1000 and f["h_ma20"] >= 1.8)


def _breakred(f: dict[str, Any]) -> bool:
    return (f["body"] >= 0.2 and f["l_pl"] <= -0.1 and f["l_ma20"] <= -0.2 and f["h_ma20"] >= -1.5 and f["c_ma20"] <= 2.7
            and f["ma20_ma60"] >= 1.8 and f["c_m5_prev"] <= 0.25 and f["c_ma240"] >= 3.5 and f["dd60"] >= -17 and f["vma5"] >= 250)


def _crossconv(f: dict[str, Any]) -> bool:
    return (-6 <= f["c_ma20"] <= -0.75 and f["h_ma20"] >= -2.5 and f["ma20_ma60"] >= 2 and f["m5_m10"] <= -0.5
            and f["ma10_slope"] <= -0.1 and f["v_pv"] <= 0.8 and f["v_v5"] <= 0.7 and f["vma5"] >= 350
            and f["dd120"] >= -22 and f["ret3"] <= 0.5)


def _rsword(f: dict[str, Any]) -> bool:
    return (f["hiago20"] == 0 and f["ushadow"] >= 2.2 and f["body"] >= 0.1 and f["chg"] <= 5 and f["c_ma20"] >= 5
            and f["ma20_slope5"] >= -1.7 and f["vol"] >= 1000)


def _xdragon(f: dict[str, Any]) -> bool:
    return (f["chg"] < 0 and f["c_m5_prev"] >= 5.3 and f["l_ma5"] >= 0 and f["hiago20"] <= 1 and f["range"] >= 3.8
            and f["lshadow"] <= 2.9 and f["ma20_ma60"] >= 0 and f["ma20_slope5"] >= 0.3 and f["c_ma60"] >= 8.5 and f["vol"] >= 2000)


def _panther(f: dict[str, Any]) -> bool:
    return (f["hiago120"] == 0 and f["body"] <= -3 and f["h_ph"] >= 4 and f["c_m5_prev"] >= 9 and f["ma5_ma20"] <= 18
            and f["vol"] >= 5000)


def _shadow(f: dict[str, Any]) -> bool:
    return (f["lshadow"] >= 2.6 and not f["l_ge_pl"] and f["body"] <= 2.4 and f["chg"] >= -2.7 and f["c_ma5"] <= 1.3
            and f["z_min5"] >= 0.75 and f["c_ma60"] >= 9 and f["vol"] >= 1000)


def _bfw2021(f: dict[str, Any]) -> bool:
    return (f["prev_black"] and f["hiago60"] <= 1 and f["newhi_cnt3"] >= 2 and not f["h_gt_ph"] and f["v_pv"] <= 0.8
            and f["vol_prev"] >= 5000 and f["ret3"] >= 2 and f["run20"] >= 25 and f["l_ma10"] <= 12 and f["hup_max20"] <= 15.5)


def _bfw905(f: dict[str, Any]) -> bool:
    return (f["prev_black"] and f["hiago120"] <= 1 and f["vol_prev"] >= 5000 and f["hup_max20"] >= 8.5
            and f["ma60_ma120"] >= 0 and f["l_ma5"] >= -3 and f["below_cnt20"] <= 10)


def _swordfw(f: dict[str, Any]) -> bool:
    return (f["pc_ma20"] >= 11 and f["v_pv"] <= 0.33 and not f["l_ge_pl"] and f["above5_cnt3"] == 3 and f["bw"] <= 63
            and f["vol"] >= 1000)


def _bfwfut(f: dict[str, Any]) -> bool:
    return (f["futures"] and f["newhi_cnt3"] >= 2 and f["h_ph"] <= -0.9 and f["v_v5"] <= 1.0 and f["v_v20"] >= 0.7
            and f["ret3_min"] >= -4.5 and f["chg_prev"] <= 3.5 and f["range"] <= 6.2 and f["run60"] >= 30)


def _fly3(f: dict[str, Any]) -> bool:
    return (f["hiago60"] <= 2 and f["above5_cnt3"] == 3 and f["l_ma10"] >= 3 and f["chg_prev"] <= 6 and f["h_ph"] <= 6.7
            and f["hup_max20"] >= 4.5 and f["v_v5"] <= 2.4 and f["z"] <= 2.8 and f["vma5"] >= 300)


def _flyburst(f: dict[str, Any]) -> bool:
    return (f["hiago120"] == 0 and f["chg"] >= 6 and f["v_v5"] >= 2.8 and f["hup_max20"] <= 10 and f["bw_min10"] <= 22
            and f["vol"] >= 3500)


def _flybreak(f: dict[str, Any]) -> bool:
    return (f["hiago60"] == 0 and f["chg"] >= 6 and f["body"] >= 3.5 and f["chg_prev"] <= 3.5 and f["lshadow"] <= 2.2
            and f["bw"] <= 25 and f["c_ma120"] >= 9.5 and f["c_ma240"] >= 15 and f["v_pv"] <= 7)


def _red3(f: dict[str, Any]) -> bool:
    return (f["consec_red"] >= 3 and f["c_ma5"] >= 4 and f["c_ma20"] >= 5 and f["spread3"] <= 4.6 and 0.45 <= f["close_pos"] <= 0.98
            and f["chg_prev"] <= 8.4 and f["dd60"] <= -2 and f["c_ma240"] >= 0 and f["vol"] >= 1000)


# 名稱與時間點照莊爸網站；desc 是我們反推的條件（給頁面「條件說明」用）；calibration＝2026-10-07 對答案的結果。
LOGICS: list[dict[str, Any]] = [
    {"key": "cross2022", "saintId": 57, "name": "波段穿惡2022版本", "kind": "波段", "rule": _cross2022,
     "times": ["09:55", "10:15", "11:15", "12:10", "13:10", "13:45"],
     "desc": "穿惡：昨天收在月線（20日線）下、今天收盤站回月線，漲 1.8% 以上、收在 5 日線上 1.9% 以上、最高超過月線 1.8%；"
             "前 60 天高低差 25% 以上（第一波）、離 60 日高點回檔 15.5% 以內、收盤比半年線高 10% 以上、季線沒有往下彎；量 1000 張以上。",
     "calibration": {"recall": 92, "precision": 57}},
    {"key": "breakred", "saintId": 57, "name": "即將突破惡(紅)", "kind": "波段", "rule": _breakred,
     "times": ["12:30", "13:10", "13:25", "15:00"],
     "desc": "回測月線收紅：今天收紅K（收比開高 0.2% 以上）、低點跌破昨天低點也跌破月線，最高不低於月線 1.5%、收盤在月線 +2.7% 以內；"
             "昨天收在 5 日線以下；月線比季線高 1.8% 以上、收盤比年線高 3.5% 以上、離 60 日高點 17% 以內；5 日均量 250 張以上。",
     "calibration": {"recall": 89, "precision": 47}},
    {"key": "crossconv", "saintId": 57, "name": "即將穿惡(收斂)", "kind": "波段", "rule": _crossconv,
     "times": ["12:20", "15:00"],
     "desc": "月線下量縮收斂：收在月線下 0.75%～6%、最高離月線不到 2.5%；5 日線在 10 日線下、10 日線往下彎；"
             "量比昨天縮到 8 成以下、不到 5 日均量 7 成（5 日均量 350 張以上）；月線比季線高 2% 以上、離 120 日高點 22% 以內、近 3 天沒漲。",
     "calibration": {"recall": 95, "precision": 31}},
    {"key": "rsword", "saintId": 32, "name": "隔日沖-R劍", "kind": "隔日沖", "rule": _rsword,
     "times": ["11:30", "12:00", "12:40", "13:20"],
     "desc": "創 20 日新高的紅K長上影（R劍）：今天最高是 20 日新高、上影線 2.2% 以上、收紅、漲幅 5% 以內；"
             "收盤比月線高 5% 以上、月線 5 天內沒有明顯下彎；量 1000 張以上。",
     "calibration": {"recall": 94, "precision": 99}},
    {"key": "xdragon", "saintId": 32, "name": "隔日沖-極限黑龍", "kind": "隔日沖", "rule": _xdragon,
     "times": ["11:30", "12:00", "12:40", "13:20"],
     "desc": "強勢股換手收跌：今天下跌、低點不破 5 日線、振幅 3.8% 以上、下影線 2.9% 以內；昨天收盤在 5 日線上 5.3% 以上、"
             "20 日新高在今天或昨天；月線在季線上而且 5 天內往上、收盤比季線高 8.5% 以上；量 2000 張以上。",
     "calibration": {"recall": 91, "precision": 89}},
    {"key": "panther", "saintId": 32, "name": "超黑豹2023版", "kind": "隔日沖", "rule": _panther,
     "times": ["11:45", "12:55", "13:10", "13:25"],
     "desc": "創 120 日新高的長黑：今天最高是 120 日新高、比昨天最高再高 4% 以上，但收長黑（收比開低 3% 以上）；"
             "昨天收盤在 5 日線上 9% 以上、5 日線離月線 18% 以內；量 5000 張以上。",
     "calibration": {"recall": 85, "precision": 100}},
    {"key": "shadow", "saintId": 32, "name": "隔日沖-神下影", "kind": "隔日沖", "rule": _shadow,
     "times": ["11:55", "12:30", "13:10", "14:00"],
     "desc": "強勢股長下影：下影線 2.6% 以上、低點跌破昨天低點、實體 2.4% 以內、跌幅 2.7% 以內、收盤離 5 日線 1.3% 以內；"
             "近 5 天收盤都在月線上方（至少 0.75 個標準差）、收盤比季線高 9% 以上；量 1000 張以上。",
     "calibration": {"recall": 91, "precision": 74}},
    {"key": "bfw2021", "saintId": 24, "name": "黑飛舞小波段2021版", "kind": "隔日沖", "rule": _bfw2021,
     "times": ["12:40", "13:20", "13:45"],
     "desc": "黑飛舞：昨天收黑K而且大量（5000 張以上）、今天量縮到昨天 8 成以下、今天最高沒超過昨天；"
             "60 日新高在今天或昨天、近 3 天有 2 天創 20 日新高、3 天漲 2% 以上、20 天高低差 25% 以上、低點離 10 日線 12% 以內、近 20 天最多超出布林上緣 15.5%。",
     "calibration": {"recall": 96, "precision": 79}},
    {"key": "bfw905", "saintId": 24, "name": "黑飛舞905", "kind": "隔日沖", "rule": _bfw905,
     "times": ["12:30", "12:40", "12:50", "13:10", "13:20", "13:45"],
     "desc": "黑飛舞 905：昨天收黑K而且大量（5000 張以上）、120 日新高在今天或昨天；近 20 天曾超出布林上緣 8.5% 以上；"
             "季線在半年線上；今天低點離 5 日線 3% 以內；近 20 天收在月線下的日子 10 天以內。",
     "calibration": {"recall": 86, "precision": 40}},
    {"key": "swordfw", "saintId": 24, "name": "隔日沖-劍飛舞", "kind": "隔日沖", "rule": _swordfw,
     "times": ["12:40", "13:20", "13:45"],
     "desc": "劍飛舞：昨天收盤比月線高 11% 以上（很強）、今天量急縮到昨天 1/3 以下、低點跌破昨天低點，但近 3 天都收在 5 日線上；"
             "布林帶寬 63% 以內；量 1000 張以上。",
     "calibration": {"recall": 90, "precision": 96}},
    {"key": "bfwfut", "saintId": 24, "name": "黑飛舞(股期)", "kind": "隔日沖", "rule": _bfwfut,
     "times": ["12:50", "13:25"],
     "desc": "有股票期貨的股票：近 3 天有 2 天創 20 日新高、今天最高比昨天低 0.9% 以上（不再創高）；量不到 5 日均量但有 20 日均量 7 成；"
             "近 3 天單日跌幅都在 4.5% 以內、昨天漲 3.5% 以內、振幅 6.2% 以內；60 天高低差 30% 以上。",
     "calibration": {"recall": 88, "precision": 44}},
    {"key": "fly3", "saintId": 30, "name": "三日飛龍", "kind": "波段", "rule": _fly3,
     "times": ["12:55", "14:00"],
     "desc": "三日飛龍：60 日新高在最近 3 天內、近 3 天都收在 5 日線上、今天低點比 10 日線高 3% 以上；昨天漲 6% 以內、今天最高比昨天高 6.7% 以內；"
             "近 20 天曾超出布林上緣 4.5%；量不超過 5 日均量 2.4 倍、收盤在月線 +2.8 個標準差以內；5 日均量 300 張以上。",
     "calibration": {"recall": 90, "precision": 34}},
    {"key": "flyburst", "saintId": 30, "name": "飛龍紅爆2022版", "kind": "波段", "rule": _flyburst,
     "times": ["11:15", "12:50", "14:00"],
     "desc": "飛龍紅爆：今天最高是 120 日新高、漲 6% 以上、爆量（5 日均量 2.8 倍以上、3500 張以上）；"
             "近 10 天布林帶寬曾縮到 22% 以內（從整理區噴出）、近 20 天最多超出布林上緣 10%。",
     "calibration": {"recall": 100, "precision": 80}},
    {"key": "flybreak", "saintId": 30, "name": "飛龍突破2023版", "kind": "波段", "rule": _flybreak,
     "times": ["09:45", "10:00", "12:10"],
     "desc": "飛龍突破：今天最高是 60 日新高、漲 6% 以上的長紅（收比開高 3.5% 以上、下影 2.2% 以內）；昨天漲 3.5% 以內；"
             "布林帶寬 25% 以內；收盤比半年線高 9.5%、比年線高 15% 以上；量不超過昨天 7 倍。",
     "calibration": {"recall": 95, "precision": 81}},
    {"key": "red3", "saintId": 30, "name": "三紅劍小波段", "kind": "波段", "rule": _red3,
     "times": ["12:30", "13:10", "13:25", "15:00"],
     "desc": "三紅劍：連續 3 天以上收紅K、收在 5 日線上 4%、月線上 5% 以上；5/10/20 日線糾結在 4.6% 以內；"
             "收在當天振幅的中上段（45%～98%）、昨天漲 8.4% 以內、離 60 日高點至少回 2%、收在年線上；量 1000 張以上。",
     "calibration": {"recall": 91, "precision": 67}},
]
LOGIC_BY_KEY = {logic["key"]: logic for logic in LOGICS}
ALL_SLOTS = sorted({t for logic in LOGICS for t in logic["times"]})


def _enabled() -> bool:
    return os.getenv("HANSTOCK_GRAIL_RADAR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _now() -> datetime:
    return datetime.now(TW_TZ)


# ------------------------------------------------------------------ 特徵

def _ma_series(c: Sequence[float], n: int) -> list[float]:
    out = [math.nan] * len(c)
    total = 0.0
    for i, value in enumerate(c):
        total += value
        if i >= n:
            total -= c[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def _std_series(c: Sequence[float], ma20: list[float], start: int) -> list[float]:
    out = [math.nan] * len(c)
    for i in range(max(19, start), len(c)):
        m = ma20[i]
        out[i] = math.sqrt(sum((y - m) ** 2 for y in c[i - 19:i + 1]) / 20)
    return out


def compute_features(o: Sequence[float], h: Sequence[float], l: Sequence[float], c: Sequence[float], v: Sequence[float]) -> dict[str, Any] | None:
    """最後一根（i = 最後）是「今天」（盤中就是到那一刻的K棒）；不夠 131 根或均線算不出來回 None。"""
    n = len(c)
    i = n - 1
    if n < MIN_BARS or c[i - 1] <= 0 or c[i - 2] <= 0 or o[i] <= 0 or h[i - 1] <= 0 or l[i - 1] <= 0 or v[i - 1] < 0:
        return None
    ma = {k: _ma_series(c, k) for k in MA_PERIODS}
    m5, m10, m20, m60, m120, m240 = (ma[k][i] for k in MA_PERIODS)
    if not (m20 > 0 and m60 > 0):
        return None
    sd = _std_series(c, ma[20], i - 25)
    for j in range(i - 20, i + 1):
        if not sd[j] == sd[j]:
            return None
    f: dict[str, Any] = {}
    f["chg"] = (c[i] / c[i - 1] - 1) * 100
    f["chg_prev"] = (c[i - 1] / c[i - 2] - 1) * 100
    f["body"] = (c[i] / o[i] - 1) * 100
    f["ushadow"] = (h[i] - max(o[i], c[i])) / c[i - 1] * 100
    f["lshadow"] = (min(o[i], c[i]) - l[i]) / c[i - 1] * 100
    f["range"] = (h[i] - l[i]) / c[i - 1] * 100
    for k, m in ((5, m5), (20, m20), (60, m60), (120, m120), (240, m240)):
        f[f"c_ma{k}"] = (c[i] / m - 1) * 100 if m == m else 0.0
    f["l_ma20"] = (l[i] / m20 - 1) * 100
    f["h_ma20"] = (h[i] / m20 - 1) * 100
    f["l_ma5"] = (l[i] / m5 - 1) * 100
    f["l_ma10"] = (l[i] / m10 - 1) * 100
    f["pc_ma20"] = (c[i - 1] / ma[20][i - 1] - 1) * 100
    f["c_m5_prev"] = (c[i - 1] / ma[5][i - 1] - 1) * 100
    f["ma10_slope"] = (m10 / ma[10][i - 1] - 1) * 100
    f["ma20_slope5"] = (m20 / ma[20][i - 5] - 1) * 100
    f["ma60_slope5"] = (m60 / ma[60][i - 5] - 1) * 100
    f["ma20_ma60"] = (m20 / m60 - 1) * 100
    f["ma5_ma20"] = (m5 / m20 - 1) * 100
    f["ma60_ma120"] = (m60 / m120 - 1) * 100 if m120 == m120 else 0.0
    f["m5_m10"] = (m5 / m10 - 1) * 100
    f["spread3"] = (max(m5, m10, m20) - min(m5, m10, m20)) / m20 * 100
    f["z"] = (c[i] - m20) / sd[i] if sd[i] else 0.0
    f["bw"] = 4 * sd[i] / m20 * 100
    f["bw_min10"] = min(4 * sd[j] / ma[20][j] * 100 for j in range(i - 9, i + 1))
    z5 = [(c[j] - ma[20][j]) / sd[j] for j in range(i - 4, i + 1) if sd[j]]
    f["z_min5"] = min(z5) if z5 else 0.0
    f["hup_max20"] = max((h[j] - (ma[20][j] + 2 * sd[j])) / ma[20][j] * 100 for j in range(i - 19, i + 1))
    f["below_cnt20"] = sum(1 for j in range(i - 19, i + 1) if c[j] <= ma[20][j])
    for window in (20, 60, 120):
        lo_index = i - window + 1
        hi = max(h[lo_index:i + 1])
        f[f"hiago{window}"] = i - next(k for k in range(lo_index, i + 1) if h[k] == hi)   # 平手算最早那天（跟對答案時一樣）
        if window in (20, 60):
            f[f"run{window}"] = (hi / min(l[lo_index:i + 1]) - 1) * 100
        if window in (60, 120):
            f[f"dd{window}"] = (c[i] / hi - 1) * 100
    f["ret3"] = (c[i] / c[i - 3] - 1) * 100
    f["vol"] = float(v[i])
    f["vol_prev"] = float(v[i - 1])
    v5 = sum(v[i - 5:i]) / 5
    v20 = sum(v[i - 20:i]) / 20
    f["v_v5"] = v[i] / v5 if v5 else 0.0
    f["v_v20"] = v[i] / v20 if v20 else 0.0
    f["v_pv"] = v[i] / v[i - 1] if v[i - 1] else 0.0
    f["vma5"] = sum(v[i - 4:i + 1]) / 5
    f["prev_black"] = c[i - 1] < o[i - 1]
    f["newhi_cnt3"] = sum(1 for j in range(i - 2, i + 1) if h[j] >= max(h[j - 19:j + 1]))
    f["h_gt_ph"] = h[i] > h[i - 1]
    f["h_ph"] = (h[i] / h[i - 1] - 1) * 100
    f["l_ge_pl"] = l[i] >= l[i - 1]
    f["l_pl"] = (l[i] / l[i - 1] - 1) * 100
    f["above5_cnt3"] = sum(1 for j in range(i - 2, i + 1) if c[j] > ma[5][j])
    f["ret3_min"] = min((c[j] / c[j - 1] - 1) * 100 for j in range(i - 2, i + 1))
    red = 0
    j = i
    while j > 0 and c[j] > o[j]:
        red += 1
        j -= 1
    f["consec_red"] = red
    f["close_pos"] = (c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0.5
    if m240 == m240:
        f["ma_score"] = sum(1 for a, short in enumerate(MA_PERIODS) for long in MA_PERIODS[a + 1:] if ma[short][i] > ma[long][i])
    else:
        f["ma_score"] = None
    return f


def evaluate(f: dict[str, Any], logic_keys: list[str] | None = None) -> list[str]:
    keys = logic_keys or [logic["key"] for logic in LOGICS]
    hits = []
    for key in keys:
        try:
            if LOGIC_BY_KEY[key]["rule"](f):
                hits.append(key)
        except (KeyError, TypeError, ZeroDivisionError):
            continue
    return hits


# ------------------------------------------------------------------ 資料

def _schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS grail_radar_runs (
            trade_date TEXT NOT NULL,
            logic TEXT NOT NULL,
            slot TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            source TEXT,
            stocks_json TEXT NOT NULL,
            PRIMARY KEY (trade_date, logic, slot)
        )"""
    )


def _futures_codes() -> set[str]:
    return {str(code).strip().upper() for code, _name in STOCK_GROUPS.get("股期標的", [])}


_group_by_code: dict[str, str] = {}


def _group_of(code: str) -> str:
    if not _group_by_code:
        for name, members in STOCK_GROUPS.items():
            if name in SPECIAL_GROUP_NAMES:
                continue
            for member_code, _stock_name in members:
                _group_by_code.setdefault(str(member_code).strip().upper(), name)
    return _group_by_code.get(code, "")


def _universe() -> dict[str, dict[str, str]]:
    """全市場個股（4 位數代號、不含 00 開頭的 ETF）：{代號: {name, market}}。"""
    with get_connection() as connection:
        rows = connection.execute("SELECT stock_code, stock_name, market FROM stocks").fetchall()
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        code = str(row["stock_code"] or "").strip().upper()
        market = str(row["market"] or "").strip().upper()
        if len(code) == 4 and code.isdigit() and not code.startswith("00") and market in ("TSE", "OTC"):
            out[code] = {"name": str(row["stock_name"] or code).strip() or code, "market": market}
    return out


def _load_bars(codes: list[str], *, until: str, include_until: bool) -> dict[str, dict[str, Any]]:
    """{代號: {last（最後一根的日期）, o, h, l, c, v}}，舊到新；until 那天要不要算進去看 include_until。
    全市場一年多的日K放在 array('d')（一個數 8 bytes），盤中整天留在記憶體裡也只要三十 MB 上下。"""
    since = (date.fromisoformat(until) - timedelta(days=HISTORY_CALENDAR_DAYS)).isoformat()
    op = "<=" if include_until else "<"
    wanted = set(codes)
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, open, high, low, close, volume FROM bars_1d
                WHERE substr(bar_time, 1, 10) >= ? AND substr(bar_time, 1, 10) {op} ?
                ORDER BY stock_code, bar_time""",
            (since, until),
        )
        for row in rows:
            code = str(row["stock_code"]).strip().upper()
            if code not in wanted:
                continue
            series = out.get(code)
            if series is None:
                series = out[code] = {"last": None, "o": array("d"), "h": array("d"), "l": array("d"), "c": array("d"), "v": array("d")}
            day = str(row["d"])
            if series["last"] == day:
                continue
            series["last"] = day
            series["o"].append(float(row["open"]))
            series["h"].append(float(row["high"]))
            series["l"].append(float(row["low"]))
            series["c"].append(float(row["close"]))
            series["v"].append(float(row["volume"] or 0))
    return out


def _shares(codes: list[str], before: str) -> dict[str, float]:
    """{代號: 發行股數}，用 before 之前最近一筆市值÷那天收盤（處置股預測收集的市值）；表不存在就回空。"""
    since = (date.fromisoformat(before) - timedelta(days=30)).isoformat()
    try:
        with get_connection() as connection:
            rows = connection.execute(
                """SELECT f.stock_code AS code, f.trade_date AS d, f.market_value AS mv, b.close AS c
                   FROM stock_fundamentals_daily f
                   JOIN bars_1d b ON b.stock_code = f.stock_code AND substr(b.bar_time, 1, 10) = f.trade_date
                   WHERE f.trade_date >= ? AND f.trade_date <= ? AND f.market_value > 0""",
                (since, before),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    latest: dict[str, tuple[str, float]] = {}
    for row in rows:
        code = str(row["code"]).strip().upper()
        if row["c"] and (code not in latest or str(row["d"]) > latest[code][0]):
            latest[code] = (str(row["d"]), float(row["mv"]) / float(row["c"]))
    wanted = set(codes)
    return {code: shares for code, (_d, shares) in latest.items() if code in wanted}


def _day_complete(day: str) -> bool:
    """官方日K那天到齊了沒：上市 500 檔以上、上櫃有前一個交易日的九成。"""
    prev = previous_trading_day(day).isoformat()
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT substr(b.bar_time, 1, 10) AS d, s.market AS m, COUNT(*) AS n FROM bars_1d b
               JOIN stocks s ON s.stock_code = b.stock_code
               WHERE substr(b.bar_time, 1, 10) IN (?, ?) GROUP BY d, m""",
            (day, prev),
        ).fetchall()
    counts = {(str(r["d"]), str(r["m"]).upper()): int(r["n"]) for r in rows}
    return counts.get((day, "TSE"), 0) >= TSE_DAY_MIN and counts.get((day, "OTC"), 0) >= max(100, counts.get((prev, "OTC"), 0) * OTC_DAY_RATIO)


# ------------------------------------------------------------------ 證交所 MIS 即時報價

def _num(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None


def _book(value: Any) -> float | None:
    return _num(str(value or "").split("_")[0])


def _default_fetcher(url: str) -> Any:
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        "User-Agent": "Mozilla/5.0 (compatible; HanStock/1.0)",
    })
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def fetch_live_bars(universe: dict[str, dict[str, str]], *, fetcher: Callable[[str], Any] | None = None) -> dict[str, dict[str, Any]]:
    """{代號: {date, time, open, high, low, close, volume(張), prevClose}}：今天到現在的K棒。
    沒成交過（沒有開盤價）的不回；現價沒有（z 是 "-"，例如漲停鎖死）用委買／委賣補，再不行用最高或最低。"""
    call = fetcher or _default_fetcher
    codes = sorted(universe)
    chunks = [codes[k:k + MIS_CHUNK] for k in range(0, len(codes), MIS_CHUNK)]

    def one(chunk: list[str]) -> list[dict[str, Any]]:
        channels = [f"{'tse' if universe[code]['market'] == 'TSE' else 'otc'}_{code}.tw" for code in chunk]
        params = urllib.parse.urlencode({"ex_ch": "|".join(channels), "json": "1", "delay": "0", "_": str(int(time.time() * 1000))})
        for attempt in range(2):
            try:
                payload = call(f"{MIS_URL}?{params}")
                return list((payload or {}).get("msgArray") or []) if isinstance(payload, dict) else []
            except Exception:  # noqa: BLE001
                if attempt:
                    raise
                time.sleep(1.5)
        return []

    out: dict[str, dict[str, Any]] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=MIS_WORKERS) as pool:
        for future in [pool.submit(one, chunk) for chunk in chunks]:
            try:
                items = future.result()
            except Exception:  # noqa: BLE001
                errors += 1
                continue
            for item in items:
                code = str(item.get("c") or "").strip().upper()
                opening = _num(item.get("o"))
                high, low = _num(item.get("h")), _num(item.get("l"))
                if not code or opening is None or high is None or low is None:
                    continue
                price = _num(item.get("z"))
                if price is None:
                    bid, ask = _book(item.get("b")), _book(item.get("a"))
                    price = bid if bid and not ask else ask if ask and not bid else round((bid + ask) / 2, 4) if bid and ask else None
                if price is None:
                    continue
                price = min(max(price, low), high)
                day = str(item.get("d") or "")
                out[code] = {
                    "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else None,
                    "time": str(item.get("t") or "") or None,
                    "open": opening, "high": high, "low": low, "close": price,
                    "volume": float(_num(item.get("v")) or 0), "prevClose": _num(item.get("y")),
                }
    if errors:
        logger.warning("飆股雷達 MIS 報價 %d/%d 段抓不到", errors, len(chunks))
    return out


# ------------------------------------------------------------------ 計算

_history_cache: dict[str, Any] = {"key": None, "bars": None, "universe": None, "shares": None}
_history_lock = threading.Lock()


def _intraday_history(day: str) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]], dict[str, float]]:
    """盤中用：今天以前的官方日K（一天只讀一次）。"""
    with _history_lock:
        if _history_cache["key"] != day:
            universe = _universe()
            bars = _load_bars(sorted(universe), until=day, include_until=False)
            _history_cache.update({"key": day, "universe": universe, "bars": bars, "shares": _shares(sorted(universe), day)})
        return _history_cache["universe"], _history_cache["bars"], _history_cache["shares"]


def _stock_row(code: str, name: str, f: dict[str, Any], price: float, volume: float, shares: dict[str, float]) -> dict[str, Any]:
    share_count = shares.get(code)
    return {
        "c": code, "n": name, "g": _group_of(code),
        "px": round(price, 2), "chg": round(f["chg"], 2), "vol": int(round(volume)),
        "m": f.get("ma_score"),
        "mcap": round(share_count * price / 1e8, 1) if share_count else None,
    }


def _evaluate_all(universe: dict[str, dict[str, str]], bars: dict[str, dict[str, Any]], logic_keys: list[str],
                  today: dict[str, dict[str, Any]] | None, shares: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    """today＝盤中K棒（接在 bars 後面）；None 表示 bars 最後一根就是要算的那天。"""
    futures = _futures_codes()
    result: dict[str, list[dict[str, Any]]] = {key: [] for key in logic_keys}
    for code, info in universe.items():
        series = bars.get(code)
        if not series or len(series["c"]) < MIN_BARS - (1 if today is not None else 0):
            continue
        o, h, l, c, v = series["o"], series["h"], series["l"], series["c"], series["v"]
        if today is not None:
            live = today.get(code)
            if not live:
                continue
            o, h, l, c, v = (o + array("d", [live["open"]]), h + array("d", [live["high"]]), l + array("d", [live["low"]]),
                             c + array("d", [live["close"]]), v + array("d", [live["volume"]]))
        f = compute_features(o, h, l, c, v)
        if f is None:
            continue
        f["futures"] = code in futures
        for key in evaluate(f, logic_keys):
            result[key].append(_stock_row(code, info["name"], f, c[-1], v[-1], shares))
    for rows in result.values():
        rows.sort(key=lambda row: row["c"])
    return result


def _save(day: str, slot: str, results: dict[str, list[dict[str, Any]]], source: str) -> None:
    computed_at = _now().isoformat(timespec="seconds")
    with get_connection() as connection:
        _schema(connection)
        connection.executemany(
            """INSERT INTO grail_radar_runs (trade_date, logic, slot, computed_at, source, stocks_json) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(trade_date, logic, slot) DO UPDATE SET computed_at = excluded.computed_at, source = excluded.source,
               stocks_json = excluded.stocks_json""",
            [(day, key, slot, computed_at, source, json.dumps(rows, ensure_ascii=False, separators=(",", ":"))) for key, rows in results.items()],
        )


def run_slot(slot: str, *, now: datetime | None = None, fetcher: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """盤中某個時間點：用 MIS 報價組今天的K棒，算這個時間點有排的邏輯。"""
    now = now or _now()
    day = now.date().isoformat()
    logic_keys = [logic["key"] for logic in LOGICS if slot in logic["times"]]
    if not logic_keys:
        return {"slot": slot, "skipped": "這個時間點沒有邏輯"}
    universe, bars, shares = _intraday_history(day)
    live = fetch_live_bars(universe, fetcher=fetcher)
    live = {code: bar for code, bar in live.items() if bar.get("date") == day}
    if len(live) < min(300, max(1, len(universe) // 2)):
        return {"slot": slot, "skipped": f"今天的即時報價只有 {len(live)} 檔，不算"}
    results = _evaluate_all(universe, bars, logic_keys, live, shares)
    _save(day, slot, results, "mis")
    return {"slot": slot, "date": day, "quotes": len(live), "counts": {key: len(rows) for key, rows in results.items()}}


def run_close(day: str, *, force: bool = False) -> dict[str, Any]:
    """收盤：官方日K到齊後用整根日K算全部邏輯（往日回推也是這個）。"""
    if not force and not _day_complete(day):
        return {"date": day, "skipped": "官方日K還沒到齊"}
    universe = _universe()
    bars = _load_bars(sorted(universe), until=day, include_until=True)
    bars = {code: series for code, series in bars.items() if series["last"] == day}   # 那天沒交易（停牌）的不算
    results = _evaluate_all(universe, bars, [logic["key"] for logic in LOGICS], None, _shares(sorted(universe), day))
    _save(day, CLOSE_SLOT, results, "official")
    return {"date": day, "stocks": len(bars), "counts": {key: len(rows) for key, rows in results.items()}}


def _saved_close_dates() -> set[str]:
    with get_connection() as connection:
        _schema(connection)
        rows = connection.execute("SELECT DISTINCT trade_date FROM grail_radar_runs WHERE slot = ?", (CLOSE_SLOT,)).fetchall()
    return {str(row["trade_date"]) for row in rows}


def _recent_days(days: int, today: date | None = None) -> list[str]:
    cursor = today or _now().date()
    wanted: list[str] = []
    while len(wanted) < days:
        cursor = previous_trading_day(cursor)
        wanted.append(cursor.isoformat())
    return sorted(wanted)


def backfill_close(days: int = BACKFILL_DAYS, *, today: date | None = None, recompute: bool = False) -> dict[str, Any]:
    """最近幾個交易日的「收盤」名單：還沒有的補算（剛上線、或重新部署前漏掉的）；recompute＝全部重算
    （日K修正後用，例如上櫃日K拿鏡像修過）。"""
    have = set() if recompute else _saved_close_dates()
    done = []
    for day in _recent_days(days, today):
        if day in have:
            continue
        result = run_close(day)
        if not result.get("skipped"):
            done.append(day)
    return {"filled": done, "recompute": recompute}


# ------------------------------------------------------------------ 給頁面

def day_payload(day: str | None = None) -> dict[str, Any]:
    initialize_database()
    with get_connection() as connection:
        _schema(connection)
        dates = [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM grail_radar_runs ORDER BY trade_date DESC LIMIT 60").fetchall()]
        day = day or (dates[0] if dates else _now().date().isoformat())
        rows = connection.execute(
            "SELECT logic, slot, computed_at, source, stocks_json FROM grail_radar_runs WHERE trade_date = ?", (day,)
        ).fetchall()
    runs: dict[str, dict[str, Any]] = {}
    updated = None
    for row in rows:
        stocks = json.loads(row["stocks_json"] or "[]")
        at = str(row["computed_at"])
        runs.setdefault(str(row["logic"]), {})[str(row["slot"])] = {
            "slot": str(row["slot"]), "at": at[11:19], "source": row["source"], "n": len(stocks), "stocks": stocks,
        }
        updated = max(updated or at, at)
    return {
        "status": "ok", "date": day, "dates": dates, "updated": updated, "saints": SAINTS,
        "logics": [{k: logic[k] for k in ("key", "saintId", "name", "kind", "times", "desc", "calibration")} for logic in LOGICS],
        "closeSlot": CLOSE_SLOT, "runs": runs,
        "note": "條件是用嗨投資名單反推的近似條件，跟嗨投資不會完全一樣；不是買賣建議。",
    }


# ------------------------------------------------------------------ 排程

_state: dict[str, Any] = {"lastSlot": None, "lastSlotAt": None, "lastClose": None, "lastError": None, "backfill": None}
_done_slots: dict[str, set[str]] = {}
_close_state: dict[str, Any] = {"date": None, "lastTry": 0.0, "done": False, "final": False}
_lock = threading.Lock()
_started = False


def _slot_minutes(slot: str) -> int:
    hour, minute = slot.split(":")
    return int(hour) * 60 + int(minute)


def due_slots(now: datetime, done: set[str]) -> list[str]:
    """現在該算的時間點：到了而且還沒算；盤中時間點過了 SLOT_GRACE_MINUTES 就不補，收盤後的時間點當天都補。
    13:25 那一輪提早 30 秒（13:25 起是收盤集合競價，證交所只揭示試撮價）。"""
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    out = []
    for slot in ALL_SLOTS:
        if slot in done:
            continue
        start = _slot_minutes(slot) * 60 - (30 if slot == "13:25" else 0)
        if seconds < start:
            continue
        if _slot_minutes(slot) <= _slot_minutes(AFTER_CLOSE_SLOT) and seconds > start + SLOT_GRACE_MINUTES * 60:
            continue
        out.append(slot)
    return out


def _tick(now: datetime) -> None:
    day = now.date().isoformat()
    if not is_trading_day(now):
        return
    done = _done_slots.setdefault(day, set())
    if not done:
        with get_connection() as connection:
            _schema(connection)
            done.update(str(r["slot"]) for r in connection.execute(
                "SELECT DISTINCT slot FROM grail_radar_runs WHERE trade_date = ? AND source = 'mis'", (day,)).fetchall())
    for slot in due_slots(now, done):
        try:
            result = run_slot(slot, now=now)
            _state.update({"lastSlot": result, "lastSlotAt": _now().isoformat(timespec="seconds"), "lastError": None})
            if not result.get("skipped"):
                done.add(slot)
        except Exception as error:  # noqa: BLE001
            _state["lastError"] = f"{slot}: {type(error).__name__}: {error}"[:300]
            logger.exception("飆股雷達 %s 失敗", slot)
    if (now.hour, now.minute) >= CLOSE_FROM:
        if _close_state["date"] != day:
            _close_state.update({"date": day, "lastTry": 0.0, "done": False, "final": False})
        final_due = (now.hour, now.minute) >= CLOSE_FINAL_AFTER and not _close_state["final"]
        if (not _close_state["done"] or final_due) and time.monotonic() - _close_state["lastTry"] >= CLOSE_RETRY_SECONDS:
            _close_state["lastTry"] = time.monotonic()
            result = run_close(day)
            _state["lastClose"] = result
            if not result.get("skipped"):
                _close_state["done"] = True
                if final_due:
                    _close_state["final"] = True


STARTUP_DELAY_SECONDS = 300   # 先讓日K收集器、上櫃鏡像核對跑一輪再回推往日名單


def _loop() -> None:
    time.sleep(STARTUP_DELAY_SECONDS)
    try:
        _state["backfill"] = backfill_close()
    except Exception as error:  # noqa: BLE001
        _state["backfill"] = {"error": f"{type(error).__name__}: {error}"[:300]}
        logger.exception("飆股雷達回推失敗")
    while True:
        try:
            _tick(_now())
        except Exception as error:  # noqa: BLE001
            _state["lastError"] = f"{type(error).__name__}: {error}"[:300]
            logger.exception("飆股雷達排程失敗")
        time.sleep(POLL_SECONDS)


def collector_status() -> dict[str, Any]:
    day = _now().date().isoformat()
    return {"enabled": _enabled(), "slots": ALL_SLOTS, "doneToday": sorted(_done_slots.get(day, set())),
            "closeState": {k: v for k, v in _close_state.items() if k != "lastTry"}, **_state}


def start_grail_radar_collector() -> bool:
    global _started
    with _lock:
        if _started or not _enabled():
            return False
        initialize_database()
        threading.Thread(target=_loop, name="hanstock-grail-radar", daemon=True).start()
        _started = True
        return True
