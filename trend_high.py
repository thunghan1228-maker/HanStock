"""熱門趨勢概念股（2026-11-14 使用者：照券商 App「動能選股 → 熱門趨勢概念股」做一個機器人代碼）。

App 的兩個欄位用 11/13 那六檔（台玻、華南金、富邦金、國泰金、中信金、創意）反推，全部對上：
- 240日最高＝最近 240 個交易日（含當天）最高的「收盤價」（不是盤中最高價）。
- 240日漲幅＝當天收盤 ÷ 240 個交易日前那天的收盤 − 1，用還原股價（除權息還原；台玻沒配息 143.14% 一模一樣，
  金融股、創意差的就是配息）。
App 挑股還看「新聞熱度」，我們沒有新聞資料；這裡只算價格的部分，熱度由前端用成交值近似、讓使用者自己調門檻。

資料用創高黑選股那份記憶體特徵（heilong_picker.load_panel：全市場還原日K，收盤後整理），算最新一天每檔的：
  close、hi240、g240（離 240 日高幾 % 由前端算）、
  以及「下一個交易日盤中」要用的 hi239（最近 239 天的最高收盤）、b239（下一個交易日的 240 日漲幅基準價）
  → 盤中拿即時價 p：240 日最高＝max(hi239, p)、240 日漲幅＝p ÷ b239 − 1。
只回離 240 日最高（收盤）NEAR_PCT% 以內的股票：其他的就算隔天盤中大漲也很難碰到新高，名單才不會太大。
"""

from __future__ import annotations

import threading
from typing import Any

import heilong_picker

WINDOW = 240
NEAR_PCT = 12.0          # 回傳範圍：收盤離 240 日最高收盤在這個 % 以內

_cache: dict[str, Any] = {"key": None, "payload": None}
_lock = threading.Lock()


def _r(value: float | None, digits: int = 2) -> float | None:
    if value is None or value != value:
        return None
    return round(float(value), digits)


def compute(panel: heilong_picker.Panel, *, near_pct: float = NEAR_PCT) -> dict[str, Any]:
    """最新一天（panel.dates 最後一天）有成交的股票，各算 240 日最高收盤、240 日漲幅，以及隔天盤中用的 hi239／b239。"""
    if not panel.dates:
        return {"status": "empty", "date": None, "rows": []}
    last = len(panel.dates) - 1
    rows: list[dict[str, Any]] = []
    for code, s in panel.series.items():
        valid = s.valid()
        if not valid or valid[-1] != last:          # 最新一天沒成交（停牌、還沒上市）
            continue
        closes = [s.c[j] for j in valid[-WINDOW:]]
        close = closes[-1]
        if not close or close <= 0:
            continue
        hi240 = max(closes)
        if (close / hi240 - 1) * 100 < -near_pct:
            continue
        g240 = (close / s.c[valid[-WINDOW - 1]] - 1) * 100 if len(valid) > WINDOW else None
        hi239 = max(closes[-(WINDOW - 1):])
        b239 = s.c[valid[-WINDOW]] if len(valid) >= WINDOW else None
        ch = s.chg[last]
        rows.append({
            "code": code, "name": s.name, "group": s.group,
            "close": _r(close), "chg": _r(ch) if ch == ch else None,
            "hi240": _r(hi240), "g240": _r(g240), "hi239": _r(hi239), "b239": _r(b239, 4),
            "score": s.score[last] if s.score[last] >= 0 else None, "hiLen": s.hilen[last] or None,
            "val5": _r(s.val5[last]), "mcap": _r(s.mcap(last), 1), "days": len(valid),
        })
    rows.sort(key=lambda r: (r["g240"] is None, -(r["g240"] or 0)))
    return {"status": "ok", "date": panel.dates[last], "window": WINDOW, "nearPct": near_pct, "rows": rows}


def payload() -> dict[str, Any]:
    """端點用：特徵表沒變就用上一次算好的。"""
    panel = heilong_picker.load_panel()
    with _lock:
        if _cache["key"] == panel.key and _cache["payload"] is not None:
            return _cache["payload"]
    out = compute(panel)
    out["loadedAt"] = panel.loaded_at
    with _lock:
        _cache["key"], _cache["payload"] = panel.key, out
    return out
