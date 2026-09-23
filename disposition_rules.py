"""證交所《公布或通知注意交易資訊暨處置作業要點》第四條14款異常標準判定（115.08.03版，
2026/8/10新制）。門檻數字取自官方原文：

  - 要點本文：twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007225
  - 第四條異常標準之詳細數據及除外情形：同站 FID=FL007226

只實作用HanStock可取得的資料算得出來的款：
  一(6日累積漲跌%)、二(30/60/90日起迄兩營業日漲跌%)、三(一款+成交量異常放大)、
  四(一款+當日週轉率過高)、六(本益比/股價淨值比異常+週轉率，不含分點/投資人集中度子
  條件)、七(一款+券資比明顯放大)、九(成交量異常放大，無需價格條件)、十(累積週轉率過
  高，無需價格條件)、十一(起迄兩營業日收盤價價差)。

不實作：
  五(需要券商分點當日買賣金額，一般管道拿不到)、六的分點/投資人集中度子條件(同上)、
  八(限台灣存託憑證TDR，不在追蹤範圍)、十二(借券賣出量，目前沒有資料源)、十三(當日
  沖銷成交量比例，目前沒有資料源——這一款也是官方規則裡唯一決定處置期間5天/7天的
  款，所以本模組只推算「會不會」被列注意/處置，不推算處置期間長短)。

每個判定函式對應「本要點第四條異常標準之詳細數據」裡的一條，函式開頭的註解引用該條
文號方便日後對照官方文字校正；輸入一律是已經算好的數字（ClauseInputs），不在這裡查
資料庫或呼叫任何外部服務，方便單純用假資料測試每一條門檻的邊界值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ClauseInputs:
    """單一股票、單一交易日，判定各款需要的計算好的數字；None代表資料不存在或期間不足
    （例如還沒有60個營業日的歷史、沒有PE/PBR），該款當作不成立，不是當作0。"""

    code: str
    close: float
    volume: float | None = None  # 當日成交量（張）
    turnover_amount: float | None = None  # 當日成交金額（元）

    # ---- 第一款：最近6營業日(含當日)累積收盤價漲跌% ----
    change_6d_pct: float | None = None
    change_6d_peer_avg_pct: float | None = None  # 全體有價證券同期間平均值
    change_6d_industry_avg_pct: float | None = None  # 同類有價證券同期間平均值（同類<5檔為None）
    price_diff_6d: float | None = None  # 最近6營業日起迄收盤價"價差"絕對值（新台幣）

    # ---- 第二款：起迄兩營業日漲跌%（30/60/90營業日三個窗口） ----
    change_2d_30d_pct: float | None = None
    change_2d_30d_peer_avg_pct: float | None = None
    change_2d_60d_pct: float | None = None
    change_2d_60d_peer_avg_pct: float | None = None
    change_2d_90d_pct: float | None = None
    change_2d_90d_peer_avg_pct: float | None = None
    close_above_open_ref: bool | None = None  # 當日收盤價 > 當日開盤參考價

    # ---- 第三/九款：成交量放大倍數 ----
    volume_ratio_60d: float | None = None  # 當日成交量 / 最近60營業日日均量
    volume_ratio_60d_peer_avg: float | None = None
    avg_volume_ratio_6d_60d: float | None = None  # 最近6營業日日均量 / 最近60營業日日均量
    avg_volume_ratio_6d_60d_peer_avg: float | None = None

    # ---- 第四/十款：週轉率 ----
    turnover_pct: float | None = None  # 當日週轉率%
    turnover_pct_peer_avg: float | None = None
    cum_turnover_6d_pct: float | None = None  # 最近6營業日累積週轉率%
    cum_turnover_6d_peer_avg_pct: float | None = None

    # ---- 第六款：本益比/股價淨值比 ----
    pe_ratio: float | None = None
    pe_ratio_peer_avg: float | None = None  # 全體依發行單位數加權平均本益比
    pbr: float | None = None
    pbr_peer_avg: float | None = None
    pbr_industry_avg: float | None = None

    # ---- 第七款：券資比 ----
    short_margin_ratio_pct: float | None = None  # 前一營業日券資比%
    margin_usage_pct: float | None = None  # 前一營業日融資使用率%
    short_usage_pct: float | None = None  # 前一營業日融券使用率%
    short_margin_ratio_min_6d_pct: float | None = None  # 最近6營業日（從前一營業日起）最低券資比%


@dataclass(frozen=True)
class ClauseResult:
    clause: str  # "一".."十一"
    fired: bool
    detail: str  # 命中的具體數字，方便前端顯示/除錯


def _diff_ok(value: float | None, avg: float | None, min_diff: float) -> bool:
    """「與全體/同類平均值的差幅在百分之X以上」；avg是平均值本身(可正可負)，
    差幅永遠取絕對差。"""
    if value is None or avg is None:
        return False
    return abs(value - avg) >= min_diff


def check_clause_1(i: ClauseInputs) -> ClauseResult:
    """第2條第1項：6日累積漲跌%超過32%(差幅全體+同類≥20%)；或超過25%(差幅≥20%)+6日
    起迄價差≥50元。收盤價<5元、本益比為負或≥60倍者不適用同類股規定（同類股比較從缺
    時當作沒有這個限制——保守起見，若差幅只差在缺同類股資料，仍以「全體」差幅為準）。"""
    if i.change_6d_pct is None or i.close < 5:
        return ClauseResult("一", False, "無6日累積漲跌%資料或收盤價<5元")
    peer_ok = _diff_ok(i.change_6d_pct, i.change_6d_peer_avg_pct, 20.0)
    industry_ok = i.change_6d_industry_avg_pct is None or _diff_ok(
        i.change_6d_pct, i.change_6d_industry_avg_pct, 20.0
    )
    if abs(i.change_6d_pct) > 32 and peer_ok and industry_ok:
        return ClauseResult("一", True, f"6日累積{i.change_6d_pct:+.1f}%>32%且差幅達標")
    if (
        abs(i.change_6d_pct) > 25
        and peer_ok
        and industry_ok
        and i.price_diff_6d is not None
        and i.price_diff_6d >= 50
    ):
        return ClauseResult(
            "一", True, f"6日累積{i.change_6d_pct:+.1f}%>25%+差幅達標+價差{i.price_diff_6d:.0f}元≥50元"
        )
    return ClauseResult("一", False, f"6日累積{i.change_6d_pct:+.1f}%未達門檻")


_CLAUSE_2_WINDOWS = (
    (30, "change_2d_30d_pct", "change_2d_30d_peer_avg_pct", 100.0, 85.0),
    (60, "change_2d_60d_pct", "change_2d_60d_peer_avg_pct", 130.0, 110.0),
    (90, "change_2d_90d_pct", "change_2d_90d_peer_avg_pct", 160.0, 135.0),
)


def check_clause_2(i: ClauseInputs) -> ClauseResult:
    """第3條第1項：30/60/90營業日起迄兩營業日漲跌%超過100%/130%/160%，且（漲幅差幅
    達85%/110%/135%以上＋收盤價高於開盤參考價）或（跌幅同幅度＋收盤價低於開盤參考
    價）。三個窗口任一成立即觸發。"""
    for days, value_field, avg_field, pct_threshold, diff_threshold in _CLAUSE_2_WINDOWS:
        value = getattr(i, value_field)
        avg = getattr(i, avg_field)
        if value is None or abs(value) <= pct_threshold:
            continue
        if not _diff_ok(value, avg, diff_threshold):
            continue
        if value > 0 and i.close_above_open_ref is True:
            return ClauseResult("二", True, f"{days}日起迄兩日漲{value:+.1f}%>{pct_threshold:.0f}%且差幅達標且收盤>開盤參考價")
        if value < 0 and i.close_above_open_ref is False:
            return ClauseResult("二", True, f"{days}日起迄兩日跌{value:+.1f}%>{pct_threshold:.0f}%且差幅達標且收盤<開盤參考價")
    return ClauseResult("二", False, "三個窗口(30/60/90日)均未達門檻")


def check_clause_3(i: ClauseInputs) -> ClauseResult:
    """第4條第1項：6日累積漲跌%超過25%(差幅≥20%)，且當日成交量較60日均量放大≥5倍
    (與全體平均放大倍數相差≥4倍)。週轉率<0.1%或量<500單位不適用（此排除規則需要
    週轉率資料，缺資料時不套用排除，避免誤刪掉真正該觸發的個股）。"""
    if i.change_6d_pct is None or abs(i.change_6d_pct) <= 25:
        return ClauseResult("三", False, "6日累積漲跌%未超過25%")
    if not _diff_ok(i.change_6d_pct, i.change_6d_peer_avg_pct, 20.0):
        return ClauseResult("三", False, "6日累積漲跌%差幅未達20%")
    if i.volume is not None and i.volume < 500:
        return ClauseResult("三", False, "成交量<500單位，除外")
    if i.turnover_pct is not None and i.turnover_pct < 0.1:
        return ClauseResult("三", False, "週轉率<0.1%，除外")
    if i.volume_ratio_60d is None or i.volume_ratio_60d < 5:
        return ClauseResult("三", False, "成交量未達60日均量5倍")
    if not _diff_ok(i.volume_ratio_60d, i.volume_ratio_60d_peer_avg, 4.0):
        return ClauseResult("三", False, "放大倍數與全體平均相差未達4倍")
    return ClauseResult(
        "三", True, f"6日累積{i.change_6d_pct:+.1f}%+量放大{i.volume_ratio_60d:.1f}倍(60日均量)"
    )


def check_clause_4(i: ClauseInputs) -> ClauseResult:
    """第5條第1項：6日累積漲跌%超過25%(差幅≥20%)，且當日週轉率≥10%(與全體平均差幅≥5%)。"""
    if i.change_6d_pct is None or abs(i.change_6d_pct) <= 25:
        return ClauseResult("四", False, "6日累積漲跌%未超過25%")
    if not _diff_ok(i.change_6d_pct, i.change_6d_peer_avg_pct, 20.0):
        return ClauseResult("四", False, "6日累積漲跌%差幅未達20%")
    if i.turnover_pct is None or i.turnover_pct < 10:
        return ClauseResult("四", False, "當日週轉率未達10%")
    if not _diff_ok(i.turnover_pct, i.turnover_pct_peer_avg, 5.0):
        return ClauseResult("四", False, "週轉率與全體平均差幅未達5%")
    return ClauseResult("四", True, f"6日累積{i.change_6d_pct:+.1f}%+當日週轉率{i.turnover_pct:.1f}%")


def check_clause_6(i: ClauseInputs) -> ClauseResult:
    """第7條第1項：本益比為負或≥60倍(且達全體加權平均2倍以上)，且股價淨值比≥6倍(且
    達全體加權平均2倍以上)，且當日週轉率≥5%且成交量≥3000單位，且股價淨值比達所屬
    產業別加權平均4倍以上——只實作產業淨值比這個子條件，不含券商/投資人集中度子
    條件（需要分點資料，我們沒有）。"""
    if i.pe_ratio is None or i.pbr is None:
        return ClauseResult("六", False, "無本益比或股價淨值比資料")
    pe_ok = i.pe_ratio < 0 or (
        i.pe_ratio >= 60 and i.pe_ratio_peer_avg is not None and i.pe_ratio >= i.pe_ratio_peer_avg * 2
    )
    if not pe_ok:
        return ClauseResult("六", False, "本益比未達門檻")
    pbr_ok = i.pbr >= 6 and i.pbr_peer_avg is not None and i.pbr >= i.pbr_peer_avg * 2
    if not pbr_ok:
        return ClauseResult("六", False, "股價淨值比未達門檻")
    if i.turnover_pct is None or i.turnover_pct < 5:
        return ClauseResult("六", False, "當日週轉率未達5%")
    if i.volume is None or i.volume < 3000:
        return ClauseResult("六", False, "成交量未達3000單位")
    if i.pbr_industry_avg is None or i.pbr < i.pbr_industry_avg * 4:
        return ClauseResult("六", False, "股價淨值比未達所屬產業別加權平均4倍（我們只驗這個子條件，不含券商/投資人集中度）")
    return ClauseResult("六", True, f"本益比{i.pe_ratio:.1f}+淨值比{i.pbr:.1f}倍(達產業平均4倍以上)+週轉率{i.turnover_pct:.1f}%")


def check_clause_7(i: ClauseInputs) -> ClauseResult:
    """第8條第1項：6日累積漲跌%超過25%(差幅≥20%)，且(前一營業日券資比≥20%且融資
    使用率≥25%且融券使用率≥15%) 或 (前一營業日券資比較最近6日最低券資比放大≥4倍)。"""
    if i.change_6d_pct is None or abs(i.change_6d_pct) <= 25:
        return ClauseResult("七", False, "6日累積漲跌%未超過25%")
    if not _diff_ok(i.change_6d_pct, i.change_6d_peer_avg_pct, 20.0):
        return ClauseResult("七", False, "6日累積漲跌%差幅未達20%")
    ratio = i.short_margin_ratio_pct
    if (
        ratio is not None
        and ratio >= 20
        and i.margin_usage_pct is not None
        and i.margin_usage_pct >= 25
        and i.short_usage_pct is not None
        and i.short_usage_pct >= 15
    ):
        return ClauseResult("七", True, f"券資比{ratio:.1f}%+融資使用率{i.margin_usage_pct:.1f}%+融券使用率{i.short_usage_pct:.1f}%")
    if (
        ratio is not None
        and i.short_margin_ratio_min_6d_pct is not None
        and i.short_margin_ratio_min_6d_pct > 0
        and ratio >= i.short_margin_ratio_min_6d_pct * 4
    ):
        return ClauseResult("七", True, f"券資比{ratio:.1f}%較6日最低{i.short_margin_ratio_min_6d_pct:.1f}%放大≥4倍")
    return ClauseResult("七", False, "券資比未達門檻")


def check_clause_9(i: ClauseInputs) -> ClauseResult:
    """第10條第1項：最近6日日均量較60日日均量放大≥5倍(與全體平均相差≥4倍)；或當日
    成交量較60日日均量放大≥5倍(與全體平均相差≥4倍)。不需要價格條件。週轉率<0.1%或
    量<500單位或成交金額<3000萬不適用。"""
    if i.turnover_pct is not None and i.turnover_pct < 0.1:
        return ClauseResult("九", False, "週轉率<0.1%，除外")
    if i.volume is not None and i.volume < 500:
        return ClauseResult("九", False, "成交量<500單位，除外")
    if i.turnover_amount is not None and i.turnover_amount < 30_000_000:
        return ClauseResult("九", False, "成交金額<3000萬，除外")
    if (
        i.avg_volume_ratio_6d_60d is not None
        and i.avg_volume_ratio_6d_60d >= 5
        and _diff_ok(i.avg_volume_ratio_6d_60d, i.avg_volume_ratio_6d_60d_peer_avg, 4.0)
    ):
        return ClauseResult("九", True, f"6日均量較60日均量放大{i.avg_volume_ratio_6d_60d:.1f}倍")
    if (
        i.volume_ratio_60d is not None
        and i.volume_ratio_60d >= 5
        and _diff_ok(i.volume_ratio_60d, i.volume_ratio_60d_peer_avg, 4.0)
    ):
        return ClauseResult("九", True, f"當日量較60日均量放大{i.volume_ratio_60d:.1f}倍")
    return ClauseResult("九", False, "成交量放大倍數未達門檻")


def check_clause_10(i: ClauseInputs) -> ClauseResult:
    """第11條第1項：最近6日累積週轉率超過50%(與全體平均差幅≥40%)，且當日週轉率
    ≥10%(與全體平均差幅≥5%)。不需要價格條件。當日成交金額<5億不適用。"""
    if i.turnover_amount is not None and i.turnover_amount < 500_000_000:
        return ClauseResult("十", False, "成交金額<5億，除外")
    if i.cum_turnover_6d_pct is None or i.cum_turnover_6d_pct <= 50:
        return ClauseResult("十", False, "6日累積週轉率未超過50%")
    if not _diff_ok(i.cum_turnover_6d_pct, i.cum_turnover_6d_peer_avg_pct, 40.0):
        return ClauseResult("十", False, "6日累積週轉率差幅未達40%")
    if i.turnover_pct is None or i.turnover_pct < 10:
        return ClauseResult("十", False, "當日週轉率未達10%")
    if not _diff_ok(i.turnover_pct, i.turnover_pct_peer_avg, 5.0):
        return ClauseResult("十", False, "當日週轉率差幅未達5%")
    return ClauseResult("十", True, f"6日累積週轉率{i.cum_turnover_6d_pct:.1f}%+當日週轉率{i.turnover_pct:.1f}%")


def check_clause_11(i: ClauseInputs) -> ClauseResult:
    """第12條第1項：收盤價每超過1000元一個級距，6日起迄兩營業日收盤價"價差"門檻
    +150元(1000~2000元區間300元)，且當日收盤價為最近6日最高或最低。只在
    close_above_open_ref標示"是最近6日最高"(True)或"是最近6日最低"(False)、
    price_diff_6d有值時才判定；None一律視為不成立。"""
    if i.price_diff_6d is None or i.close <= 1000:
        return ClauseResult("十一", False, "收盤價未超過1000元或無6日價差資料")
    # 級距是「逾N千至(N+1)千以下」，剛好等於某個千元整數算下一級距的下界(含)，
    # 不是上界；ceil(close/1000)對整數千元剛好給出這個級距編號，用它再減1
    # 才會是「逾1000至2000以下」= tier 1。
    tier = math.ceil(i.close / 1000) - 1
    threshold = 300 + max(0, tier - 1) * 150
    if i.price_diff_6d < threshold:
        return ClauseResult("十一", False, f"6日價差{i.price_diff_6d:.0f}元未達門檻{threshold}元")
    if i.close_above_open_ref is None:
        return ClauseResult("十一", False, "無法判定是否為6日最高/最低收盤價")
    return ClauseResult("十一", True, f"收盤價{i.close:.0f}元，6日價差{i.price_diff_6d:.0f}元≥{threshold}元")


# 對應第六條「連續5個營業日或最近10個營業日內有6天或最近30個營業日內有12天，依第一款
# 至第八款發布交易資訊」——本模組做得到的款只到七，八(TDR)不適用一般股票。
CHECKERS = {
    "一": check_clause_1,
    "二": check_clause_2,
    "三": check_clause_3,
    "四": check_clause_4,
    "六": check_clause_6,
    "七": check_clause_7,
    "九": check_clause_9,
    "十": check_clause_10,
    "十一": check_clause_11,
}
# 第六條處置累積基數只看第一款到第八款；本模組能做的款是這個集合跟CHECKERS的交集。
ACCUMULATION_CLAUSES = {"一", "二", "三", "四", "六", "七"}


def check_all_clauses(i: ClauseInputs) -> list[ClauseResult]:
    """跑過本模組能判定的每一款，回傳全部結果（包含fired=False的，讓呼叫端看得到
    「差一點點沒中」的數字，不是只回傳命中的）。"""
    return [checker(i) for checker in CHECKERS.values()]
