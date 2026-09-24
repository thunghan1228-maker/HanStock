"""台股（集中市場／櫃買）交易日判斷：平日、而且不是休市日。

使用者 2026-09-25：「今天 25 號沒有交易啊」——程式原本只看星期幾，把中秋節當成交易日，
醞釀／發動存了一份 9/25 的名單、大戶力也會在 08:45 把上一個交易日的資料清掉。
休市日依證交所公告的「市場開休市日期」整理（115 年）；每年年底要把下一年的補進來，
沒補的年份只會退回「平日就是交易日」，不會整個壞掉。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# 證交所 115 年（2026）休市日：只列平日（週末本來就不開盤）。
# 春節 2/12、2/13 市場無交易只辦結算交割，也算休市。
TW_MARKET_HOLIDAYS: frozenset[str] = frozenset({
    "2026-01-01",  # 元旦
    "2026-02-12", "2026-02-13",  # 春節前無交易日（僅結算交割）
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",  # 農曆除夕、春節
    "2026-02-27",  # 和平紀念日補假
    "2026-04-03", "2026-04-06",  # 兒童節、民族掃墓節
    "2026-05-01",  # 勞動節
    "2026-06-19",  # 端午節
    "2026-09-25",  # 中秋節
    "2026-09-28",  # 教師節
    "2026-10-09",  # 國慶日補假
    "2026-10-26",  # 光復節補假
    "2026-12-25",  # 行憲紀念日
    "2027-01-01",  # 元旦
})


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def is_trading_day(value: date | datetime | str) -> bool:
    """平日且不在休市日名單。"""
    day = _as_date(value)
    return day.weekday() < 5 and day.isoformat() not in TW_MARKET_HOLIDAYS


def previous_trading_day(value: date | datetime | str) -> date:
    """value 之前（不含）最近的一個交易日。"""
    day = _as_date(value) - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_trading_day(value: date | datetime | str) -> date:
    """value 之後（不含）最近的一個交易日。"""
    day = _as_date(value) + timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day
