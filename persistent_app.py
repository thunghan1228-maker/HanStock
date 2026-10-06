"""HanStock 持久化 app：只保留台股即時行情與主力副圖所需的持久化端點。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import Body, Query, Request
from fastapi.responses import JSONResponse

from hanstock_app import app, _normalize_stock_code
from main_force_collector import start_main_force_collector
from main_force_store import (
    latest_trade_date_with_data,
    load_daily_main_force_net,
    load_main_force_bars,
    load_main_force_ranking,
    main_force_storage_status,
)
from main_force_backfill_jobs import list_main_force_backfill_jobs, prune_pending_backfill_jobs, queue_backfill_for_all_group_stocks, request_main_force_backfill
from disposition_stocks import disposition_status, get_disposition_map, start_disposition_collector
from stock_groups import industry_group_codes
from brew_launch_history import (
    backfill_holder_force as brew_launch_backfill_holder_force,
    group_quotes_payload,
    history as brew_launch_history,
    purge_false_relaunches as brew_launch_purge_false_relaunches,
    scan_status as brew_launch_scan_status,
    start_brew_launch_scan,
)
from trading_days import is_trading_day
from watchlist_store import WatchlistError, load as watchlist_load, save as watchlist_save
from chips_daily import chips_daily as chips_daily_payload, collector_status as chips_collector_status, run_collect as run_chips_collect, start_chips_collector
from swing_report import run_once as run_swing_report, start_swing_report_collector, swing_report as swing_report_payload
from etf_holdings import collector_status as etf_status, run_collect as run_etf_collect, start_etf_collector
from heilong_backtest import backtest as heilong_backtest_payload, collector_status as heilong_status, rebuild as rebuild_heilong
from heilong_picker import payload as picker_payload
from chip_radar import payload as chip_radar_payload, stock as chip_radar_stock
from stock_checkup import checkup as checkup_payload, collector_status as checkup_status, diag as diag_payload, rebuild as rebuild_checkup
from fundamentals_daily import collector_status as fundamentals_status, run_collect as run_fundamentals_collect, start_fundamentals_collector
from stock_trading_eligibility import (
    contract_debug,
    peek_trading_eligibility,
    start_trading_eligibility_warmer,
    trading_eligibility_warmer_status,
)
from history_sources import OTC_INDEX_CODE, history_sources_status, probe_history_sources, stock_market
from intraday_large_order_collector import start_intraday_large_order_collector, collector_status as large_order_collector_status
from four_gate_signals_collector import start_four_gate_signals_collector
from daily_bars_collector import collector_status as daily_bars_collector_status, start_daily_bars_collector
from daily_bars_store import daily_bars_storage_status, latest_daily_trade_date_before, load_daily_bars
from after_hours_fixed_price_collector import start_after_hours_fixed_price_collector
from after_hours_fixed_price import load_after_hours_day, load_latest_after_hours_day
from otc_gap_backfill import start_otc_gap_backfill, backfill_state as otc_gap_backfill_state
from daily_bars_history_backfill import start_group_history_backfill, backfill_state as group_history_backfill_state
from bars_history import coverage as bars_history_coverage, run_in_background as run_bars_history, start_bars_history_collector, status as bars_history_status
from price_adjust import list_events as list_price_adjust_events
from four_gate_signals import fix_stale_four_gate_labels
from intraday_signal_store import load_latest_signals, load_latest_signals_by_kind, load_recent_trade_dates, load_signals_for_ticker, find_out_of_session_kline_signals, purge_out_of_session_kline_signals
from intraday_kline_signals import kline_signal_backfill_status, start_kline_signal_backfill_today
from kline_signal_backfill_collector import start_kline_signal_backfill_collector
from market_data_hub import get_market_data_hub
from main_force_flip_backfill_collector import start_main_force_flip_backfill_collector
from main_force_flip_signals import (
    flip_signal_backfill_status,
    get_main_force_flip_monitor,
    inspect_flip_signals,
    start_flip_signal_backfill,
)
from history_quota import history_quota
from otc_index import OTC_INDEX_DISPLAY_NAME, OTC_INDEX_HUB_CODE, TW_TZ, taipei_trade_date
from otc_index_hub import get_otc_index_hub
from otc_index_service import get_otc_index_service
from otc_index_store import save_index_bars_5m
from stock_history_service import get_stock_history_bars_1m, get_stock_history_bars_5m
from stock_bar_bootstrap import stock_bar_repair_status
from stock_bar_repair_collector import backfill_pause_reason, start_stock_bar_repair_collector
from quote_service import get_quote_service


_market_data_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _persistent_lifespan(fastapi_app):
    async with _market_data_lifespan(fastapi_app) as state:
        # 主力副圖是唯一保留的持久化背景工作。
        # 備援 Railway 專案不登入 Shioaji，因此不啟動沒有工作的保存執行緒。
        from quote_service import quote_deployment_role

        # 純本機SQLite文字修正，跟Shioaji/角色無關，兩個Railway都可以跑；
        # 已經是最新文字的列不會被UPDATE命中，重複執行成本趨近於0。
        try:
            fix_stale_four_gate_labels()
        except Exception:  # noqa: BLE001
            pass

        if quote_deployment_role() == "primary":
            # 櫃買指數每收掉一根5分K就存進SQLite：重新部署/跨日後的MA20歷史不再
            # 只能靠Shioaji kbars（額度用完就整天「資料蒐集中」）。
            get_otc_index_hub().set_bar_persister(save_index_bars_5m)
            start_main_force_collector()
            start_intraday_large_order_collector()
            start_four_gate_signals_collector()
            start_daily_bars_collector()
            start_after_hours_fixed_price_collector()
            start_otc_gap_backfill()
            start_group_history_backfill()
            start_brew_launch_scan()  # 醞釀快照＋盤中發動紀錄（每日保存）
            start_chips_collector()  # 盤後籌碼：三大法人（上市直抓、上櫃鏡像）＋主力大單每日
            start_fundamentals_collector()  # 本益比、月營收、股本、集保週籌碼（波段日報第二階段）
            start_etf_collector()  # 主動式 ETF 五檔每日持股（下午報第三階段）
            start_swing_report_collector()  # 波段日報：收盤後整理、每日保存（2026-09-26 使用者）
            start_bars_history_collector()  # 日K補到三年＋分割減資還原事件（創高黑選股，2026-10-04 使用者）
            # 之前只有stock_bar_repair_status(唯讀查詢)被匯入，start_
            # stock_bar_repair_collector從來沒被呼叫過──main_force_backfill_
            # jobs佇列裡的工作因此永遠不會被process_main_force_backfill_job
            # 處理，不管優先序設多高，attempts永遠停在0。這裡補上真正啟動
            # 這個背景執行緒。
            start_stock_bar_repair_collector()
            # 5分鐘K盤中訊號(905/1+2多/創高黑龍等)收盤後(13:35+)用歷史
            # kbars自動重播校正一次，修正即時路徑受動態訂閱時機影響、當天
            # 可能已經算錯或漏掉的訊號；之前這個回補只有手動觸發的端點，
            # 沒有排程，沒人記得打就永遠不會自動修正。
            start_kline_signal_backfill_collector()
            # 主力累計翻多空：收盤後用kbars+已落盤的主力副圖重播今天，補回偵測器
            # 不在線時漏掉的訊號；額度用完那天補不成就隔天開盤前再補。
            start_main_force_flip_backfill_collector()
            start_disposition_collector()
            start_trading_eligibility_warmer(_group_stock_codes)
            # 排全族群股票的主力副圖回補，不用等使用者自己點開每一支才觸發；
            # 純SQLite寫入(無Shioaji連線)但幾百檔股票還是有感時間，丟背景
            # 執行緒避免拖慢啟動就緒。只排最近3個平日（使用者明確說主力副圖
            # 補3天就夠，不用30天）：逐筆回補是Shioaji歷史額度的最大消耗者，
            # 之前排的30天批次留下的舊pending工作一併清掉，之後的日子由每天
            # 的即時落盤自然累積。
            import threading as _threading

            def _queue_recent_main_force_backfill() -> None:
                prune_pending_backfill_jobs(days=3)
                queue_backfill_for_all_group_stocks(days=3)

            _threading.Thread(
                target=_queue_recent_main_force_backfill,
                name="hanstock-main-force-group-backfill-queue",
                daemon=True,
            ).start()
        try:
            yield state
        finally:
            pass


app.router.lifespan_context = _persistent_lifespan


@app.get("/api/hub/persistence/status")
def get_persistence_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "data": {
            "mainForceCollectorEnabled": os.getenv(
                "HANSTOCK_MAIN_FORCE_COLLECTOR_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "mainForceHistory": main_force_storage_status(),
            "instantLargeOrderCollectorEnabled": os.getenv(
                "HANSTOCK_INSTANT_LARGE_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "instantLargeOrder": large_order_collector_status(),
            "fourGateCollectorEnabled": os.getenv(
                "HANSTOCK_FOUR_GATE_COLLECTOR_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "dailyBarsCollectorEnabled": os.getenv(
                "HANSTOCK_DAILY_BARS_COLLECTOR_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "dailyBarsHistory": daily_bars_storage_status(),
            "dailyBarsCollector": daily_bars_collector_status(),
            "otcGapBackfillEnabled": os.getenv(
                "HANSTOCK_OTC_GAP_BACKFILL_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "otcGapBackfill": otc_gap_backfill_state(),
            "groupHistoryBackfill": group_history_backfill_state(),
            "barsHistory": bars_history_status(),
            "stockBarAutoRepairEnabled": False,
            "stockBarAutoRepair": stock_bar_repair_status(),
            "mainForceBackfillPausedReason": backfill_pause_reason(),
            "klineSignalBackfillCollectorEnabled": os.getenv(
                "HANSTOCK_KLINE_SIGNAL_BACKFILL_COLLECTOR_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "klineSignalBackfill": kline_signal_backfill_status(),
            "mainForceFlip": get_main_force_flip_monitor().status(),
            "mainForceFlipBackfill": flip_signal_backfill_status(),
        },
    }


@app.get("/api/hub/quote-health")
def get_quote_health() -> dict[str, Any]:
    """即時行情健康檢查：stock_quote_age_seconds/last_stock_quote_time可以
    直接看出Shioaji股票tick是不是還在正常流入(main_force_collector等背景
    工作都只是被動讀取Hub已經聚合好的bar，tick本身停了的話這些背景工作
    不會報錯，只會一直沒有新資料)，不用再去翻Railway log找關鍵字才能
    確認。這個檢查本來就存在(QuoteService.get_stock_health，
    intraday_large_order_collector背景工作本身就有在用)，只是先前只有
    沒有部署的api_server.py才對外暴露，這裡補上讓正式部署的app也能查。"""
    service = get_quote_service()
    api = getattr(service, "api", None)
    return {
        "status": "ok",
        "data": {
            **service.get_stock_health(),
            # Shioaji歷史資料流量額度：kbars回補(個股主力副圖/櫃買指數)失敗時先看這裡。
            "historyQuota": history_quota.snapshot(api),
            "otcIndex": {**get_otc_index_hub().get_status(), "lastError": get_otc_index_service().last_error},
        },
    }


def _kick_otc_index_bootstrap(hub: Any) -> None:
    """跨日rollover或kbars補齊失敗後hub的bootstrap_ok會是False：趁前端輪詢時在背景重補，
    不用等下一次重新部署/重連才有MA20歷史。"""
    try:
        if hub.get_status().get("bootstrap_ok"):
            return
        service = get_quote_service()
        if not bool(getattr(getattr(service, "state", None), "logged_in", False)):
            return
        get_otc_index_service().ensure_bootstrapped(service.api)
    except Exception:  # noqa: BLE001
        pass


def main_force_threshold() -> dict[str, Any]:
    """主力大單的判定門檻（張數；金額門檻預設關閉），給 API 回給前端顯示用。"""
    from market_data_hub import MAIN_FORCE_MIN_AMOUNT, MAIN_FORCE_MIN_LOTS

    return {"minLots": int(MAIN_FORCE_MIN_LOTS), "minAmount": float(MAIN_FORCE_MIN_AMOUNT)}


def _group_stock_codes() -> list[str]:
    from stock_groups import industry_group_codes

    return sorted(industry_group_codes())  # 只標 43 個族群的股票（股期標的清單不是族群）


@app.get("/api/hub/stock-flags")
def get_stock_flags(summary: bool = Query(False)) -> dict[str, Any]:
    """全部族群個股的可交易旗標（可融資／可融券／可現股當沖／有股期）與是否為處置股，給訊號中心
    每一列標註用；一次回全部，前端幾分鐘抓一次就好。融資券旗標由背景每分鐘更新的快取供應（合約
    清單還沒下載完時為 null，之後幾輪內會填滿），這裡只讀快取、不查合約，不會卡住回 502；
    處置股來自 TWSE／TPEx 官方公告，抓取狀態放在 disposition 裡。"""
    from stock_trading_eligibility import has_stock_futures

    disposition = get_disposition_map()
    codes = _group_stock_codes()
    start_trading_eligibility_warmer(_group_stock_codes)  # 沒被 lifespan 啟動（例如測試環境）也能自救
    stocks: dict[str, Any] = {}
    for code in codes:
        info = peek_trading_eligibility(code) or {
            "marginable": None, "shortable": None, "dayTradeEligible": None, "hasStockFutures": has_stock_futures(code),
        }
        item = disposition.get(code)
        level = int(info.get("dispositionLevel") or 0)
        # 公告清單沒抓到（TPEx 被擋 403 之類）時，永豐個股資訊列的處置等級也算處置中。
        info["disposition"] = bool(item) or level > 0
        info["dispositionUntil"] = item.get("end") if item else None
        info["dispositionReason"] = (item.get("reason") if item else None) or (f"永豐合約處置等級 {level}" if level > 0 else None)
        stocks[code] = info
    unknown = sum(1 for info in stocks.values() if info.get("marginable") is None)
    sample_code = "2330" if "2330" in stocks else (codes[0] if codes else "")
    counts = {
        "marginable": sum(1 for info in stocks.values() if info.get("marginable")),
        "shortable": sum(1 for info in stocks.values() if info.get("shortable")),
        "dayTradeEligible": sum(1 for info in stocks.values() if info.get("dayTradeEligible")),
        "disposition": sum(1 for info in stocks.values() if info.get("disposition")),
        "attention": sum(1 for info in stocks.values() if info.get("attention")),
        "total": len(stocks),
    }
    return {
        "status": "ok",
        "updatedAt": datetime.now(TW_TZ).isoformat(timespec="seconds"),
        # summary=true 只看摘要與診斷，不回 455 檔明細
        "stocks": {} if summary else stocks,
        "counts": counts,
        "sample": {code: stocks[code] for code in ("2330", "1101", "3532") if code in stocks},
        "unknownEligibilityCount": unknown,
        "eligibilityWarmer": trading_eligibility_warmer_status(),
        # 公告清單（TWSE／TPEx／Shioaji punish）＋ 永豐個股資訊列處置等級 > 0 的，合在一起
        "dispositionCodes": sorted(code for code, info in stocks.items() if info.get("disposition")),
        "dispositionLevelCodes": sorted(code for code, info in stocks.items() if int(info.get("dispositionLevel") or 0) > 0),
        # 官方注意股（永豐個股資訊列的 attention_flag，交易所當天公布注意交易資訊的股票）
        "attentionCodes": sorted(code for code, info in stocks.items() if info.get("attention")),
        "disposition": disposition_status(),
        # 診斷：背景暖機上一輪對一檔合約的檢查結果（合約型別、contracts.info 欄位、各條路的耗時），
        # 融資券旗標全是 null／false 時看這裡；請求路徑本身不碰 Shioaji。
        "debug": contract_debug(sample_code) if sample_code else None,
    }


@app.get("/api/hub/history-sources")
def get_history_sources(
    probe: str | None = Query(None),
    trade_date: str | None = Query(None),
    symbols: str | None = Query(None),
    finmind_id: str | None = Query(None),
) -> dict[str, Any]:
    """歷史分K來源自檢：永豐額度、FinMind/Yahoo備援統計。帶 probe=代號 會真的各打一次
    FinMind 與 Yahoo（不碰永豐額度），回傳筆數與首尾K棒，用來驗證備援的欄位、分鐘標籤
    跟成交量單位；trade_date 預設最近一個已收盤的交易日。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    service = get_quote_service()
    data: dict[str, Any] = {"shioaji": history_quota.snapshot(getattr(service, "api", None))}
    if probe:
        raw = str(probe).strip().upper()
        # 櫃買指數（OTC_INDEX／TPEX／^TWOII）走指數專用 probe：Yahoo 1 分／5 分 K 與 FinMind 櫃買分 K。
        code = OTC_INDEX_CODE if raw in {"OTC_INDEX", "OTC", "TPEX", "^TWOII"} else _normalize_stock_code(probe)
        if not trade_date:
            now = datetime.now(TW_TZ)
            day = now.date() if (now.hour, now.minute) >= (13, 35) and now.weekday() < 5 else now.date() - timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            trade_date = day.isoformat()
        data["probe"] = probe_history_sources(
            code, trade_date, market=None if code == OTC_INDEX_CODE else stock_market(code),
            yahoo_symbols=[item for item in (symbols or "").split(",") if item.strip()] or None,
            finmind_id=finmind_id,
        )
    # 統計放在 probe 之後才拿，成交量單位校準、資料集自動換名這些 probe 觸發的結果才看得到。
    data["sources"] = history_sources_status()
    return {"status": "ok", "data": data}


@app.post("/api/hub/main-force-flip/backfill")
def post_main_force_flip_backfill(trade_date: str | None = Query(None)) -> dict[str, Any]:
    """手動觸發主力累計翻多空的當日重播回補（背景執行，結果看 backfill-status）；
    預設今天。盤中觸發會跟即時偵測互相干擾，請在收盤後或開盤前使用。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    return {"status": "ok", **start_flip_signal_backfill(trade_date)}


@app.get("/api/hub/main-force-flip/backfill-status")
def get_main_force_flip_backfill_status() -> dict[str, Any]:
    return {"status": "ok", "data": flip_signal_backfill_status()}


@app.get("/api/hub/main-force-flip/inspect")
def get_main_force_flip_inspect(
    code: str = Query(...),
    trade_date: str | None = Query(None),
    trace: bool = Query(False),
) -> dict[str, Any]:
    """單檔重播主力累計翻多空的判定過程：每個零軸／VWAP 穿越的時間、同步視窗內被哪個濾網擋下
    （nearMisses），trace=true 再附每根 1 分 K 的累計／VWAP／量比，用來跟另一台工具對條件。
    不寫入訊號、不動即時偵測器；價量走 kbars 或備援來源，只涵蓋最近 5 個日曆天。"""
    stock = _normalize_stock_code(code)
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    else:
        trade_date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    return {"status": "ok", "data": inspect_flip_signals(stock, trade_date, include_trace=trace)}


@app.get("/api/hub/intraday-signals")
def get_intraday_signals(
    trade_date: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, ge=1, le=20000),
    include_chart_kinds: bool = Query(False),
) -> dict[str, Any]:
    """讀取已永久保存的盤中訊號。

    5 分鐘K線結構性訊號(1+2多/創高黑龍等)由intraday_kline_signals.py
    在本機即時偵測寫入；即時大單與四項精選也共用同一個永久訊號表。此端點
    只讀取已保存資料，不對外連線。不指定 kind 的當日總表預設不含只在 K 線圖
    上疊符號的 5 分 K 訊號（905／20MA 穿越等），那些一天就幾千筆，會把早盤的
    其他訊號擠出 limit；要完整資料帶 include_chart_kinds=true。
    """
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    signals = (
        load_latest_signals_by_kind(date, kind, limit=limit)
        if kind
        else load_latest_signals(date, limit=limit, include_chart_kinds=include_chart_kinds)
    )
    return {
        "status": "ok",
        "tradeDate": date,
        "kind": kind,
        "count": len(signals),
        "signals": signals,
    }


@app.get("/api/hub/intraday-signals/dates")
def get_intraday_signal_dates(limit: int = Query(10, ge=1, le=60)) -> dict[str, Any]:
    """有保存訊號紀錄的交易日清單，供歷史查詢分頁使用。"""
    return {"status": "ok", "dates": load_recent_trade_dates(limit=limit)}


@app.get("/api/hub/intraday-signals/stock/{stock_code}")
def get_intraday_signals_for_stock(
    stock_code: str,
    trade_date: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """單一股票當日所有已保存K線訊號（905/1+2多/520等），依時間
    由舊到新排序，供K線圖疊上符號標記使用。"""
    code = _normalize_stock_code(stock_code)
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    signals = load_signals_for_ticker(code, date, limit=limit)
    return {
        "status": "ok",
        "code": code,
        "tradeDate": date,
        "count": len(signals),
        "signals": signals,
    }


@app.get("/api/hub/kline-signals/backfill-today")
def trigger_kline_signal_backfill_today(
    trade_date: str | None = Query(None), local_only: bool = Query(False),
) -> dict[str, Any]:
    """一次性回補：用Shioaji歷史kbars重播trade_date(預設今天)已經走完的
    5分K，補回905/520/1+2多/創高黑龍等訊號偵測引擎剛上線那天漏掉的
    部分。也可以指定過去幾天內的日期(例如假日想先驗證上一個交易日的
    資料)，只要在Shioaji歷史kbars查詢範圍內就抓得到。背景執行緒跑，
    馬上回應；進度看/api/hub/kline-signals/backfill-today/status。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    # local_only=true（2026-10-05）：盤中只用本機存好的今天 5 分K 重播（從 09:00 起連續的才做），不打永豐歷史 API；
    # 規則改了要馬上補今天的訊號、或盤中重新部署後要把即時狀態補回來時用。
    return start_kline_signal_backfill_today(trade_date=trade_date, local_only=local_only)


@app.get("/api/hub/kline-signals/backfill-today/status")
def get_kline_signal_backfill_today_status() -> dict[str, Any]:
    return {"status": "ok", **kline_signal_backfill_status()}


@app.get("/api/hub/kline-signals/audit-out-of-session")
def audit_kline_signals_out_of_session(trade_date: str | None = Query(None)) -> dict[str, Any]:
    """稽核用：列出trade_date(預設今天)裡，K線訊號家族(905/520/
    1+2多/創高黑龍等)中bar_ts落在09:00~13:30正常盤中時段之外的異常
    資料列——這是盤前試撮tick混入bar聚合器的舊bug留下的髒資料(bug已
    在market_data_hub修掉，這裡只是清點bug修復之前寫入的舊資料)。
    只讀不刪，要實際清除請呼叫
    /api/hub/kline-signals/purge-out-of-session並帶confirm=true。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    rows = find_out_of_session_kline_signals(date)
    return {
        "status": "ok",
        "tradeDate": date,
        "count": len(rows),
        "signals": rows,
    }


@app.get("/api/hub/kline-signals/purge-out-of-session")
def purge_kline_signals_out_of_session(
    trade_date: str | None = Query(None),
    confirm: bool = Query(False),
) -> dict[str, Any]:
    """實際刪除audit-out-of-session會列出的那些時段外髒資料列。要求
    confirm=true才會真的刪，避免誤觸。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    if not confirm:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="要實際刪除請帶 confirm=true")
    deleted = purge_out_of_session_kline_signals(date)
    return {"status": "ok", "tradeDate": date, "deleted": deleted}


@app.get("/api/hub/force/bars/{stock_code}")
def get_persisted_main_force_bars(
    stock_code: str,
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    days: int = Query(31, ge=1, le=400),
    limit: int = Query(20000, ge=1, le=100000),
    backfill: bool = Query(True),
) -> dict[str, Any]:
    """讀取永久保存的主力進出副圖；可選擇建立受限歷史補抓請求。"""
    code = _normalize_stock_code(stock_code)
    date = trade_date
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    bars = load_main_force_bars(code, interval, trade_date=date, days=days, limit=limit)
    backfill_result = None
    if date and backfill:
        try:
            backfill_result = request_main_force_backfill(code, date)
        except ValueError as error:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "status": "ok",
        "code": code,
        "interval": interval,
        "tradeDate": date,
        "bar_count": len(bars),
        "bars": bars,
        "persistent": True,
        "source": "railway_sqlite_shioaji_ticks",
        "backfill": backfill_result,
        # 前端副圖標題下要寫出真正的大戶定義（不是寫死的示範文字）：單筆成交 ≥ 這麼多張就算主力大單
        "mainForceMinLots": main_force_threshold()["minLots"],
        "mainForceMinAmount": main_force_threshold()["minAmount"],
    }


@app.get("/api/hub/force/backfill-status/{stock_code}")
def get_main_force_backfill_status(stock_code: str) -> dict[str, Any]:
    """查詢指定股票的主力副圖背景回補佇列狀態；診斷「為什麼歷史主力買賣力
    還沒補回來」用——status是complete/pending，pending時看attempts跟
    result裡的錯誤訊息判斷是還沒輪到還是每次都失敗。"""
    code = _normalize_stock_code(stock_code)
    return {"status": "ok", "code": code, "jobs": list_main_force_backfill_jobs(code)}


RANKING_HOLD_UNTIL_MINUTE = 8 * 60 + 45  # 下一個交易日開盤前 15 分鐘


def _should_hold_previous_ranking(now: datetime) -> bool:
    """今天還沒有主力資料時，要不要繼續給上一個交易日的排行。使用者 2026-09-24：
    盤中大戶力／族群大戶力／族群綜合表的資料過午夜不能不見，要留到下一個交易日開盤前
    15 分鐘（08:45）。週末、國定假日（trading_days 的休市日曆）整天都留；交易日 08:45 起清空等開盤。"""
    if not is_trading_day(now):
        return True
    return now.hour * 60 + now.minute < RANKING_HOLD_UNTIL_MINUTE


@app.get("/api/hub/main-force/ranking")
def get_main_force_ranking(
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    limit: int = Query(30, ge=1, le=1000),
) -> dict[str, Any]:
    """今日（或指定交易日）主力累計買賣超排行；只讀取既有主力副圖資料，不新增任何 Shioaji 連線。
    limit 上限放寬到 1000：族群大戶力（前端逐族群取排行前幾名）要一次拿到全市場的排行，
    不能被 30／200 檔的預設上限漏掉排名較後面的族群成員。"""
    date = trade_date
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    else:
        date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    # 只排 43 個一般族群裡的股票：收集器也會追蹤開過圖的 ETF 等族群外的代號，使用者 2026-09-23
    # 要求排行不要出現 ETF；2026-09-24 再要求只看 43 個族群（股期標的清單不算）。
    codes = industry_group_codes()
    ranking = load_main_force_ranking(date, interval=interval, limit=limit, codes=codes)
    held_from = None
    if not trade_date and not ranking and _should_hold_previous_ranking(datetime.now(TW_TZ)):
        # 沒指定日期、今天還沒有資料（午夜過後到開盤前）：沿用上一個交易日的最終排行，
        # tradeDate 回真正的資料日期，heldFrom 標示是替哪一天暫留的。
        previous = latest_trade_date_with_data(date, interval=interval)
        if previous:
            ranking = load_main_force_ranking(previous, interval=interval, limit=limit, codes=codes)
            held_from = date
            date = previous
    return {
        "status": "ok",
        "tradeDate": date,
        "heldFrom": held_from,
        "interval": interval,
        "count": len(ranking),
        "ranking": ranking,
        "source": "railway_sqlite_shioaji_ticks",
    }


@app.get("/api/hub/index/otc/status")
def get_otc_index_status() -> dict[str, Any]:
    """櫃買指數 Hub 狀態與最新 Quote。"""
    hub = get_otc_index_hub()
    return {
        "status": "ok",
        "data": hub.get_status(),
        "quote": hub.get_latest_quote(),
    }


@app.get("/api/hub/index/otc/bars")
def get_otc_index_bars(
    include_current: bool = Query(default=False, description="是否包含尚未收棒的目前 5 分 K"),
) -> dict[str, Any]:
    """櫃買指數今日正式 5 分 K。"""
    hub = get_otc_index_hub()
    bars = hub.get_bars_5m(include_current=include_current)
    return {
        "status": "ok",
        "code": OTC_INDEX_HUB_CODE,
        "name": OTC_INDEX_DISPLAY_NAME,
        "interval": "5m",
        "include_current": include_current,
        "bar_count": len(bars),
        "bars": bars,
        "hub": hub.get_status(),
    }


@app.get("/api/hub/index/otc/bars1m")
def get_otc_index_bars_1m(include_current: bool = Query(default=True)) -> dict[str, Any]:
    """櫃買指數今日正式 1 分 K。"""
    hub = get_otc_index_hub()
    bars = hub.get_bars_1m(include_current=include_current)
    return {
        "status": "ok",
        "code": OTC_INDEX_HUB_CODE,
        "name": OTC_INDEX_DISPLAY_NAME,
        "interval": "1m",
        "include_current": include_current,
        "bar_count": len(bars),
        "bars": bars,
        "hub": hub.get_status(),
    }


@app.get("/api/hub/index/otc/strength")
def get_otc_index_strength() -> dict[str, Any]:
    """櫃買盤勢：依5分K換算「站上/跌破20MA」與「站上/跌破今日第3根5K低點」兩個條件。

    兩個條件都成立（都站上）＝強多；都不成立（都跌破）＝強空；一個成立一個
    不成立＝個股震盪。第3根5K只比低點，不看高點（跟20MA一樣是單一門檻的
    上/下二分判斷，不是三段式的上/下/區間內）。

    20MA允許跨日計算（bootstrap_today已經改成回補近幾個交易日的歷史K棒，
    不用每天早上重新等今天自己累積20根、開盤後1小時40分才有第一個訊號）；
    但「今日第3根5K」語意上必須是今天自己的bar，不能被歷史K棒頂替，所以
    另外篩today_bars只給這個門檻用。
    """
    hub = get_otc_index_hub()
    bars = hub.get_bars_5m(include_current=True)
    quote = hub.get_latest_quote()
    now = datetime.now(TW_TZ)
    today = now.strftime("%Y-%m-%d")
    today_bars = [b for b in bars if taipei_trade_date(int(b["ts"])) == today]
    held_from = None
    # 使用者 2026-09-25：過午夜不能變成「資料蒐集中」，上一個交易日收盤時的判斷要留到下一個交易日
    # 開盤前 15 分鐘（08:45），跟大戶力排行同一條規則（週末整天留）；沿用時價格用那天最後一根 5 分 K 的收盤。
    if not today_bars and bars and _should_hold_previous_ranking(now):
        last_day = taipei_trade_date(int(bars[-1]["ts"]))
        if last_day < today:
            held_from, today = today, last_day
            today_bars = [b for b in bars if taipei_trade_date(int(b["ts"])) == today]
            quote = None
    # 櫃買指數今天的漲跌幅（盤中打 333 的「漲幅 ≥ 櫃買%」用）：昨收＝上一個交易日最後一根 5 分 K 的收盤。
    prev_bars = [b for b in bars if taipei_trade_date(int(b["ts"])) < today]
    prev_close = float(prev_bars[-1]["close"]) if prev_bars else None
    quote_close_early = quote.get("close") if quote else None
    early_price = float(quote_close_early) if quote_close_early else (float(today_bars[-1]["close"]) if today_bars else None)
    change_pct = round((early_price / prev_close - 1) * 100, 2) if (prev_close and early_price) else None
    if len(bars) < 20 or not today_bars:
        _kick_otc_index_bootstrap(hub)
        hub_status = hub.get_status()
        bootstrap_error = str(hub_status.get("bootstrap_error") or "").strip()
        return {
            "status": "ok",
            "ready": False,
            "prevClose": prev_close,
            "changePct": change_pct,
            "reason": (
                f"資料不足（5分K {len(bars)}/20 根、今日 {len(today_bars)} 根、"
                f"即時報價{'有' if quote else '無'}），歷史5分K補齊中"
                + (f"；補齊失敗：{bootstrap_error[:220]}" if bootstrap_error else "")
            ),
            "barCount": len(bars),
            "todayBarCount": len(today_bars),
            "hub": hub_status,
        }
    closes = [float(b["close"]) for b in bars[-20:]]
    ma20 = sum(closes) / len(closes)
    # 收盤後、或剛重啟還沒收到第一筆報價時，用今天最後一根 5 分 K 的收盤價；有即時報價就用報價。
    quote_close = quote.get("close") if quote else None
    price_source = "quote" if quote_close else "lastBar"
    price = float(quote_close) if quote_close else float(today_bars[-1]["close"])
    above_ma20 = price > ma20

    ref_bar = today_bars[2] if len(today_bars) > 2 else today_bars[0]
    ref_low = float(ref_bar["low"])
    above_ref_low = price > ref_low

    if above_ma20 and above_ref_low:
        label = "強多"
    elif not above_ma20 and not above_ref_low:
        label = "強空"
    else:
        label = "個股震盪"

    return {
        "status": "ok",
        "ready": True,
        "label": label,
        "price": price,
        "prevClose": prev_close,
        "changePct": round((price / prev_close - 1) * 100, 2) if prev_close else None,
        "ma20": round(ma20, 2),
        "aboveMa20": above_ma20,
        "refBarIndex": 3,
        "refLow": ref_low,
        "aboveRefLow": above_ref_low,
        "priceSource": price_source,
        "tradeDate": today,
        "heldFrom": held_from,  # 有值＝現在還沒到下一個交易日 08:45，畫面上是 tradeDate 那天收盤時的判斷
        "updatedAt": datetime.now(TW_TZ).isoformat(),
        "hub": hub.get_status(),
    }


@app.get("/api/hub/group-daily-changes")
def get_group_daily_changes_endpoint(days: int = Query(3, ge=1, le=10)) -> dict[str, Any]:
    """43 個族群最近幾個交易日的平均漲跌幅與名次（昨天、前天…），給盤中打 333 的 188 做多／199 做空
    名單與 ⚔️🔪 標記用；來源是官方日K，半小時快取。"""
    from group_daily_changes import get_group_daily_changes

    return get_group_daily_changes(days)


@app.get("/api/hub/group-quotes")
def get_group_quotes_endpoint(codes: str = Query("", description="代號，逗號分隔（最多 600 檔）")) -> Any:
    """證交所即時報價代抓（2026-10-06 使用者：tw-groups 首頁報價在使用者那邊一直抓不到，醞釀／發動、刀劍空全空）。
    tw-groups worker 自己向證交所抓失敗時改問這裡：每檔 price／prevClose／change／changePercent／open／
    limitUp／limitDown／volume／name，加上報價日期時間與沒抓到的檔數；同一批代號 10 秒內共用一份。"""
    try:
        return group_quotes_payload(codes)
    except ValueError as error:
        return JSONResponse({"status": "error", "error": str(error)}, status_code=400)
    except Exception as error:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"證交所報價抓不到：{error}"}, status_code=502)


@app.get("/api/hub/session-close")
def get_session_close_endpoint() -> dict[str, Any]:
    """休市日（週末、假日）與交易日 08:45 前，tw-groups 首頁／盤中333 等即時行情改顯示上一個交易日收盤用：
    回現在該不該暫留（held）、要顯示的那個交易日（session），以及族群成員那天的日K收盤／漲跌／成交量／
    漲跌停旗標。使用者 2026-10-04：週末 TWSE 測試盤會回假價，不能再拿即時報價。快取 5 分鐘。"""
    from session_close import get_session_close

    return get_session_close()


@app.get("/api/hub/brew-launch")
def get_brew_launch_endpoint(codes: str | None = Query(None, description="只回這些代號（逗號分隔），查特定個股用")) -> dict[str, Any]:
    """醞釀／發動選股（43 個族群成員）：每檔的近 10 日箱頂／箱底、六條均線與均線分數、給前端用即時價
    重算均線的部分和、5 日均量、發行張數，以及上一個交易日收盤是否符合「醞釀」；「發動」要用即時價量，
    由前端判斷。來源是官方日K＋市值，半小時快取。"""
    from brew_launch import get_brew_launch

    payload = get_brew_launch()
    if codes:
        wanted = {code.strip().upper() for code in codes.split(",") if code.strip()}
        payload = dict(payload, stocks={code: info for code, info in payload["stocks"].items() if code in wanted})
    return payload


@app.get("/api/hub/brew-launch/history")
def get_brew_launch_history(days: int = Query(10, ge=1, le=60), date: str | None = Query(None)) -> dict[str, Any]:
    """醞釀／發動每日保存：每個交易日的醞釀名單快照＋盤中第一次發動的紀錄（時間、價格、分數、周轉）。
    使用者 2026-09-25：訊號要永久保存，不能明天就不見。"""
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="date 必須是 YYYY-MM-DD") from exc
    return {**brew_launch_history(days=days, date=date), "scan": brew_launch_scan_status()}


@app.post("/api/hub/brew-launch/purge-false-relaunches")
def post_brew_launch_purge_false_relaunches(date: str | None = Query(None), dry_run: bool = Query(True)) -> dict[str, Any]:
    """刪掉當天確定是假的發動紀錄：兩筆之間 1 分K 從沒跌回箱頂以下、分數也沒掉下門檻的「重新發動」；
    收盤後再加上 13:26 以後收盤試撮時段記到、但收盤那一盤沒成交或收盤價不是發動的。
    預設 dry_run=true 只回報會刪哪些；確認後帶 dry_run=false 才真的刪。"""
    return brew_launch_purge_false_relaunches(trade_date=date, dry_run=dry_run)


@app.post("/api/hub/watchlist/load")
def post_watchlist_load(body: dict[str, Any] = Body(default_factory=dict)) -> Any:
    """自選股（2026-10-05 使用者）：用同步碼讀清單；沒存過回空清單（版本 0）。同步碼放在內容裡，不放網址。"""
    try:
        return watchlist_load(body.get("key"))
    except WatchlistError as error:
        return JSONResponse({"status": "error", "error": str(error)}, status_code=400)


@app.post("/api/hub/watchlist/save")
def post_watchlist_save(body: dict[str, Any] = Body(default_factory=dict)) -> Any:
    """自選股存檔：body＝{key, data, baseVersion}。別台電腦已經先存過（版本不同）回 409＋最新的一份，不會互相蓋掉。"""
    try:
        result = watchlist_save(body.get("key"), body.get("data"), body.get("baseVersion"))
    except WatchlistError as error:
        return JSONResponse({"status": "error", "error": str(error)}, status_code=400)
    return JSONResponse(result, status_code=409) if result["status"] == "conflict" else result


@app.post("/api/hub/brew-launch/backfill-holder-force")
def post_brew_launch_backfill_holder_force(
    date: str | None = Query(None),
    days: int = Query(10, ge=1, le=60),
) -> dict[str, Any]:
    """手動觸發：幫發動永久紀錄裡還沒有盤中大戶力（strengthPct 等欄位是 None）的舊列，
    用當時存的 trade_date 重新查一次主力排行補回去。給 date 就只補那天，不給就補最近
    days 天內有發動紀錄的日子。冪等，可以重複觸發。"""
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="date 必須是 YYYY-MM-DD") from exc
    return {"status": "ok", **brew_launch_backfill_holder_force([date] if date else None, limit_dates=days)}


@app.get("/api/hub/chips/daily")
def get_chips_daily(date: str | None = Query(None)) -> dict[str, Any]:
    """盤後籌碼排行（第一步）：43 個族群股票那一天的主力大單淨額、三大法人買賣超（張）、連續天數、收盤與漲跌幅。
    date 沒給就是最新有法人資料的交易日。使用者 2026-09-25：盤後籌碼排行先做。"""
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="date 必須是 YYYY-MM-DD") from exc
    return chips_daily_payload(date)


@app.get("/api/hub/chips/status")
def get_chips_status() -> dict[str, Any]:
    return {"status": "ok", **chips_collector_status()}


@app.post("/api/hub/chips/collect")
def post_chips_collect() -> dict[str, Any]:
    """立刻抓一次（tw-groups 的排程工作流程把上櫃資料推到鏡像後會戳這裡）。"""
    return {"status": "ok", "result": run_chips_collect()}


@app.get("/api/hub/swing-report")
def get_swing_report(date: str | None = Query(None)) -> dict[str, Any]:
    """波段日報（第一階段）：今日摘要、產業觀察、籌碼面／技術面／均線轉強精選，每檔附防守價與風險；
    date 沒給就是最新一份，可回看近 10 個交易日（2026-09-26 使用者：照波段精選日報做）。"""
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="date 必須是 YYYY-MM-DD") from exc
    return swing_report_payload(date)


@app.post("/api/hub/swing-report/refresh")
def post_swing_report_refresh() -> dict[str, Any]:
    """立刻重算最新一天（並補齊近幾天沒存的）。"""
    return {"status": "ok", "result": run_swing_report()}


@app.get("/api/hub/fundamentals/status")
def get_fundamentals_status() -> dict[str, Any]:
    return {"status": "ok", **fundamentals_status()}


@app.post("/api/hub/fundamentals/collect")
def post_fundamentals_collect(force: int = Query(0)) -> dict[str, Any]:
    """立刻抓一次本益比、月營收、股本、集保週資料（排程主機推完鏡像會戳這裡），抓完重算波段日報；force=1 本益比與集保都重抓。"""
    return {"status": "ok", "result": run_fundamentals_collect(force=bool(force))}


@app.get("/api/hub/etf/status")
def get_etf_status() -> dict[str, Any]:
    return {"status": "ok", **etf_status()}


@app.post("/api/hub/etf/collect")
def post_etf_collect() -> dict[str, Any]:
    """立刻從鏡像拉主動式 ETF 五檔的持股（排程主機晚上推完會戳這裡），有新的就重算下午報。"""
    return {"status": "ok", "result": run_etf_collect()}


@app.get("/api/hub/heilong")
def get_heilong(
    score: int = Query(10), k: str = Query("black"), lo: float = Query(-10.0, alias="min"), hi: float = Query(3.0, alias="max"),
    week: float | None = Query(None), gavg: float | None = Query(None), hits: int | None = Query(None), val: float | None = Query(None),
    exdispo: int = Query(1), cap: int = Query(0), sort: str = Query("score"), tp: float = Query(3.0), mine: str = Query("both"),
    days: int = Query(10), amt: float = Query(50.0), algo: str = Query("site"),
    bmin: float | None = Query(None), bmax: float | None = Query(None), exattn: int = Query(0), exout: int = Query(0),
    pmin: float | None = Query(None), pmax: float | None = Query(None), vmin: int | None = Query(None), fut: int = Query(0),
    scope: str = Query("groups"), fee: float = Query(0.0),
) -> dict[str, Any]:
    """黑龍回測：照參數（均線分數、K棒、漲跌幅、週籌碼、族群平均分、近 20 日漲逾 8% 次數、5 日均成交值、排除處置、每天最多幾檔；
    2026-10-04 加月季乖離、排除注意股、排除剛出關、收盤價範圍、當天成交量、只看有股期、範圍族群表內／全市場、費用）
    挑每天的名單，用 D+1 開高低收算各種出場方式的績效、累積曲線、爆發力，附今日名單與每日明細（2026-09-28 使用者：照創高黑龍績效分析做）。"""
    from fastapi import HTTPException

    try:
        return heilong_backtest_payload({
            "score": score, "k": k, "min": lo, "max": hi, "week": week, "gavg": gavg, "hits": hits, "val": val,
            "exdispo": exdispo, "cap": cap, "sort": sort, "tp": tp, "mine": mine, "days": days, "amt": amt, "algo": algo,
            "bmin": bmin, "bmax": bmax, "exattn": exattn, "exout": exout, "pmin": pmin, "pmax": pmax, "vmin": vmin, "fut": fut,
            "scope": scope, "fee": fee,
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/hub/bars-history/status")
def get_bars_history_status() -> dict[str, Any]:
    """日K補到三年的進度（上市逐日、上櫃鏡像、還原事件）與目前涵蓋範圍。"""
    return {"status": "ok", **bars_history_status(), "coverage": bars_history_coverage()}


@app.post("/api/hub/bars-history/run")
def post_bars_history_run() -> dict[str, Any]:
    """立刻補一輪（背景跑，馬上回狀態）；tw-groups 的上櫃日K鏡像推完會戳這裡。"""
    return run_bars_history()


@app.get("/api/hub/price-adjust/events")
def get_price_adjust_events(limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """分割減資還原事件（最新的在前）：來源 twse／tpex 官方表、tpex-quote 櫃買行情反推、inferred 日K推測。"""
    return {"status": "ok", "events": list_price_adjust_events(limit)}


@app.get("/api/hub/picker")
def get_picker(request: Request) -> dict[str, Any]:
    """創高黑選股（2026-10-04 使用者：照莊爸 App「創高黑」做）：view＝today（模擬帳戶、今天要做）／picks（選股漏斗、每週名單、
    每日新進、條件池）／perf（實績：各種出場方式）／rules；其餘查詢參數是模組參數（見 heilong_picker.DEFAULTS），
    date＝看哪一天、week＝每週名單看第幾週、lists＝使用者改過的名單『週一:代號,代號;…』、stars＝設成優先的代號。"""
    from fastapi import HTTPException

    query = dict(request.query_params)
    view = query.pop("view", "today")
    day = query.pop("date", None)
    week = query.pop("week", None)
    lists = query.pop("lists", None)
    stars = query.pop("stars", None)
    try:
        return picker_payload(view, query, day=day, week=int(week) if week not in (None, "") else None, lists=lists, stars=stars)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/hub/chip-radar")
def get_chip_radar(week: str | None = Query(None)) -> dict[str, Any]:
    """籌碼暴增雷達（2026-10-04 使用者：照莊爸 zhuang.tw/radar 做）：集保週資料的大戶增減，本週買超／賣超榜（最近 8 週）、
    上榜累積榜、族群排名、連續增排行、熱門股。week＝看哪一週（集保結算日 YYYY-MM-DD），不給＝最新一週。"""
    from fastapi import HTTPException

    try:
        return chip_radar_payload(week or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/hub/chip-radar/stock")
def get_chip_radar_stock(code: str = Query(...)) -> dict[str, Any]:
    """籌碼暴增雷達的個股查詢：九週籌碼軌跡、同族群當週排名、三大法人（每週加總、近 5 日）。"""
    from fastapi import HTTPException

    try:
        return chip_radar_stock(code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/hub/heilong/status")
def get_heilong_status() -> dict[str, Any]:
    return {"status": "ok", **heilong_status()}


@app.post("/api/hub/heilong/rebuild")
def post_heilong_rebuild(force: int = Query(0)) -> dict[str, Any]:
    """立刻補齊／重算黑龍表（force=1 全部重算）。"""
    return {"status": "ok", "result": rebuild_heilong(force=bool(force))}


@app.get("/api/hub/checkup")
def get_checkup(codes: str = Query("")) -> dict[str, Any]:
    """每日持股健診（2026-09-28 使用者）：codes＝『2481/2408,2344』這樣的股號清單，回每檔的三面向分數、綜合、防守線、
    七科小體檢；資料是收盤後跟下午報一起算好存的。"""
    return checkup_payload(codes)


@app.get("/api/hub/diag")
def get_diag(code: str = Query("")) -> dict[str, Any]:
    """個股問診（2026-09-28 使用者）：一檔的三面向分數、七科、防守線、近 120 根日K、同族對照、族群強度榜與今日名單、穿惡名單。"""
    return diag_payload(code)


@app.get("/api/hub/checkup/status")
def get_checkup_status() -> dict[str, Any]:
    return {"status": "ok", **checkup_status()}


@app.post("/api/hub/checkup/rebuild")
def post_checkup_rebuild() -> dict[str, Any]:
    """立刻重算最新一天的健診表。"""
    return {"status": "ok", "result": rebuild_checkup()}


@app.get("/api/hub/bars1d/{stock_code}")
def get_daily_bars(
    stock_code: str,
    limit: int = Query(260, ge=1, le=2000),
) -> dict[str, Any]:
    """個股日K；來源是官方 TWSE/TPEx 盤後資料（跟 Shioaji 訂閱無關），
    背景收集器每天定期回補最新交易日，並只保留最近365個交易日。

    每根日K會補上 mainNet（當天主力淨量），來源是主力副圖 5 分K依交易日
    彙總；主力副圖只保留最近約30個交易日，比這個範圍舊的日K會沒有 mainNet
    （null），是保留政策造成的預期限制。"""
    code = _normalize_stock_code(stock_code)
    bars = load_daily_bars(code, limit=limit)
    daily_net = load_daily_main_force_net(code)
    for bar in bars:
        bar["mainNet"] = daily_net.get(str(bar["ts"])[:10])
    return {
        "status": "ok",
        "code": code,
        "interval": "1d",
        "bar_count": len(bars),
        "bars": bars,
        "source": "twse_tpex_official_after_hours",
    }


@app.get("/api/hub/after-hours-fixed-price")
def get_after_hours_fixed_price(
    trade_date: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    latest: bool = Query(False),
) -> dict[str, Any]:
    """盤後定價交易（14:00-14:30撮合，14:30公布）成交價/成交量排行；來源是官方
    TWSE盤後公開資料（exchangeReport/BFT41U），跟Shioaji訂閱無關。14:35前或
    尚未收集到當天資料時，entries會是空list（不是錯誤，是還沒公布）。
    latest=true（且沒指定trade_date）時改回最近一個已收集的交易日——今天還沒
    公布就是前一個交易日——tradeDate/isToday會標明實際是哪一天的資料。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    if latest and not trade_date:
        latest_date, entries = load_latest_after_hours_day(limit=limit, on_or_before=today)
        date = latest_date or today
    else:
        date = trade_date or today
        entries = load_after_hours_day(date, limit=limit)
    return {
        "status": "ok",
        "tradeDate": date,
        "isToday": date == today,
        "count": len(entries),
        "entries": entries,
        "source": "twse_official_after_hours_fixed_price",
    }


@app.get("/api/hub/history5m/{stock_code}")
def get_strategy_history_5m(
    stock_code: str,
    calendar_days: int = Query(14, ge=3, le=31),
) -> dict[str, Any]:
    """股票 K 線歷史資料；保留供即時行情頁面的個股圖表使用。"""
    code = _normalize_stock_code(stock_code)
    return get_stock_history_bars_5m(code, calendar_days=calendar_days)


@app.get("/api/hub/history1m/{stock_code}")
def get_strategy_history_1m(
    stock_code: str,
    calendar_days: int = Query(5, ge=3, le=10),
) -> dict[str, Any]:
    """股票1分K多日歷史（含今天）；跟history5m共用同一份Shioaji多日kbars快取，
    不會為了1分K多打一次Shioaji歷史查詢。"""
    code = _normalize_stock_code(stock_code)
    return get_stock_history_bars_1m(code, calendar_days=calendar_days)
