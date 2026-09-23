"""把disposition_market_stats.py(全市場橫斷面統計)跟disposition_rules.py(14款判定)
接起來，算出我們追蹤的43個官方族群股票每天觸發了哪些款、並依官方第六條的累積規則判斷
「已經連續/累積到會被處置」。

第六條累積規則：連續3個營業日依第一款發布注意；或(連續5個營業日 或 最近10個營業日內
有6天 或 最近30個營業日內有12天)依第一款至第八款發布注意。我們做得到的款只有一二三四
六七(五需要分點資料、八限TDR)，所以「依第一款至第八款」這條路徑只能用我們做得到的6款
去湊，會比官方實際判定寬鬆一點——這不是我們判斷錯，是官方會用到我們沒有的款(五/八)
也可能觸發，我們這邊真的沒辦法算，資料庫裡沒有這兩款的紀錄不代表那天真的沒有異常。

處置期間：官方新制固定5個營業日，除非基數計算期間內也曾依第十三款(當日沖銷比例過高)
發布注意才加重為7個營業日——我們沒有當沖比例資料，所以本模組永遠只回報5個營業日，
並在結果裡標注「可能因當沖比例過高而延長為7個營業日，我們沒有這項資料」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from database import get_connection, initialize_database
from disposition_market_stats import MarketSnapshot, build_market_snapshot
from disposition_rules import ACCUMULATION_CLAUSES, CHECKERS, ClauseInputs, ClauseResult
from stock_groups import STOCK_GROUPS

TW_TZ = timezone(timedelta(hours=8))

DISPOSITION_DURATION_BUSINESS_DAYS = 5  # 新制固定5天；7天加重規則需要當沖比例資料，我們沒有


def official_group_codes() -> set[str]:
    codes: set[str] = set()
    for members in STOCK_GROUPS.values():
        codes.update(code for code, _name in members)
    return codes


def _ensure_table() -> None:
    initialize_database()
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS disposition_clause_log (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                clause TEXT NOT NULL,
                fired INTEGER NOT NULL,
                detail TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, trade_date, clause)
            );
            CREATE INDEX IF NOT EXISTS disposition_clause_log_date_idx
                ON disposition_clause_log (trade_date);
            """
        )


def build_clause_inputs(
    code: str, snapshot: MarketSnapshot, *, industry: str | None = None,
    fundamentals: dict[str, float | None] | None = None,
) -> ClauseInputs | None:
    """組出單一股票的ClauseInputs：價格/成交量欄位來自snapshot(全市場橫斷面統計)，
    週轉率/本益比/淨值比/券資比等Phase 2欄位由fundamentals(呼叫端合併好的{欄位:值})
    覆蓋——沒給就全部是None，對應款直接判定不成立，不會誤觸發。"""
    metrics = snapshot.metrics_by_code.get(code)
    if metrics is None:
        return None
    peer = snapshot.peer_avg
    industry_avg = snapshot.industry_avg.get(industry, {}) if industry else {}
    fundamentals = fundamentals or {}
    return ClauseInputs(
        code=code,
        close=metrics.close,
        volume=metrics.volume,
        turnover_amount=fundamentals.get("turnover_amount"),
        change_6d_pct=metrics.change_6d_pct,
        change_6d_peer_avg_pct=peer.get("change_6d_pct"),
        change_6d_industry_avg_pct=industry_avg.get("change_6d_pct"),
        price_diff_6d=metrics.price_diff_6d,
        change_2d_30d_pct=metrics.change_2d_30d_pct,
        change_2d_30d_peer_avg_pct=peer.get("change_2d_30d_pct"),
        change_2d_60d_pct=metrics.change_2d_60d_pct,
        change_2d_60d_peer_avg_pct=peer.get("change_2d_60d_pct"),
        change_2d_90d_pct=metrics.change_2d_90d_pct,
        change_2d_90d_peer_avg_pct=peer.get("change_2d_90d_pct"),
        close_above_open_ref=metrics.close_above_open_ref,
        volume_ratio_60d=metrics.volume_ratio_60d,
        volume_ratio_60d_peer_avg=peer.get("volume_ratio_60d"),
        avg_volume_ratio_6d_60d=metrics.avg_volume_ratio_6d_60d,
        avg_volume_ratio_6d_60d_peer_avg=peer.get("avg_volume_ratio_6d_60d"),
        turnover_pct=fundamentals.get("turnover_pct"),
        turnover_pct_peer_avg=fundamentals.get("turnover_pct_peer_avg"),
        cum_turnover_6d_pct=fundamentals.get("cum_turnover_6d_pct"),
        cum_turnover_6d_peer_avg_pct=fundamentals.get("cum_turnover_6d_peer_avg_pct"),
        pe_ratio=fundamentals.get("pe_ratio"),
        pe_ratio_peer_avg=fundamentals.get("pe_ratio_peer_avg"),
        pbr=fundamentals.get("pbr"),
        pbr_peer_avg=fundamentals.get("pbr_peer_avg"),
        pbr_industry_avg=fundamentals.get("pbr_industry_avg"),
        short_margin_ratio_pct=fundamentals.get("short_margin_ratio_pct"),
        margin_usage_pct=fundamentals.get("margin_usage_pct"),
        short_usage_pct=fundamentals.get("short_usage_pct"),
        short_margin_ratio_min_6d_pct=fundamentals.get("short_margin_ratio_min_6d_pct"),
    )


def save_clause_results(trade_date: str, code: str, results: list[ClauseResult]) -> None:
    _ensure_table()
    updated_at = datetime.now(TW_TZ).isoformat(timespec="seconds")
    rows = [(code, trade_date, r.clause, 1 if r.fired else 0, r.detail, updated_at) for r in results]
    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO disposition_clause_log (stock_code, trade_date, clause, fired, detail, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date, clause) DO UPDATE SET
                fired = excluded.fired, detail = excluded.detail, updated_at = excluded.updated_at
            """,
            rows,
        )


def run_universe_for_date(
    trade_date: str, *, codes: set[str] | None = None,
    industry_by_code: dict[str, str] | None = None,
    fundamentals_by_code: dict[str, dict[str, float | None]] | None = None,
) -> dict[str, list[ClauseResult]]:
    """對指定股票集合(預設43個官方族群)跑完disposition_market_stats+disposition_rules，
    存進disposition_clause_log，回傳{代號: [每款結果]}。"""
    targets = codes if codes is not None else official_group_codes()
    snapshot = build_market_snapshot(trade_date, industry_by_code=industry_by_code)
    fundamentals_by_code = fundamentals_by_code or {}
    output: dict[str, list[ClauseResult]] = {}
    for code in sorted(targets):
        industry = (industry_by_code or {}).get(code)
        inputs = build_clause_inputs(
            code, snapshot, industry=industry, fundamentals=fundamentals_by_code.get(code),
        )
        if inputs is None:
            continue
        results = [checker(inputs) for checker in CHECKERS.values()]
        output[code] = results
        save_clause_results(trade_date, code, results)
    return output


@dataclass(frozen=True)
class AccumulationStatus:
    code: str
    trigger_path: str | None  # None=沒觸發；否則是中文說明走哪條路徑
    fired_dates: list[str]  # 貢獻到這次判定的交易日
    predicted_duration_business_days: int | None
    duration_caveat: str | None  # 提醒：可能因當沖比例而延長，我們沒有這項資料


def _recent_clause_log(code: str, trade_date: str, lookback_dates: int = 35) -> list[tuple[str, set[str]]]:
    """回傳[(trade_date, {那天命中的款}), ...]，由新到舊，最多lookback_dates個「有紀錄的
    交易日」(不是日曆天數)——用來湊第六條的3/5/10/30個營業日窗口。"""
    _ensure_table()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, clause FROM disposition_clause_log
            WHERE stock_code = ? AND trade_date <= ? AND fired = 1
            ORDER BY trade_date DESC
            """,
            (code, trade_date),
        ).fetchall()
        all_dates = connection.execute(
            """
            SELECT DISTINCT trade_date FROM disposition_clause_log
            WHERE stock_code = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?
            """,
            (code, trade_date, lookback_dates),
        ).fetchall()
    fired_by_date: dict[str, set[str]] = {}
    for row in rows:
        fired_by_date.setdefault(row["trade_date"], set()).add(row["clause"])
    return [(row["trade_date"], fired_by_date.get(row["trade_date"], set())) for row in all_dates]


def check_disposition_trigger(code: str, trade_date: str) -> AccumulationStatus:
    """依官方第六條累積規則，用disposition_clause_log的歷史判定這檔股票是不是已經走到
    會被處置的地步。三條路徑依序檢查，中第一條就回傳；都沒中回傳trigger_path=None。"""
    history = _recent_clause_log(code, trade_date, lookback_dates=35)
    caveat = (
        "官方規則：處置基數期間若也曾因當日沖銷比例過高(第十三款)被列注意，處置期間會從5個"
        "營業日加重為7個營業日；我們沒有當日沖銷資料，這裡固定顯示5天，實際可能是7天。"
    )

    # 路徑一：連續3個營業日都依第一款發布注意。
    consecutive_clause_1: list[str] = []
    for trade, clauses in history:
        if "一" in clauses:
            consecutive_clause_1.append(trade)
            if len(consecutive_clause_1) >= 3:
                break
        else:
            break
    if len(consecutive_clause_1) >= 3:
        return AccumulationStatus(
            code, "連續3個營業日依第一款發布注意", list(reversed(consecutive_clause_1[:3])),
            DISPOSITION_DURATION_BUSINESS_DAYS, caveat,
        )

    def _any_accumulation_clause(clauses: set[str]) -> bool:
        return bool(clauses & ACCUMULATION_CLAUSES)

    # 路徑二：連續5個營業日依第一款至第八款發布注意。
    consecutive_any: list[str] = []
    for trade, clauses in history:
        if _any_accumulation_clause(clauses):
            consecutive_any.append(trade)
            if len(consecutive_any) >= 5:
                break
        else:
            break
    if len(consecutive_any) >= 5:
        return AccumulationStatus(
            code, "連續5個營業日依第一款至第八款發布注意(僅計我們做得到的一二三四六七款)",
            list(reversed(consecutive_any[:5])), DISPOSITION_DURATION_BUSINESS_DAYS, caveat,
        )

    # 路徑三：最近10個營業日內有6天；路徑四：最近30個營業日內有12天。
    for window, need, label in ((10, 6, "最近10個營業日內有6天"), (30, 12, "最近30個營業日內有12天")):
        window_dates = [trade for trade, _clauses in history[:window]]
        hit_dates = [trade for trade, clauses in history[:window] if _any_accumulation_clause(clauses)]
        if len(hit_dates) >= need:
            return AccumulationStatus(
                code, f"{label}依第一款至第八款發布注意(僅計我們做得到的一二三四六七款)",
                list(reversed(hit_dates[:need])), DISPOSITION_DURATION_BUSINESS_DAYS, caveat,
            )

    return AccumulationStatus(code, None, [], None, None)
