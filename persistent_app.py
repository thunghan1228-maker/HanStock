"""HanStock 持久化 app：只保留台股即時行情與主力副圖所需的持久化端點。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Query

from hanstock_app import app, _normalize_stock_code
from main_force_collector import start_main_force_collector
from main_force_store import load_daily_main_force_net, load_main_force_bars, load_main_force_ranking, main_force_storage_status
from main_force_backfill_jobs import request_main_force_backfill
from intraday_large_order_collector import start_intraday_large_order_collector, collector_status as large_order_collector_status
from four_gate_signals_collector import start_four_gate_signals_collector
from daily_bars_collector import start_daily_bars_collector
from daily_bars_store import daily_bars_storage_status, load_daily_bars
from after_hours_fixed_price_collector import start_after_hours_fixed_price_collector
from after_hours_fixed_price import load_after_hours_day
from otc_gap_backfill import start_otc_gap_backfill, backfill_state as otc_gap_backfill_state
from four_gate_signals import fix_stale_four_gate_labels
from intraday_signal_store import load_latest_signals, load_latest_signals_by_kind, load_recent_trade_dates, load_signals_for_ticker, find_out_of_session_kline_signals, purge_out_of_session_kline_signals
from intraday_kline_signals import start_kline_signal_backfill_today, kline_signal_backfill_status
from otc_index import OTC_INDEX_DISPLAY_NAME, OTC_INDEX_HUB_CODE, TW_TZ, taipei_trade_date
from otc_index_hub import get_otc_index_hub
from stock_history_service import get_stock_history_bars_5m
from stock_bar_bootstrap import stock_bar_repair_status
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
            start_main_force_collector()
            start_intraday_large_order_collector()
            start_four_gate_signals_collector()
            start_daily_bars_collector()
            start_after_hours_fixed_price_collector()
            start_otc_gap_backfill()
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
            "otcGapBackfillEnabled": os.getenv(
                "HANSTOCK_OTC_GAP_BACKFILL_ENABLED", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
            "otcGapBackfill": otc_gap_backfill_state(),
            "stockBarAutoRepairEnabled": False,
            "stockBarAutoRepair": stock_bar_repair_status(),
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
    return {"status": "ok", "data": service.get_stock_health()}


@app.get("/api/hub/intraday-signals")
def get_intraday_signals(
    trade_date: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, ge=1, le=5000),
) -> dict[str, Any]:
    """讀取已永久保存的盤中訊號。

    5 分鐘K線結構性訊號(12空/1+2多/創高黑龍等)由intraday_kline_signals.py
    在本機即時偵測寫入；即時大單與四項精選也共用同一個永久訊號表。此端點
    只讀取已保存資料，不對外連線。
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
        else load_latest_signals(date, limit=limit)
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
    """單一股票當日所有已保存K線訊號（905/12空/1+2多/520等），依時間
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
def trigger_kline_signal_backfill_today(trade_date: str | None = Query(None)) -> dict[str, Any]:
    """一次性回補：用Shioaji歷史kbars重播trade_date(預設今天)已經走完的
    5分K，補回12空/905/520/1+2多/創高黑龍等訊號偵測引擎剛上線那天漏掉的
    部分。也可以指定過去幾天內的日期(例如假日想先驗證上一個交易日的
    資料)，只要在Shioaji歷史kbars查詢範圍內就抓得到。背景執行緒跑，
    馬上回應；進度看/api/hub/kline-signals/backfill-today/status。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    return start_kline_signal_backfill_today(trade_date=trade_date)


@app.get("/api/hub/kline-signals/backfill-today/status")
def get_kline_signal_backfill_today_status() -> dict[str, Any]:
    return {"status": "ok", **kline_signal_backfill_status()}


@app.get("/api/hub/kline-signals/audit-out-of-session")
def audit_kline_signals_out_of_session(trade_date: str | None = Query(None)) -> dict[str, Any]:
    """稽核用：列出trade_date(預設今天)裡，K線訊號家族(12空/905/520/
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
    }


@app.get("/api/hub/main-force/ranking")
def get_main_force_ranking(
    interval: str = Query("5m", pattern="^(1m|5m)$"),
    trade_date: str | None = Query(None),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    """今日（或指定交易日）主力累計買賣超排行；只讀取既有主力副圖資料，不新增任何 Shioaji 連線。"""
    date = trade_date
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    else:
        date = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    ranking = load_main_force_ranking(date, interval=interval, limit=limit)
    return {
        "status": "ok",
        "tradeDate": date,
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
    today = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    today_bars = [b for b in bars if taipei_trade_date(int(b["ts"])) == today]
    if len(bars) < 20 or not quote or not today_bars:
        return {
            "status": "ok",
            "ready": False,
            "reason": "資料不足（需要至少20根5分K歷史與今日即時報價）",
            "barCount": len(bars),
            "todayBarCount": len(today_bars),
            "hub": hub.get_status(),
        }
    closes = [float(b["close"]) for b in bars[-20:]]
    ma20 = sum(closes) / len(closes)
    price = float(quote.get("close") or bars[-1]["close"])
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
        "ma20": round(ma20, 2),
        "aboveMa20": above_ma20,
        "refBarIndex": 3,
        "refLow": ref_low,
        "aboveRefLow": above_ref_low,
        "updatedAt": datetime.now(TW_TZ).isoformat(),
        "hub": hub.get_status(),
    }


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
) -> dict[str, Any]:
    """盤後定價交易（14:00-14:30撮合，14:30公布）成交價/成交量排行；來源是官方
    TWSE盤後公開資料（exchangeReport/BFT41U），跟Shioaji訂閱無關。14:35前或
    尚未收集到當天資料時，entries會是空list（不是錯誤，是還沒公布）。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="trade_date 必須是 YYYY-MM-DD") from exc
    date = trade_date or datetime.now(TW_TZ).strftime("%Y-%m-%d")
    entries = load_after_hours_day(date, limit=limit)
    return {
        "status": "ok",
        "tradeDate": date,
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
