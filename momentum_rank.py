"""動能排行（2026-11-14 使用者：照券商 App「動能選股 → 動能排行」做機器人代碼；跟「熱門趨勢概念股」同一組、分開兩個選股）。

App 的說明：「找出週、月、季、年漲幅前 5% 的創高強勢股」。用 11/13 選出的 4 檔（台玻、競國、騰雲、大世科）反推：
- 週漲幅＝本週以來（跟上一週最後一個交易日的收盤比）、月漲幅＝本月以來（跟上個月最後一個交易日的收盤比），
  App 上這 4 檔的數字一模一樣；跟「最近 20 個交易日」對不上，所以是日曆的週、月。季、年照同一個算法：本季以來、今年以來。
- 4 檔當天收盤都是最近 240 個交易日最高的收盤 → 創高＝收盤是 240 日最高收盤。
- 前 5%＝全市場一般股（4 位數、排除 00 開頭的 ETF，跟創高黑選股同一份）四個漲幅各自排，四個都在前 5%。
價格用還原股價（創高黑選股那份記憶體還原日K：heilong_picker.load_panel）。資料是收盤後的，盤中不變。
另外回「差一個」的名單（創高、四個裡面三個在前 N%），給前端參考。
"""

from __future__ import annotations

import math
import threading
from bisect import bisect_left
from datetime import date, timedelta
from typing import Any

import heilong_picker

WINDOW = 240
METRICS = (("wtd", "週"), ("mtd", "月"), ("qtd", "季"), ("ytd", "年"))
TOP_CHOICES = (1, 2, 3, 5, 8, 10, 15, 20)
DEFAULT_TOP = 5

_cache: dict[tuple, Any] = {}
_lock = threading.Lock()


def _r(value: float | None, digits: int = 2) -> float | None:
    if value is None or value != value:
        return None
    return round(float(value), digits)


def period_starts(day: str) -> dict[str, str]:
    """這一天所在的週（星期一）、月、季、年的第一天。"""
    d = date.fromisoformat(day)
    return {
        "wtd": (d - timedelta(days=d.weekday())).isoformat(),
        "mtd": d.replace(day=1).isoformat(),
        "qtd": d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1).isoformat(),
        "ytd": d.replace(month=1, day=1).isoformat(),
    }


def compute(panel: heilong_picker.Panel, *, top: float = DEFAULT_TOP) -> dict[str, Any]:
    if not panel.dates:
        return {"status": "empty", "date": None, "rows": []}
    last = len(panel.dates) - 1
    day = panel.dates[last]
    starts = period_starts(day)
    # 每個期間的基準日＝期間開始之前最後一個交易日（全表的交易日 index；個股那天沒成交就往前找）
    base_index = {k: bisect_left(panel.dates, v) - 1 for k, v in starts.items()}
    stats: list[dict[str, Any]] = []
    for code, s in panel.series.items():
        valid = s.valid()
        if not valid or valid[-1] != last:
            continue
        close = s.c[last]
        if not close or close <= 0:
            continue
        closes = [s.c[j] for j in valid[-WINDOW:]]
        row: dict[str, Any] = {"code": code, "s": s, "close": close, "newHigh": close >= max(closes) - 1e-9}
        for key, _label in METRICS:
            bi = base_index[key]
            pos = bisect_left(valid, bi + 1) - 1          # 基準日（含）以前最後一個有成交的日子
            base = s.c[valid[pos]] if bi >= 0 and pos >= 0 else None
            row[key] = (close / base - 1) * 100 if base else None
        stats.append(row)
    cuts: dict[str, float | None] = {}
    ranks: dict[str, dict[str, int]] = {}
    for key, _label in METRICS:
        ordered = sorted((r for r in stats if r[key] is not None), key=lambda r: -r[key])
        n = len(ordered)
        k = max(1, math.ceil(n * top / 100)) if n else 0
        cuts[key] = ordered[k - 1][key] if k else None
        ranks[key] = {r["code"]: i + 1 for i, r in enumerate(ordered)}
        ranks[key]["__n"] = n

    def in_top(r: dict[str, Any], key: str) -> bool:
        return r[key] is not None and cuts[key] is not None and r[key] >= cuts[key] - 1e-9

    def out(r: dict[str, Any], miss: list[str] | None = None) -> dict[str, Any]:
        s = r["s"]
        ch = s.chg[last]
        item = {
            "code": r["code"], "name": s.name, "group": s.group, "close": _r(r["close"]), "chg": _r(ch) if ch == ch else None,
            "score": s.score[last] if s.score[last] >= 0 else None, "hiLen": s.hilen[last] or None, "val5": _r(s.val5[last]),
        }
        for key, _label in METRICS:
            item[key] = _r(r[key])
            item[key + "Rank"] = ranks[key].get(r["code"])
        if miss is not None:
            item["miss"] = miss
        return item

    rows, almost = [], []
    for r in stats:
        if not r["newHigh"]:
            continue
        miss = [label for key, label in METRICS if not in_top(r, key)]
        if not miss:
            rows.append(out(r))
        elif len(miss) == 1:
            almost.append(out(r, miss))
    rows.sort(key=lambda x: x["code"])
    almost.sort(key=lambda x: x["code"])
    return {
        "status": "ok", "date": day, "top": top, "starts": starts, "universe": len(stats),
        "cuts": {key: _r(v) for key, v in cuts.items()}, "rows": rows, "almost": almost,
    }


def payload(top: float | None = None) -> dict[str, Any]:
    top = float(top) if top is not None else float(DEFAULT_TOP)
    if top not in TOP_CHOICES:
        raise ValueError("top 只能是 " + "、".join(str(x) for x in TOP_CHOICES))
    panel = heilong_picker.load_panel()
    key = (panel.key, top)
    with _lock:
        if key in _cache:
            return _cache[key]
    result = compute(panel, top=top)
    result["loadedAt"] = panel.loaded_at
    with _lock:
        for old in [k for k in _cache if k[0] != panel.key]:
            del _cache[old]
        _cache[key] = result
    return result
