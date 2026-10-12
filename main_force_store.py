"""個股 1 分／5 分主力進出副圖的 Railway SQLite 永久保存層。"""

from __future__ import annotations

from datetime import datetime
import threading
from typing import Any, Iterable

import database
from otc_index import is_regular_otc_session, taipei_trade_date

_table_lock = threading.Lock()
_table_ready_path: str | None = None


def _ensure_table() -> None:
    global _table_ready_path
    database_path = str(database.DATABASE_PATH.resolve())
    if _table_ready_path == database_path:
        return
    with _table_lock:
        if _table_ready_path == database_path:
            return
        with database.get_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS main_force_bars (
                    stock_code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    bar_ts INTEGER NOT NULL,
                    main_buy_volume INTEGER NOT NULL DEFAULT 0,
                    main_sell_volume INTEGER NOT NULL DEFAULT 0,
                    main_net_volume INTEGER NOT NULL DEFAULT 0,
                    main_buy_amount REAL NOT NULL DEFAULT 0,
                    main_sell_amount REAL NOT NULL DEFAULT 0,
                    main_net_amount REAL NOT NULL DEFAULT 0,
                    main_tick_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (stock_code, interval, bar_ts)
                );
                CREATE INDEX IF NOT EXISTS idx_main_force_code_interval_date
                ON main_force_bars (stock_code, interval, trade_date, bar_ts);
                """
            )
            row_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(main_force_bars)").fetchall()
            }
            if "total_amount" not in row_columns:
                # 當日累計成交金額（來自Shioaji tick本身，不是這根bar自己的
                # 成交額），給大戶力等需要「淨額佔當日總成交額比例」的指標
                # 當分母用。舊資料沒有這個欄位，補上後預設0（代表資料缺失，
                # 不是真的成交額0）。
                connection.execute(
                    "ALTER TABLE main_force_bars ADD COLUMN total_amount REAL NOT NULL DEFAULT 0"
                )
        _table_ready_path = database_path


def _rows_for_bars(
    stock_code: str,
    interval: str,
    bars: Iterable[dict[str, Any]],
    now: str,
) -> list[tuple[Any, ...]]:
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    code = str(stock_code).strip().upper()
    rows: list[tuple[Any, ...]] = []
    for bar in bars:
        if not isinstance(bar, dict) or not bar.get("main_force_available"):
            continue
        try:
            ts = int(bar["ts"])
            buy_volume = max(0, int(bar.get("main_buy_volume", 0) or 0))
            sell_volume = max(0, int(bar.get("main_sell_volume", 0) or 0))
            buy_amount = max(0.0, float(bar.get("main_buy_amount", 0) or 0))
            sell_amount = max(0.0, float(bar.get("main_sell_amount", 0) or 0))
            tick_count = max(0, int(bar.get("main_tick_count", 0) or 0))
            total_amount = max(0.0, float(bar.get("total_amount", 0) or 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if ts <= 0:
            continue
        if not is_regular_otc_session(ts):
            # main_force_bars 只代表盤中連續交易（09:00-13:30）逐筆主力統計；
            # 盤後定價撮合（14:00-14:30）若被上游誤判成一根新K棒，不能混進來，
            # 否則MAX(bar_ts)會被撮合時間蓋掉，看起來像「只有盤後那筆資料」。
            continue
        rows.append((
            code, taipei_trade_date(ts), interval, ts,
            buy_volume, sell_volume, buy_volume - sell_volume,
            buy_amount, sell_amount, buy_amount - sell_amount,
            tick_count, total_amount, now,
        ))
    return rows


def _write_rows(rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    _ensure_table()
    with database.get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO main_force_bars (
                stock_code, trade_date, interval, bar_ts,
                main_buy_volume, main_sell_volume, main_net_volume,
                main_buy_amount, main_sell_amount, main_net_amount,
                main_tick_count, total_amount, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, interval, bar_ts) DO UPDATE SET
                trade_date=excluded.trade_date,
                main_buy_volume=excluded.main_buy_volume,
                main_sell_volume=excluded.main_sell_volume,
                main_net_volume=excluded.main_net_volume,
                main_buy_amount=excluded.main_buy_amount,
                main_sell_amount=excluded.main_sell_amount,
                main_net_amount=excluded.main_net_amount,
                main_tick_count=excluded.main_tick_count,
                total_amount=excluded.total_amount,
                updated_at=excluded.updated_at
            """,
            rows,
        )
    return len(rows)


def save_main_force_bars(stock_code: str, interval: str, bars: Iterable[dict[str, Any]]) -> int:
    """只保存真正含有主力逐筆統計的 K 棒；不以零值偽造缺漏資料。"""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return _write_rows(_rows_for_bars(stock_code, interval, bars, now))


def save_main_force_batches(
    entries: Iterable[tuple[str, str, Iterable[dict[str, Any]]]],
) -> int:
    """在單一交易中批次保存多檔股票，避免每分鐘建立上千個 SQLite 寫入交易。"""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    rows: list[tuple[Any, ...]] = []
    for stock_code, interval, bars in entries:
        rows.extend(_rows_for_bars(stock_code, interval, bars, now))
    return _write_rows(rows)


def load_main_force_bars(
    stock_code: str,
    interval: str,
    *,
    trade_date: str | None = None,
    days: int = 31,
    limit: int = 20000,
) -> list[dict[str, Any]]:
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    code = str(stock_code).strip().upper()
    params: list[Any] = [code, interval]
    if trade_date:
        date_filter = "AND trade_date = ?"
        params.append(trade_date)
    else:
        date_filter = ""
    with database.get_connection() as connection:
        if not trade_date:
            recent_dates = connection.execute(
                """
                SELECT trade_date
                FROM main_force_bars
                WHERE stock_code = ? AND interval = ?
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (code, interval, max(1, min(days, 400))),
            ).fetchall()
            if not recent_dates:
                return []
            date_filter = "AND trade_date >= ?"
            params = [code, interval, recent_dates[-1]["trade_date"]]
        row_limit = max(1, min(limit, 100000))
        params.append(row_limit)
        rows = connection.execute(
            f"""
            SELECT trade_date, bar_ts, main_buy_volume, main_sell_volume,
                   main_net_volume, main_buy_amount, main_sell_amount,
                   main_net_amount, main_tick_count, total_amount
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ? {date_filter}
            ORDER BY bar_ts DESC LIMIT ?
            """,
            params,
        ).fetchall()
    rows = list(reversed(rows))
    return [{
        "trade_date": row["trade_date"], "ts": int(row["bar_ts"]),
        "main_buy_volume": int(row["main_buy_volume"]),
        "main_sell_volume": int(row["main_sell_volume"]),
        "main_net_volume": int(row["main_net_volume"]),
        "main_buy_amount": round(float(row["main_buy_amount"])),
        "main_sell_amount": round(float(row["main_sell_amount"])),
        "main_net_amount": round(float(row["main_net_amount"])),
        "main_tick_count": int(row["main_tick_count"]),
        # 當日累計成交金額；0可能是真的0，也可能是這個欄位上線前的舊資料。
        "total_amount": round(float(row["total_amount"] or 0)),
        "main_force_available": True,
    } for row in rows]


def list_main_force_codes_for_date(trade_date: str, interval: str = "1m") -> list[str]:
    """trade_date 當天有主力副圖 K 棒落盤的股票代號（＝當天曾被訂閱到、有 tick 的股票）。"""
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT stock_code FROM main_force_bars
            WHERE interval = ? AND trade_date = ?
            ORDER BY stock_code
            """,
            (interval, trade_date),
        ).fetchall()
    return [str(row["stock_code"]) for row in rows]


HOLDER_STRENGTH_MIN_TURNOVER = 100_000_000  # 累計成交額至少1億元才有正式門檻資格
HOLDER_STRENGTH_MIN_NET_AMOUNT = 30_000_000  # 大戶淨額絕對值至少3,000萬元
HOLDER_STRENGTH_SIGNAL_PCT = 12.0  # 正式訊號門檻：多方≥+12%；空方≤-12%
HOLDER_STRENGTH_STRONG_PCT = 28.0  # 強力標籤門檻：絕對值28%


def compute_holder_strength_pct(buy_amount: float, sell_amount: float, total_amount: float) -> float | None:
    """大戶力% =（累計大單買進金額－累計大單賣出金額）÷累計成交額×100%。
    total_amount<=0（還沒有今日累計成交額資料，例如total_amount欄位上線前
    的舊資料）時回None，代表無法計算，不能當成0%。"""
    if total_amount <= 0:
        return None
    return (buy_amount - sell_amount) / total_amount * 100


def classify_holder_strength(
    buy_amount: float, sell_amount: float, total_amount: float
) -> tuple[float | None, str | None]:
    """回傳（大戶力%, 標籤）。標籤只在通過正式門檻（累計成交額≥1億、大戶
    淨額絕對值≥3,000萬、且百分比達±12%）時才有值；沒通過門檻時百分比仍會
    算出來，但標籤是None（代表還沒到正式訊號，不是沒有數字）。"""
    pct = compute_holder_strength_pct(buy_amount, sell_amount, total_amount)
    if pct is None:
        return None, None
    net_amount = buy_amount - sell_amount
    eligible = total_amount >= HOLDER_STRENGTH_MIN_TURNOVER and abs(net_amount) >= HOLDER_STRENGTH_MIN_NET_AMOUNT
    if not eligible:
        return round(pct, 2), None
    if pct >= HOLDER_STRENGTH_STRONG_PCT:
        label = "盤中大戶強力買進"
    elif pct >= HOLDER_STRENGTH_SIGNAL_PCT:
        label = "盤中大戶偏買"
    elif pct <= -HOLDER_STRENGTH_STRONG_PCT:
        label = "盤中大戶強力賣出"
    elif pct <= -HOLDER_STRENGTH_SIGNAL_PCT:
        label = "盤中大戶偏賣"
    else:
        label = None
    return round(pct, 2), label


def load_main_force_ranking(
    trade_date: str,
    interval: str = "5m",
    limit: int = 30,
    codes: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """依交易日彙總主力累計買賣超排行；只讀取既有已收集資料，不新增任何即時訂閱。

    strengthPct/holderLabel 是官方大戶力公式（大單淨額÷累計成交額×100%，
    見classify_holder_strength）；total_amount欄位上線前的舊資料兩者都會
    是None，代表當時沒有累計成交額資料可以算，不是0%。

    codes 有給就只排這些代號：主力副圖收集器會追蹤任何開過圖的股票（含 ETF），對外的
    排行端點要限縮在 stock_groups 的官方族群範圍。limit 上限 1000（不是 200）：族群大戶力
    要一次拿到全市場排行，夾在 200 會漏掉排名較後面的族群成員。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    limit = max(1, min(int(limit), 1000))
    code_clause = ""
    code_params: list[str] = []
    if codes is not None:
        code_params = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
        if not code_params:
            return []
        code_clause = f" AND b.stock_code IN ({', '.join('?' for _ in code_params)})"
    with database.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT b.stock_code,
                   SUM(b.main_net_volume) AS net_volume,
                   SUM(b.main_buy_volume) AS buy_volume,
                   SUM(b.main_sell_volume) AS sell_volume,
                   SUM(b.main_buy_amount) AS buy_amount,
                   SUM(b.main_sell_amount) AS sell_amount,
                   MAX(b.total_amount) AS total_amount,
                   MAX(b.bar_ts) AS last_ts,
                   s.stock_name AS stock_name
            FROM main_force_bars b
            LEFT JOIN stocks s ON s.stock_code = b.stock_code
            WHERE b.trade_date = ? AND b.interval = ?{code_clause}
            GROUP BY b.stock_code
            ORDER BY ABS(SUM(b.main_net_volume)) DESC
            LIMIT ?
            """,
            (trade_date, interval, *code_params, limit),
        ).fetchall()
    from stock_trading_eligibility import get_trading_eligibility

    results = []
    for row in rows:
        buy_amount = float(row["buy_amount"] or 0)
        sell_amount = float(row["sell_amount"] or 0)
        total_amount = float(row["total_amount"] or 0)
        strength_pct, holder_label = classify_holder_strength(buy_amount, sell_amount, total_amount)
        code = row["stock_code"]
        # 融資/融券/可現股當沖/有股期跟大戶力本身無關，抓不到(Shioaji未登入等)
        # 就是這幾個欄位維持None，不影響大戶力排行本身。
        try:
            eligibility = get_trading_eligibility(code)
        except Exception:  # noqa: BLE001
            eligibility = {"marginable": None, "shortable": None, "dayTradeEligible": None, "hasStockFutures": None}
        results.append({
            "code": code,
            "name": row["stock_name"] or code,
            "netVolume": int(row["net_volume"] or 0),
            "buyVolume": int(row["buy_volume"] or 0),
            "sellVolume": int(row["sell_volume"] or 0),
            "netAmount": round(buy_amount - sell_amount),  # 大戶買賣超金額(元)，正=買超
            "lastTs": int(row["last_ts"]),
            "side": "buy" if (row["net_volume"] or 0) >= 0 else "sell",
            "strengthPct": strength_pct,
            "holderLabel": holder_label,
            "marginable": eligibility["marginable"],
            "shortable": eligibility["shortable"],
            "dayTradeEligible": eligibility["dayTradeEligible"],
            "hasStockFutures": eligibility["hasStockFutures"],
        })
    return results


HOLDER_TIMELINE_MIN_NET_VOLUME = 500  # 盤中大戶力分頁的門檻：累計大單淨量 ≥ 500 張（前端 BIG_HOLDER_THRESHOLD）


def load_holder_timeline(
    trade_date: str,
    interval: str = "5m",
    codes: Iterable[str] | None = None,
    min_net_volume: int = HOLDER_TIMELINE_MIN_NET_VOLUME,
) -> dict[str, dict[str, Any]]:
    """盤中大戶力每檔的「什麼時候進名單」：一根一根累加主力副圖 K 棒，累計大單淨量第一次 ≥ +min_net_volume（多）
    ／≤ −min_net_volume（空）的那根就是進名單時間，再記那一邊的最高（最低）大戶力%、最後一根還在不在。
    2026-10-13 使用者：盤中大戶力多／空只看得到收盤那一刻（13:20）的名單，早上進過、後來掉出去的都不見了。
    回 {代號: {"bull": {firstTs, firstPct, peakPct, peakTs, still}, "bear": {...}}}，沒進過名單的代號不回。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    code_clause = ""
    code_params: list[str] = []
    if codes is not None:
        code_params = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
        if not code_params:
            return {}
        code_clause = f" AND stock_code IN ({', '.join('?' for _ in code_params)})"
    with database.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT stock_code, bar_ts, main_net_volume, main_buy_amount, main_sell_amount, total_amount
            FROM main_force_bars
            WHERE trade_date = ? AND interval = ?{code_clause}
            ORDER BY stock_code, bar_ts
            """,
            (trade_date, interval, *code_params),
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {}

    def finish(code: str) -> None:
        sides = state.get("sides") or {}
        if not sides:
            return
        net = state["net"]
        for side, info in sides.items():
            info["still"] = net >= min_net_volume if side == "bull" else net <= -min_net_volume
        out[code] = sides

    current = None
    for row in rows:
        code = str(row["stock_code"])
        if code != current:
            if current is not None:
                finish(current)
            current = code
            state = {"net": 0, "buy": 0.0, "sell": 0.0, "total": 0.0, "sides": {}}
        state["net"] += int(row["main_net_volume"] or 0)
        state["buy"] += float(row["main_buy_amount"] or 0)
        state["sell"] += float(row["main_sell_amount"] or 0)
        state["total"] = max(state["total"], float(row["total_amount"] or 0))
        net = state["net"]
        side = "bull" if net >= min_net_volume else "bear" if net <= -min_net_volume else None
        if side is None:
            continue
        pct = compute_holder_strength_pct(state["buy"], state["sell"], state["total"])
        pct = round(pct, 2) if pct is not None else None
        ts = int(row["bar_ts"])
        info = state["sides"].get(side)
        if info is None:
            state["sides"][side] = {"firstTs": ts, "firstPct": pct, "peakPct": pct, "peakTs": ts}
        elif pct is not None and (info["peakPct"] is None or (pct > info["peakPct"] if side == "bull" else pct < info["peakPct"])):
            info["peakPct"], info["peakTs"] = pct, ts
    if current is not None:
        finish(current)
    return out


WINDOW_FLIP_BARS = 6              # 近 30 分鐘＝最近 6 根 5 分K
WINDOW_FLIP_MIN_GROSS_LOTS = 200   # 近 30 分鐘主力買＋賣至少 200 張（10 筆大單），太冷清的不算
WINDOW_FLIP_MIN_NET_LOTS = 50      # 近 30 分鐘淨買（賣）至少 50 張
# 2026-10-13 上線第一版（20%／10%／1,000 萬）10/07～10/12 每天 220～250 筆、同一檔一天翻 10 次，太多；
# 照那三天調到每天 40～55 筆（跟開盤累計翻多空差不多量）：
WINDOW_FLIP_MIN_RATIO = 0.4        # 近 30 分鐘淨額佔主力買賣合計 ≥ 40%（一面倒）
WINDOW_FLIP_PREV_MIN_RATIO = 0.3   # 前 30 分鐘是反方向、淨額佔比至少 30%（原本真的在賣／買）
WINDOW_FLIP_MIN_NET_AMOUNT = 80_000_000  # 近 30 分鐘淨額至少 8,000 萬元（2026-10-13 使用者：5,000 萬實際每天 64～84 筆還是太多，提高到 8,000 萬）
WINDOW_FLIP_COOLDOWN_BARS = 12     # 同一檔同方向 60 分鐘內不重複發


def load_window_flips(
    trade_date: str,
    interval: str = "5m",
    codes: Iterable[str] | None = None,
    window: int = WINDOW_FLIP_BARS,
) -> list[dict[str, Any]]:
    """近 30 分鐘主力翻多／翻空（2026-10-13 使用者）：開盤累計的「主力累計翻多空」越晚越難翻，盤中後段幾乎沒有訊號，
    這裡改看「前 30 分鐘 vs 近 30 分鐘」——前 30 分鐘主力淨賣（淨額佔主力買賣 ≥ 10%），近 30 分鐘轉成淨買
    （≥ 50 張、≥ 8,000 萬元、佔近 30 分鐘主力買賣 ≥ 40%，主力買賣合計 ≥ 200 張；前 30 分鐘反方向佔比 ≥ 30%）＝翻多；反過來＝翻空。
    每根 5 分K收完檢查一次，條件第一次成立那根發一次；之後要先不成立、再成立，而且同方向距離上次 60 分鐘以上才會再發。
    最早 10:00 才有（要先有一小時的資料）。
    回傳 [{code, name, side, ts（那根收盤時間）, curNet, prevNet, curRatio, prevRatio, curNetAmount, cumStrengthPct}]，舊到新。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    bar_ms = 5 * 60 * 1000 if interval == "5m" else 60 * 1000
    _ensure_table()
    code_clause = ""
    code_params: list[str] = []
    if codes is not None:
        code_params = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
        if not code_params:
            return []
        code_clause = f" AND b.stock_code IN ({', '.join('?' for _ in code_params)})"
    with database.get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT b.stock_code, b.bar_ts, b.main_buy_volume, b.main_sell_volume, b.main_buy_amount, b.main_sell_amount,
                   b.total_amount, s.stock_name
            FROM main_force_bars b LEFT JOIN stocks s ON s.stock_code = b.stock_code
            WHERE b.trade_date = ? AND b.interval = ?{code_clause}
            ORDER BY b.stock_code, b.bar_ts
            """,
            (trade_date, interval, *code_params),
        ).fetchall()
    by_code: dict[str, list[Any]] = {}
    names: dict[str, str] = {}
    for row in rows:
        code = str(row["stock_code"])
        by_code.setdefault(code, []).append(row)
        names[code] = row["stock_name"] or code
    events: list[dict[str, Any]] = []
    for code, bars in by_code.items():
        buys = [int(b["main_buy_volume"] or 0) for b in bars]
        sells = [int(b["main_sell_volume"] or 0) for b in bars]
        buy_amt = [float(b["main_buy_amount"] or 0) for b in bars]
        sell_amt = [float(b["main_sell_amount"] or 0) for b in bars]
        cum_buy = cum_sell = cum_total = 0.0
        armed = {"bull": True, "bear": True}
        last_fired = {"bull": -10**9, "bear": -10**9}
        for i, bar in enumerate(bars):
            cum_buy += buy_amt[i]
            cum_sell += sell_amt[i]
            cum_total = max(cum_total, float(bar["total_amount"] or 0))
            if i < 2 * window - 1:
                continue
            cur = slice(i - window + 1, i + 1)
            prev = slice(i - 2 * window + 1, i - window + 1)
            cur_gross = sum(buys[cur]) + sum(sells[cur])
            prev_gross = sum(buys[prev]) + sum(sells[prev])
            cur_net = sum(buys[cur]) - sum(sells[cur])
            prev_net = sum(buys[prev]) - sum(sells[prev])
            cur_net_amount = sum(buy_amt[cur]) - sum(sell_amt[cur])
            cur_ratio = cur_net / cur_gross if cur_gross else 0.0
            prev_ratio = prev_net / prev_gross if prev_gross else 0.0
            for side, sign in (("bull", 1), ("bear", -1)):
                ok = (cur_gross >= WINDOW_FLIP_MIN_GROSS_LOTS and sign * cur_net >= WINDOW_FLIP_MIN_NET_LOTS
                      and sign * cur_ratio >= WINDOW_FLIP_MIN_RATIO and sign * cur_net_amount >= WINDOW_FLIP_MIN_NET_AMOUNT
                      and prev_gross > 0 and -sign * prev_ratio >= WINDOW_FLIP_PREV_MIN_RATIO)
                if ok and armed[side] and i - last_fired[side] >= WINDOW_FLIP_COOLDOWN_BARS:
                    armed[side] = False
                    last_fired[side] = i
                    pct = compute_holder_strength_pct(cum_buy, cum_sell, cum_total)
                    events.append({
                        "code": code, "name": names[code], "side": side, "ts": int(bar["bar_ts"]) + bar_ms,
                        "curNet": cur_net, "prevNet": prev_net, "curRatio": round(cur_ratio * 100, 1), "prevRatio": round(prev_ratio * 100, 1),
                        "curNetAmount": round(cur_net_amount), "cumStrengthPct": round(pct, 2) if pct is not None else None,
                    })
                elif not ok:
                    armed[side] = True
    events.sort(key=lambda e: (e["ts"], e["code"]))
    return events


def load_daily_main_force_net(stock_code: str, interval: str = "5m") -> dict[str, int]:
    """單一股票依交易日彙總主力淨量，給日線圖副圖用。只涵蓋目前保留天數內
    （prune_old_bars 預設30個交易日）的資料，比這個範圍舊的交易日不會有值，
    是資料保留政策造成的預期限制，不是bug。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, SUM(main_net_volume) AS net_volume
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ?
            GROUP BY trade_date
            """,
            (stock_code, interval),
        ).fetchall()
    return {row["trade_date"]: int(row["net_volume"] or 0) for row in rows}


def load_daily_main_force_net_amount(stock_code: str, interval: str = "5m") -> dict[str, float]:
    """跟 load_daily_main_force_net 一樣依交易日彙總，但回傳金額（元）
    而不是張數；供需要金額門檻（例如「前日大單淨額 > 1 億元」）的訊號
    使用。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT trade_date, SUM(main_net_amount) AS net_amount
            FROM main_force_bars
            WHERE stock_code = ? AND interval = ?
            GROUP BY trade_date
            """,
            (stock_code, interval),
        ).fetchall()
    return {row["trade_date"]: float(row["net_amount"] or 0) for row in rows}


def previous_trade_date_with_data(stock_code: str, before_date: str, interval: str = "5m") -> str | None:
    """回傳這檔股票在 before_date 之前，最近一個有主力副圖資料的交易日；
    沒有更早資料時回 None。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        row = connection.execute(
            """
            SELECT trade_date FROM main_force_bars
            WHERE stock_code = ? AND interval = ? AND trade_date < ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            (stock_code, interval, before_date),
        ).fetchone()
    return row["trade_date"] if row else None


def latest_trade_date_with_data(before_date: str, interval: str = "5m") -> str | None:
    """before_date 之前（不含）最近一個有任何主力副圖資料的交易日；沒有就 None。
    排行端點在今天還沒有資料時（午夜過後到下一個交易日開盤前）拿來沿用上一個交易日。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        row = connection.execute(
            "SELECT MAX(trade_date) AS trade_date FROM main_force_bars WHERE interval = ? AND trade_date < ?",
            (interval, before_date),
        ).fetchone()
    return row["trade_date"] if row and row["trade_date"] else None


def list_tracked_stock_codes(trade_date: str, interval: str = "1m") -> list[str]:
    """今日已有主力副圖資料的股票代號；用來找「目前實際在追蹤」的股票，
    不需要另外掃描或訂閱。"""
    if interval not in {"1m", "5m"}:
        raise ValueError(f"不支援 interval: {interval}")
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT stock_code FROM main_force_bars WHERE trade_date = ? AND interval = ?",
            (trade_date, interval),
        ).fetchall()
    return sorted(str(row["stock_code"]) for row in rows)


def prune_old_bars(keep_days: int = 30) -> int:
    """只保留最近 keep_days 個「有資料的交易日」，刪掉更早的1分/5分主力副圖，
    避免資料庫無限長大。用實際存在的 trade_date 決定，不是單純的日曆天數，
    所以會自動跳過假日。"""
    _ensure_table()
    keep_days = max(1, int(keep_days))
    with database.get_connection() as connection:
        rows = connection.execute(
            "SELECT DISTINCT trade_date FROM main_force_bars ORDER BY trade_date DESC LIMIT ?",
            (keep_days,),
        ).fetchall()
        if len(rows) < keep_days:
            return 0  # 資料還不滿 keep_days 天，還不用清
        cutoff_date = rows[-1]["trade_date"]
        cursor = connection.execute(
            "DELETE FROM main_force_bars WHERE trade_date < ?",
            (cutoff_date,),
        )
        return int(cursor.rowcount or 0)


def purge_out_of_session_bars() -> int:
    """一次性清理：收集器過去沒有限制09:00-13:30盤中連續交易時段，若上游把
    盤後定價撮合（14:00-14:30）誤判成一根K棒，就會被存進來，讓排行的
    MAX(bar_ts)顯示成盤後時間，蓋掉真正的盤中最後一筆。_rows_for_bars
    已經擋掉新資料，這裡是清掉舊資料庫裡已經寫進去的髒資料，不用等
    30天保留期自然淘汰。之後每次應該都刪0筆（冪等）。"""
    _ensure_table()
    with database.get_connection() as connection:
        rows = connection.execute("SELECT DISTINCT bar_ts FROM main_force_bars").fetchall()
        bad_ts = [int(row["bar_ts"]) for row in rows if not is_regular_otc_session(row["bar_ts"])]
        if not bad_ts:
            return 0
        placeholders = ",".join("?" for _ in bad_ts)
        cursor = connection.execute(
            f"DELETE FROM main_force_bars WHERE bar_ts IN ({placeholders})", bad_ts
        )
        return int(cursor.rowcount or 0)


def main_force_storage_status() -> dict[str, Any]:
    _ensure_table()
    with database.get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT stock_code) AS codes, COUNT(DISTINCT trade_date) AS dates, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date FROM main_force_bars"
        ).fetchone()
    return {
        "barCount": int(row["n"]), "stockCount": int(row["codes"]),
        "tradeDateCount": int(row["dates"]), "firstTradeDate": row["first_date"],
        "lastTradeDate": row["last_date"],
    }
