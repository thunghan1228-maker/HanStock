"""飆股雷達（2026-10-07 使用者：照莊爸 App 的「飆股雷達」做一個，條件我們自己算）。

莊爸的飆股雷達是在固定時間跑嗨投資「紫殺」學院的四個選股聖杯（穿山鱷龍／黑龍短沖／小資利器之黑飛舞／飛龍戰法），
共 15 個邏輯，把名單貼上網站。聖杯只給結果、不公開條件，這裡的條件是用使用者截圖的嗨投資一個月名單
（9/7～10/6，每檔顯示最早符合那天）加上莊爸網站的全天名單，對全市場日K反推出來的近似條件。
第二版（RULES_VERSION 2，2026-10-07 晚上）：使用者給了莊爸 10/07 的名單對答案，加上 10/01～10/06 四天一起重新校準
（CALIBRATION＝一個月名單＋五天全名單：嗨投資選的我們抓到幾成、我們選的有幾成在他們名單裡）。
主力籌碼（嗨投資的「主力買賣超」「1日σ／10日σ」）我們沒有全市場的資料，所以有幾個邏輯準度比較低。

時間點以莊爸網站的固定時點為主、每個都提早 10 分鐘（2026-10-08 使用者）：
波段（穿山鱷龍、飛龍戰法）莊爸 13:00、15:00 → 我們 12:50、14:50＋收盤；
隔日沖（黑龍短沖 4 個含 R劍、黑飛舞家族）莊爸 12:00、13:20、13:45 → 我們 11:50、13:10、13:35。
- 盤中時間點：證交所 MIS 即時報價（跟首頁同一個來源）組出全市場今天到那一刻的K棒（開、高、低、現價、累積張數），
  接在官方日K後面算條件；那個時間點過了 SLOT_GRACE_MINUTES 分鐘還沒算到（例如剛好重新部署）就不補，留空。
  13:30 收盤以後的時間點（13:35、14:50）用的是收盤後的最後報價，晚一點算也一樣，所以當天都可以補。
- 收盤：官方日K（上市證交所、上櫃鏡像／Yahoo）到齊以後再用整根日K算一次「收盤」，往日名單也是這樣回推的。
名單上的均線分數跟莊爸一樣用「前一交易日收盤」的官網式 15 分（heilong_daily.score2，籌碼暴增雷達也是這套）。
條件改版（RULES_VERSION 變了）重新啟動時，存著的「收盤」名單全部用新條件重算。
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
RULES_VERSION = 3               # 條件改了就加一：重新啟動時存著的「收盤」名單全部重算
# 2026-10-08 使用者：以莊爸的時點為主，我們每個時點都提早 10 分鐘（他 12:00 我們 11:50、他 13:20 我們 13:10，以此類推）
SWING_TIMES = ["12:50", "14:50"]               # 波段：莊爸 13:00、15:00 → 12:50、14:50（14:50 用收盤後的最後報價），另有官方日K的「收盤」
OVERNIGHT_TIMES = ["11:50", "13:10", "13:35"]  # 隔日沖：莊爸 12:00、13:20、13:45 → 11:50、13:10、13:35

SAINTS = [
    {"id": 57, "name": "穿山鱷龍"},
    {"id": 32, "name": "黑龍短沖"},
    {"id": 24, "name": "小資利器之黑飛舞"},
    {"id": 30, "name": "飛龍戰法"},
]


def _cross2022(f: dict[str, Any]) -> bool:
    return (f["pc_ma20"] <= 0 and f["c_ma20"] > 0 and f["c_ma5"] >= 0.75 and f["c_ma120"] >= 10.4 and f["dd60"] >= -15.5
            and f["run60"] >= 25 and f["ma60_slope5"] >= -0.8 and f["vol"] >= 1000 and f["h_ma20"] >= 1.8 and f["hiago60"] <= 28
            and f["c_ma240"] <= 100 and f["range"] >= 2.86 and f["v_v5"] <= 6.1 and f["ma60_slope"] >= 0 and f["bw"] <= 29.3
            and f["c_ma60"] >= 4.18)


def _breakred(f: dict[str, Any]) -> bool:
    return (f["body"] >= 0.2 and f["l_pl"] <= -0.1 and f["l_ma20"] <= 0 and f["h_ma20"] >= -1.6 and f["c_ma20"] <= 2.97
            and f["ma20_ma60"] >= 1.4 and f["c_m5_prev"] <= 0.4 and f["c_ma240"] >= 2.8 and f["dd60"] >= -15.4
            and f["vma5"] >= 262.5 and f["l_ma5"] <= -1.05 and f["chg"] >= -0.55 and f["close_pos"] >= 0.6
            and f["below_cnt20"] <= 12)


def _crossconv(f: dict[str, Any]) -> bool:
    return (-9 <= f["c_ma20"] <= -0.4 and f["h_ma20"] >= -2.5 and f["ma20_ma60"] >= 1.8 and f["m5_m10"] <= -0.5
            and f["ma10_slope"] <= -0.1 and f["v_pv"] <= 0.8 and f["v_v5"] <= 1.1 and f["v_v20"] <= 0.405 and f["vma5"] >= 280
            and f["dd120"] >= -15.5 and f["ret3"] <= 0.55 and f["run60"] <= 31 and f["ma20_slope5"] <= 1)


def _rsword(f: dict[str, Any]) -> bool:
    return (f["hiago20"] == 0 and f["ushadow"] >= 2.062 and f["body"] >= 0.1 and f["chg"] <= 5 and f["c_ma20"] >= 5
            and f["ma20_slope5"] >= -2.1 and f["vol"] >= 1000)


def _xdragon(f: dict[str, Any]) -> bool:
    return (f["chg"] < 0 and f["black"] and f["c_m5_prev"] >= 4.5 and f["ma5_slope"] >= 0.95 and f["l_ma5"] >= -0.2
            and f["hiago20"] <= 1 and f["range"] >= 3.6 and f["lshadow"] <= 3 and f["ma20_ma60"] >= -0.2
            and f["ma20_slope5"] >= 0.1 and f["c_ma60"] >= 4.2 and f["vol"] >= 1900)


def _panther(f: dict[str, Any]) -> bool:
    return (f["hiago120"] == 0 and f["body"] <= -1.8 and f["h_ph"] >= 2 and f["c_m5_prev"] >= 8.98 and f["ma5_ma20"] <= 29.7
            and f["vol"] >= 4500 and f["c_ma20"] <= 31.4)


def _shadow(f: dict[str, Any]) -> bool:
    return (f["lshadow"] >= 2.6 and not f["l_ge_pl"] and f["body"] <= 2.6 and f["chg"] >= -3 and f["c_ma5"] <= 1.8
            and f["z_min5"] >= 0.7 and f["c_ma60"] >= 8.6 and f["vol"] >= 950 and f["chg_max4"] >= 0.34 and f["v_v20"] >= 0.45
            and f["ma20_ma60"] <= 27.1 and f["ma5_slope"] <= 3)


def _bfw2021(f: dict[str, Any]) -> bool:
    return (f["prev_black"] and f["c_m5_prev"] >= 2.2 and f["hiago60"] <= 1 and f["newhi_cnt3"] >= 2 and not f["h_gt_ph"]
            and f["v_pv"] <= 1.1 and f["body"] >= -5.5 and f["vol_prev"] >= 5000 and f["ret3"] >= 0.2 and f["run20"] >= 10
            and f["l_ma10"] <= 18 and f["hup_max20"] <= 16.27 and f["bw"] <= 62.605 and f["bw_min10"] >= 6.6
            and f["h_ma20"] >= 9.1 and f["above5_cnt3"] >= 3)


def _bfw905(f: dict[str, Any]) -> bool:
    return (f["prev_black"] and f["hiago120"] <= 1 and f["vol_prev"] >= 5548 and f["hup_max20"] >= 8.1 and f["ma60_ma120"] >= -0.2
            and f["l_ma5"] >= -3.1 and f["below_cnt20"] <= 10 and f["vol"] <= 8420 and f["bw_min10"] <= 77.5 and f["body"] <= 6.5)


def _swordfw(f: dict[str, Any]) -> bool:
    return (f["pc_ma20"] >= 9.9 and f["v_pv"] <= 0.352 and not f["l_ge_pl"] and f["above5_cnt3"] == 3 and f["bw"] <= 71.8
            and f["vol"] >= 900 and f["body"] <= 2.8 and f["chg_2"] >= 4.9)


def _bfwfut(f: dict[str, Any]) -> bool:
    return (f["futures"] and f["newhi_cnt3"] >= 2 and f["h_ph"] <= -0.2 and f["bigred_cnt3"] >= 1 and f["v_v5"] <= 0.973
            and f["v_v20"] >= 0.3 and f["ret3_min"] >= -4.5 and f["chg_prev"] <= 3.7 and f["run60"] >= 40.5
            and f["hup_max20"] >= 3.3 and f["below_cnt10"] <= 2 and f["vma5"] >= 1748.8 and f["c_ma60"] >= 4.5)


def _fly3(f: dict[str, Any]) -> bool:
    return (f["nh60_2"] and f["launch_chg"] >= 4.5 and f["chg_prev"] <= 3.3 and f["ret3"] <= 12 and f["above5_cnt3"] == 3
            and f["vma5"] >= 297 and f["chg_3"] >= -0.5)


def _flyburst(f: dict[str, Any]) -> bool:
    return (f["hiago120"] == 0 and f["chg"] >= 4.8 and f["v_v5"] >= 2.5 and f["hup_max20"] <= 11 and f["bw_min10"] <= 24.2
            and f["vol"] >= 3150 and f["ma20_ma60"] >= 2 and f["c_m5_prev"] >= 1.5 and f["c_ma120"] <= 47.81)


def _flybreak(f: dict[str, Any]) -> bool:
    return (f["hiago60"] == 0 and f["chg"] >= 5.7 and f["close_pos"] >= 0.4 and f["chg_prev"] <= 3.5 and f["bw"] <= 25
            and f["c_ma120"] >= 7.6 and f["c_ma240"] >= 14.2 and f["ma60_slope"] >= 0.1 and f["vol_prev"] >= 64
            and f["range"] <= 11.3 and f["chg_2"] >= -2.5 and f["hiago120"] <= 118 and f["c_m5_prev"] <= 7.3)


def _red3(f: dict[str, Any]) -> bool:
    return (f["consec_red"] >= 3 and f["c_ma5"] >= 1.2 and f["c_ma20"] >= 5 and f["spread3"] <= 5.8
            and 0.2 <= f["close_pos"] <= 0.98 and f["chg_prev"] <= 12.6 and f["chg_max4"] <= 8.5 and f["dd60"] <= -1.6
            and f["c_ma240"] >= -1 and f["ma60_ma120"] >= -8.4 and f["vol"] >= 900 and f["z_min5"] <= 0.4 and f["ushadow"] <= 3.85
            and f["run20"] >= 11)


# 名稱照莊爸網站；時間點＝莊爸的固定時點提早 10 分鐘（波段 12:50、14:50＋收盤，隔日沖 11:50／13:10／13:35；R劍照他現在的頁面算隔日沖）；
# desc 是我們反推的條件（給頁面「條件說明」用）；calibration＝第三版對答案（一個月嗨投資名單＋莊爸 10/01～10/08 六天全名單）。
# 第三版（2026-10-08）：從第二版出發重調，門檻往寬的方向留邊（分數幾乎不掉就放寬，不卡在某一天剛好的邊上）；
# 輪流拿掉一天調、用那天驗：六天平均抓到莊爸 79%、我們名單 79% 跟他一樣（第二版在沒看過的 10/08 是 73%／60%）。
LOGICS: list[dict[str, Any]] = [
    {"key": "cross2022", "saintId": 57, "name": "波段穿惡2022版本", "kind": "波段", "rule": _cross2022, "times": SWING_TIMES,
     "desc": "穿惡：昨天收在月線（20日線）下、今天收盤站回月線，收在 5 日線上 0.75% 以上、最高超過月線 1.8%、振幅 2.86% 以上；"
             "60 日高點在最近 28 天內、前 60 天高低差 25% 以上（第一波）、離 60 日高點回檔 15.5% 以內；"
             "收盤比季線高 4.18%、比半年線高 10.4% 以上、但沒超過年線一倍；季線沒有往下彎；布林帶寬 29.3% 以內；"
             "量 1000 張以上、不到 5 日均量 6.1 倍。",
     "calibration": {"recall": 86, "precision": 90}},
    {"key": "breakred", "saintId": 57, "name": "即將突破惡(紅)", "kind": "波段", "rule": _breakred, "times": SWING_TIMES,
     "desc": "回測月線收紅：今天收紅K（收比開高 0.2% 以上）、跌幅 0.55% 以內、收在當天振幅 6 成以上；"
             "低點跌破昨天低點、跌破 5 日線 1.05% 以上、碰到月線，最高不低於月線 1.6%、收盤在月線 +2.97% 以內；"
             "昨天收盤在 5 日線 +0.4% 以內；"
             "月線比季線高 1.4% 以上、收盤比年線高 2.8% 以上、離 60 日高點 15.4% 以內、近 20 天收在月線下最多 12 天；"
             "5 日均量 262.5 張以上。",
     "calibration": {"recall": 75, "precision": 73}},
    {"key": "crossconv", "saintId": 57, "name": "即將穿惡(收斂)", "kind": "波段", "rule": _crossconv, "times": SWING_TIMES,
     "desc": "月線下量縮收斂：收在月線下 0.4%～9%、最高離月線不到 2.5%；"
             "5 日線在 10 日線下 0.5% 以上、10 日線往下彎、月線 5 天內漲不到 1%；"
             "量比昨天縮到 8 成以下、不到 5 日均量 1.1 倍、不到 20 日均量 40.5%（5 日均量 280 張以上）；"
             "月線比季線高 1.8% 以上、離 120 日高點 15.5% 以內、60 天高低差 31% 以內、近 3 天漲不到 0.55%。",
     "calibration": {"recall": 62, "precision": 100}},
    {"key": "rsword", "saintId": 32, "name": "隔日沖-R劍", "kind": "隔日沖", "rule": _rsword, "times": OVERNIGHT_TIMES,
     "desc": "創 20 日新高的紅K長上影（R劍）：今天最高是 20 日新高、上影線 2.06% 以上、收紅、漲幅 5% 以內；"
             "收盤比月線高 5% 以上、月線 5 天內下彎不到 2.1%；量 1000 張以上。",
     "calibration": {"recall": 100, "precision": 97}},
    {"key": "xdragon", "saintId": 32, "name": "隔日沖-極限黑龍", "kind": "隔日沖", "rule": _xdragon, "times": OVERNIGHT_TIMES,
     "desc": "強勢股換手收黑：今天收黑K而且下跌、低點最多跌破 5 日線 0.2%、振幅 3.6% 以上、下影線 3% 以內；"
             "昨天收盤在 5 日線上 4.5% 以上、5 日線還在往上（比昨天高 0.95% 以上）、20 日新高在今天或昨天；"
             "月線最多比季線低 0.2% 而且 5 天內往上、收盤比季線高 4.2% 以上；量 1900 張以上。",
     "calibration": {"recall": 97, "precision": 88}},
    {"key": "panther", "saintId": 32, "name": "超黑豹2023版", "kind": "隔日沖", "rule": _panther, "times": OVERNIGHT_TIMES,
     "desc": "創 120 日新高的長黑：今天最高是 120 日新高、比昨天最高再高 2% 以上，但收長黑（收比開低 1.8% 以上）；"
             "昨天收盤在 5 日線上 8.98% 以上、5 日線離月線 29.7% 以內、收盤離月線 31.4% 以內；"
             "量 4500 張以上。",
     "calibration": {"recall": 100, "precision": 100}},
    {"key": "shadow", "saintId": 32, "name": "隔日沖-神下影", "kind": "隔日沖", "rule": _shadow, "times": OVERNIGHT_TIMES,
     "desc": "強勢股長下影：下影線 2.6% 以上、低點跌破昨天低點、實體 2.6% 以內、跌幅 3% 以內、收盤離 5 日線 1.8% 以內、5 日線一天漲不到 3%；"
             "近 5 天收盤都在月線上方（至少 0.7 個標準差）、近 4 天有一天漲 0.34% 以上、收盤比季線高 8.6% 以上、月線離季線 27.1% 以內；"
             "量 950 張以上、有 20 日均量 45%。",
     "calibration": {"recall": 89, "precision": 89}},
    {"key": "bfw2021", "saintId": 24, "name": "黑飛舞小波段2021版", "kind": "隔日沖", "rule": _bfw2021, "times": OVERNIGHT_TIMES,
     "desc": "黑飛舞：昨天收黑K而且大量（5000 張以上），但收盤還在 5 日線上 2.2% 以上；"
             "今天最高沒超過昨天、量最多是昨天 1.1 倍、不是長黑（收比開低 5.5% 以內）；"
             "60 日新高在今天或昨天、近 3 天有 2 天創 20 日新高、近 3 天都收在 5 日線上、3 天漲 0.2% 以上；"
             "最高比月線高 9.1% 以上、低點離 10 日線 18% 以內；"
             "20 天高低差 10% 以上、近 20 天最多超出布林上緣 16.27%、布林帶寬 62.6% 以內、近 10 天最窄帶寬 6.6% 以上。",
     "calibration": {"recall": 91, "precision": 100}},
    {"key": "bfw905", "saintId": 24, "name": "黑飛舞905", "kind": "隔日沖", "rule": _bfw905, "times": OVERNIGHT_TIMES,
     "desc": "黑飛舞 905：昨天收黑K而且大量（5548 張以上）、120 日新高在今天或昨天；"
             "今天量 8420 張以內、實體紅K 6.5% 以內；"
             "近 20 天曾超出布林上緣 8.1% 以上、近 10 天最窄帶寬 77.5% 以內；季線最多比半年線低 0.2%；"
             "今天低點離 5 日線 3.1% 以內；近 20 天收在月線下的日子 10 天以內。",
     "calibration": {"recall": 80, "precision": 100}},
    {"key": "swordfw", "saintId": 24, "name": "隔日沖-劍飛舞", "kind": "隔日沖", "rule": _swordfw, "times": OVERNIGHT_TIMES,
     "desc": "劍飛舞：兩天前漲 4.9% 以上、昨天收盤比月線高 9.9% 以上（很強）；"
             "今天量急縮到昨天 35.2% 以下、低點跌破昨天低點、實體 2.8% 以內，但近 3 天都收在 5 日線上；"
             "布林帶寬 71.8% 以內；量 900 張以上。",
     "calibration": {"recall": 97, "precision": 100}},
    {"key": "bfwfut", "saintId": 24, "name": "黑飛舞(股期)", "kind": "隔日沖", "rule": _bfwfut, "times": OVERNIGHT_TIMES,
     "desc": "有股票期貨的股票：近 3 天有 2 天創 20 日新高、今天最高比昨天低 0.2% 以上（不再創高）、近 3 天有一根 3% 以上的紅K；"
             "量不到 5 日均量 97.3% 但有 20 日均量 3 成、5 日均量 1748.8 張以上；"
             "近 3 天單日跌幅都在 4.5% 以內、昨天漲 3.7% 以內；"
             "收盤比季線高 4.5% 以上、60 天高低差 40.5% 以上、近 20 天曾衝出布林上緣 3.3% 以上、近 10 天最多 2 天收在月線下。",
     "calibration": {"recall": 85, "precision": 100}},
    {"key": "fly3", "saintId": 30, "name": "三日飛龍", "kind": "波段", "rule": _fly3, "times": SWING_TIMES,
     "desc": "三日飛龍：兩天前創 60 日新高，而且兩、三天前有一天漲 4.5% 以上（發動）、三天前沒跌超過 0.5%；"
             "之後整理：昨天漲 3.3% 以內、3 天漲幅 12% 以內、近 3 天都收在 5 日線上；5 日均量 297 張以上。",
     "calibration": {"recall": 84, "precision": 79}},
    {"key": "flyburst", "saintId": 30, "name": "飛龍紅爆2022版", "kind": "波段", "rule": _flyburst, "times": SWING_TIMES,
     "desc": "飛龍紅爆：今天最高是 120 日新高、漲 4.8% 以上、爆量（5 日均量 2.5 倍以上、3150 張以上）；"
             "昨天收盤在 5 日線上 1.5% 以上、月線比季線高 2% 以上、收盤離半年線 47.81% 以內；"
             "近 10 天布林帶寬曾縮到 24.2% 以內（從整理區噴出）、近 20 天最多超出布林上緣 11%。",
     "calibration": {"recall": 100, "precision": 100}},
    {"key": "flybreak", "saintId": 30, "name": "飛龍突破2023版", "kind": "波段", "rule": _flybreak, "times": SWING_TIMES,
     "desc": "飛龍突破：今天最高是 60 日新高、漲 5.7% 以上（漲停也算）、振幅 11.3% 以內、收在當天振幅 4 成以上；"
             "昨天漲 3.5% 以內、昨收離 5 日線 7.3% 以內、兩天前跌幅 2.5% 以內；"
             "布林帶寬 25% 以內（從整理區突破）；"
             "收盤比半年線高 7.6%、比年線高 14.2% 以上、季線往上（另有兩條很寬的條件：昨天有量、120 日高點不在最舊那一天）。",
     "calibration": {"recall": 100, "precision": 96}},
    {"key": "red3", "saintId": 30, "name": "三紅劍小波段", "kind": "波段", "rule": _red3, "times": SWING_TIMES,
     "desc": "三紅劍：連續 3 天以上收紅K、收在 5 日線上 1.2%、月線上 5% 以上；"
             "5/10/20 日線糾結在 5.8% 以內、近 5 天有一天收盤離月線不到 0.4 個標準差（剛從月線附近上來）；"
             "收在當天振幅 2 成～98%、上影線 3.85% 以內、昨天漲 12.6% 以內、近 4 天沒有單日漲 8.5% 以上；"
             "20 天高低差 11% 以上、離 60 日高點至少回 1.6%、收盤最多低於年線 1%、季線沒有比半年線低超過 8.4%；"
             "量 900 張以上。",
     "calibration": {"recall": 88, "precision": 91}},
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
    # 第二版加的：收黑、5 日線／季線一天的斜率、前兩三天的漲幅（三日飛龍的發動日）、近 3 天 3% 以上紅K、近 10 天收在月線下幾天
    f["black"] = c[i] < o[i]
    f["ma5_slope"] = (m5 / ma[5][i - 1] - 1) * 100
    f["ma60_slope"] = (m60 / ma[60][i - 1] - 1) * 100
    f["chg_2"] = (c[i - 2] / c[i - 3] - 1) * 100
    f["chg_3"] = (c[i - 3] / c[i - 4] - 1) * 100
    f["launch_chg"] = max(f["chg_2"], f["chg_3"])
    f["chg_max4"] = max(f["chg"], f["chg_prev"], f["chg_2"], f["chg_3"])
    f["nh60_2"] = h[i - 2] >= max(h[i - 61:i - 1])     # 兩天前那根創 60 日新高
    f["bigred_cnt3"] = sum(1 for j in range(i - 2, i + 1) if o[j] > 0 and (c[j] / o[j] - 1) * 100 >= 3)
    f["below_cnt10"] = sum(1 for j in range(i - 9, i + 1) if c[j] <= ma[20][j])
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
    connection.execute("CREATE TABLE IF NOT EXISTS grail_radar_meta (key TEXT PRIMARY KEY, value TEXT)")


def _meta_get(key: str) -> str | None:
    with get_connection() as connection:
        _schema(connection)
        row = connection.execute("SELECT value FROM grail_radar_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def _meta_set(key: str, value: str) -> None:
    with get_connection() as connection:
        _schema(connection)
        connection.execute("INSERT INTO grail_radar_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                           (key, value))


def _prev_scores(day: str) -> dict[str, int]:
    """{代號: 均線分數}：day 之前最近一個交易日收盤的官網式 15 分（heilong_daily.score2）。
    跟莊爸一樣盤中、收盤、往日都看前一天收盤那一版；黑龍表還沒建（或還沒有那天）就回空，名單上顯示「—」。"""
    try:
        with get_connection() as connection:
            row = connection.execute("SELECT MAX(trade_date) AS d FROM heilong_daily WHERE trade_date < ?", (day,)).fetchone()
            prev = row["d"] if row else None
            if not prev:
                return {}
            rows = connection.execute("SELECT stock_code, score2 FROM heilong_daily WHERE trade_date = ?", (prev,)).fetchall()
    except Exception:  # noqa: BLE001  （黑龍表不存在）
        return {}
    return {str(r["stock_code"]).strip().upper(): int(r["score2"]) for r in rows if r["score2"] is not None}


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
    upper = f"{until}z" if include_until else until   # bar_time 以日期開頭：< 'Dz' 就是 <= D 那天
    wanted = set(codes)
    out: dict[str, dict[str, Any]] = {}
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT stock_code, substr(bar_time, 1, 10) AS d, open, high, low, close, volume FROM bars_1d
                WHERE bar_time >= ? AND bar_time < ?
                ORDER BY stock_code, bar_time""",
            (since, upper),
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
                   JOIN bars_1d b ON b.stock_code = f.stock_code AND b.bar_time >= f.trade_date AND b.bar_time < f.trade_date || 'z'
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
               WHERE (b.bar_time >= ? AND b.bar_time < ? || 'z') OR (b.bar_time >= ? AND b.bar_time < ? || 'z') GROUP BY d, m""",
            (day, day, prev, prev),
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

_history_cache: dict[str, Any] = {"key": None, "bars": None, "universe": None, "shares": None, "scores": None}
_history_lock = threading.Lock()


def _intraday_history(day: str) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]], dict[str, float], dict[str, int]]:
    """盤中用：今天以前的官方日K、股數、前一天的均線分數（一天只讀一次；均線分數還沒有就下一輪再讀）。"""
    with _history_lock:
        if _history_cache["key"] != day:
            universe = _universe()
            bars = _load_bars(sorted(universe), until=day, include_until=False)
            _history_cache.update({"key": day, "universe": universe, "bars": bars, "shares": _shares(sorted(universe), day),
                                   "scores": None})
        if not _history_cache["scores"]:
            _history_cache["scores"] = _prev_scores(day)
        return _history_cache["universe"], _history_cache["bars"], _history_cache["shares"], _history_cache["scores"]


def _stock_row(code: str, name: str, f: dict[str, Any], price: float, volume: float, shares: dict[str, float],
               scores: dict[str, int]) -> dict[str, Any]:
    share_count = shares.get(code)
    return {
        "c": code, "n": name, "g": _group_of(code),
        "px": round(price, 2), "chg": round(f["chg"], 2), "vol": int(round(volume)),
        "m": scores.get(code),     # 前一交易日收盤的均線分數（跟莊爸一樣）
        "mcap": round(share_count * price / 1e8, 1) if share_count else None,
    }


def _evaluate_all(universe: dict[str, dict[str, str]], bars: dict[str, dict[str, Any]], logic_keys: list[str],
                  today: dict[str, dict[str, Any]] | None, shares: dict[str, float],
                  scores: dict[str, int] | None = None) -> dict[str, list[dict[str, Any]]]:
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
            result[key].append(_stock_row(code, info["name"], f, c[-1], v[-1], shares, scores or {}))
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
    universe, bars, shares, scores = _intraday_history(day)
    live = fetch_live_bars(universe, fetcher=fetcher)
    live = {code: bar for code, bar in live.items() if bar.get("date") == day}
    if len(live) < min(300, max(1, len(universe) // 2)):
        return {"slot": slot, "skipped": f"今天的即時報價只有 {len(live)} 檔，不算"}
    results = _evaluate_all(universe, bars, logic_keys, live, shares, scores)
    _save(day, slot, results, "mis")
    return {"slot": slot, "date": day, "quotes": len(live), "counts": {key: len(rows) for key, rows in results.items()}}


def run_close(day: str, *, force: bool = False) -> dict[str, Any]:
    """收盤：官方日K到齊後用整根日K算全部邏輯（往日回推也是這個）。"""
    if not force and not _day_complete(day):
        return {"date": day, "skipped": "官方日K還沒到齊"}
    universe = _universe()
    bars = _load_bars(sorted(universe), until=day, include_until=True)
    bars = {code: series for code, series in bars.items() if series["last"] == day}   # 那天沒交易（停牌）的不算
    results = _evaluate_all(universe, bars, [logic["key"] for logic in LOGICS], None, _shares(sorted(universe), day),
                            _prev_scores(day))
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
    （日K修正後用，例如上櫃日K拿鏡像修過；條件改版時連更早存著的「收盤」也一起重算）。"""
    saved = _saved_close_dates()
    targets = set(_recent_days(days, today))
    if recompute:
        targets |= saved
    have = set() if recompute else saved
    done = []
    for day in sorted(targets):
        if day in have:
            continue
        result = run_close(day)
        if not result.get("skipped"):
            done.append(day)
    return {"filled": done, "recompute": recompute}


# ------------------------------------------------------------------ 給頁面

def day_payload(day: str | None = None) -> dict[str, Any]:
    initialize_database()
    now = _now()
    today = now.date().isoformat()
    with get_connection() as connection:
        _schema(connection)
        dates = [str(r["trade_date"]) for r in connection.execute(
            "SELECT DISTINCT trade_date FROM grail_radar_runs ORDER BY trade_date DESC LIMIT 60").fetchall()]
        # 2026-10-08 使用者：今天第一輪 12:00 才算，早上打開卻停在昨天。交易日一律預設看今天（還沒有名單就是空的），
        # 頁面另外寫下一輪幾點；往日照樣可以從日期選。
        if is_trading_day(now) and today not in dates:
            dates.insert(0, today)
        day = day or (dates[0] if dates else today)
        rows = connection.execute(
            "SELECT logic, slot, computed_at, source, stocks_json FROM grail_radar_runs WHERE trade_date = ?", (day,)
        ).fetchall()
    runs: dict[str, dict[str, Any]] = {}
    updated = None
    for row in rows:
        logic = LOGIC_BY_KEY.get(str(row["logic"]))
        if logic is None or (str(row["slot"]) != CLOSE_SLOT and str(row["slot"]) not in logic["times"]):
            continue   # 改時間點以前留下的盤中輪次（例如 10/07 舊時間點）不給頁面
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
        "nextSlot": _next_slot(now) if day == today and is_trading_day(now) else None,
        "note": "條件是用嗨投資名單反推的近似條件，跟嗨投資不會完全一樣；不是買賣建議。",
    }


def _next_slot(now: datetime) -> str | None:
    """今天還沒到的下一個時間點（都過了就是「收盤」，14:30 後官方日K到齊才算）。"""
    minutes = now.hour * 60 + now.minute
    upcoming = [slot for slot in ALL_SLOTS if _slot_minutes(slot) > minutes]
    return upcoming[0] if upcoming else (CLOSE_SLOT if (now.hour, now.minute) < CLOSE_FINAL_AFTER else None)


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
    """現在該算的時間點：到了而且還沒算；盤中時間點過了 SLOT_GRACE_MINUTES 就不補，收盤後的時間點當天都補。"""
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    out = []
    for slot in ALL_SLOTS:
        if slot in done:
            continue
        start = _slot_minutes(slot) * 60
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
        outdated = _meta_get("rules_version") != str(RULES_VERSION)   # 條件改版：存著的「收盤」名單全部重算
        _state["backfill"] = backfill_close(recompute=outdated)
        if outdated:
            _meta_set("rules_version", str(RULES_VERSION))
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
