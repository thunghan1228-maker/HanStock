"""記憶體診斷：Railway 記憶體圖常衝到 8–10 GB，但本機模擬只重現到 0.5 GB（2026-10-10 使用者）。

背景每 SAMPLE_SECONDS 秒取樣一次：
- 程序 RSS（真的被 Python 用掉的）與容器 cgroup 的 anon／file（file＝讀 SQLite 留下的磁碟快取，可回收）。
- 一次漲超過 SPIKE_MB 就記一行 WARNING，附上每條執行緒「當下正在跑本專案哪個函式」，抓出是誰。
請求也一樣：單一請求前後 RSS 漲超過 REQUEST_SPIKE_MB 就記路徑。
HANSTOCK_TRACEMALLOC=1 時另開 tracemalloc（有額外開銷，查完就關），端點會列出佔最多記憶體的程式位置。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("hanstock.memory")

TW_TZ = timezone(timedelta(hours=8))
PROJECT_DIR = str(Path(__file__).resolve().parent)
SAMPLE_SECONDS = max(5, int(os.getenv("HANSTOCK_MEMORY_SAMPLE_SECONDS", "30")))
SPIKE_MB = max(50, int(os.getenv("HANSTOCK_MEMORY_SPIKE_MB", "300")))
REQUEST_SPIKE_MB = max(20, int(os.getenv("HANSTOCK_MEMORY_REQUEST_SPIKE_MB", "150")))
HISTORY_POINTS = 24 * 3600 // SAMPLE_SECONDS     # 留一天
EVENT_POINTS = 200
TRACEMALLOC_ENABLED = os.getenv("HANSTOCK_TRACEMALLOC", "").strip().lower() in {"1", "true", "yes", "on"}

_history: deque[dict[str, Any]] = deque(maxlen=HISTORY_POINTS)
_events: deque[dict[str, Any]] = deque(maxlen=EVENT_POINTS)
_lock = threading.Lock()
_started = False


def _now() -> str:
    return datetime.now(TW_TZ).isoformat(timespec="seconds")


def process_memory() -> dict[str, int | None]:
    """{rssMb, peakRssMb}：/proc/self/status 的 VmRSS、VmHWM（MB）。"""
    out: dict[str, int | None] = {"rssMb": None, "peakRssMb": None}
    try:
        with open("/proc/self/status", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    out["rssMb"] = int(line.split()[1]) // 1024
                elif line.startswith("VmHWM:"):
                    out["peakRssMb"] = int(line.split()[1]) // 1024
    except OSError:
        pass
    return out


def rss_mb() -> int | None:
    return process_memory()["rssMb"]


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as fh:
            text = fh.read().strip()
        return None if text == "max" else int(text)
    except (OSError, ValueError):
        return None


def cgroup_memory() -> dict[str, int | None]:
    """容器整體（Railway 計費看的）：currentMb、anonMb（程式真的用的）、fileMb（磁碟快取）、limitMb。cgroup v2 優先，v1 退回。"""
    out: dict[str, int | None] = {"currentMb": None, "anonMb": None, "fileMb": None, "limitMb": None}
    mb = 1024 * 1024
    current = _read_int("/sys/fs/cgroup/memory.current")
    if current is not None:
        out["currentMb"] = current // mb
        limit = _read_int("/sys/fs/cgroup/memory.max")
        out["limitMb"] = limit // mb if limit else None
        stat_path = "/sys/fs/cgroup/memory.stat"
        keys = {"anon": "anonMb", "file": "fileMb"}
    else:
        current = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        if current is None:
            return out
        out["currentMb"] = current // mb
        limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        out["limitMb"] = limit // mb if limit and limit < (1 << 60) else None
        stat_path = "/sys/fs/cgroup/memory/memory.stat"
        keys = {"rss": "anonMb", "cache": "fileMb"}
    try:
        with open(stat_path, encoding="ascii") as fh:
            for line in fh:
                name, _, value = line.partition(" ")
                if name in keys:
                    out[keys[name]] = int(value) // mb
    except (OSError, ValueError):
        pass
    return out


def thread_activity() -> list[dict[str, str]]:
    """每條執行緒當下最內層的本專案函式（檔名:行 函式），閒著等 sleep 的也照列，方便對時間。"""
    names = {t.ident: t.name for t in threading.enumerate()}
    out: list[dict[str, str]] = []
    for ident, frame in sys._current_frames().items():
        where = ""
        f = frame
        while f is not None:
            filename = f.f_code.co_filename
            if filename.startswith(PROJECT_DIR) and os.path.basename(filename) != "memory_diag.py":
                where = f"{os.path.basename(filename)}:{f.f_lineno} {f.f_code.co_name}"
                break
            f = f.f_back
        out.append({"thread": names.get(ident, str(ident)), "at": where or "-"})
    out.sort(key=lambda x: x["thread"])
    return out


def _busy_threads() -> list[dict[str, str]]:
    return [t for t in thread_activity() if t["at"] != "-"]


def record_event(kind: str, **detail: Any) -> None:
    with _lock:
        _events.append({"at": _now(), "kind": kind, **detail})


def _sample_once(previous: int | None) -> int | None:
    proc = process_memory()
    group = cgroup_memory()
    point = {"at": _now(), "rssMb": proc["rssMb"], "cgroupMb": group["currentMb"],
             "anonMb": group["anonMb"], "fileMb": group["fileMb"], "threads": threading.active_count()}
    with _lock:
        _history.append(point)
    rss = proc["rssMb"]
    if rss is not None and previous is not None and rss - previous >= SPIKE_MB:
        busy = _busy_threads()
        record_event("spike", fromMb=previous, toMb=rss, cgroup=group, threads=busy)
        logger.warning("記憶體暴增 %d→%d MB（容器 %s MB，快取 %s MB）；執行中：%s", previous, rss, group["currentMb"],
                       group["fileMb"], "；".join(f"{t['thread']}@{t['at']}" for t in busy))
    return rss


def _loop() -> None:
    previous: int | None = None
    while True:
        try:
            previous = _sample_once(previous)
        except Exception:  # noqa: BLE001
            logger.exception("memory sample failed")
        time.sleep(SAMPLE_SECONDS)


def start_memory_sampler() -> bool:
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    if TRACEMALLOC_ENABLED:
        import tracemalloc

        tracemalloc.start(1)
    threading.Thread(target=_loop, name="hanstock-memory-sampler", daemon=True).start()
    return True


def note_request(path: str, before_mb: int | None, after_mb: int | None, seconds: float) -> None:
    """請求跑完呼叫：前後 RSS 漲太多就記下來（併發時可能算到別人的，所以只當線索）。"""
    if before_mb is None or after_mb is None or after_mb - before_mb < REQUEST_SPIKE_MB:
        return
    record_event("request", path=path, fromMb=before_mb, toMb=after_mb, seconds=round(seconds, 1))
    logger.warning("請求後記憶體 %d→%d MB：%s（%.1fs）", before_mb, after_mb, path, seconds)


def top_allocations(limit: int = 25) -> list[dict[str, Any]] | None:
    """tracemalloc 有開才有：佔最多記憶體的程式位置。"""
    if not TRACEMALLOC_ENABLED:
        return None
    import tracemalloc

    if not tracemalloc.is_tracing():
        return None
    stats = tracemalloc.take_snapshot().statistics("lineno")[:limit]
    return [{"where": f"{s.traceback[0].filename.replace(PROJECT_DIR + os.sep, '')}:{s.traceback[0].lineno}",
             "mb": round(s.size / 1024 / 1024, 1), "count": s.count} for s in stats]


TABLE_SIZES_TTL_SECONDS = 6 * 3600
_table_sizes: dict[str, Any] = {"computedAt": None, "at": 0.0, "running": False, "tables": None, "error": None}


def _file_sizes() -> dict[str, Any]:
    """資料庫檔（含 -wal／-shm）與所在磁碟（Railway volume）的大小，MB；只讀檔案資訊，很便宜。"""
    import shutil

    from database import DATABASE_PATH

    path = Path(DATABASE_PATH)
    files = {}
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{path}{suffix}")
        if p.exists():
            files[p.name] = round(p.stat().st_size / 1024 / 1024, 1)
    out: dict[str, Any] = {"path": str(path), "filesMb": files}
    try:
        usage = shutil.disk_usage(path.parent)
        out["diskMb"] = {"total": usage.total // 1024 // 1024, "used": usage.used // 1024 // 1024, "free": usage.free // 1024 // 1024}
    except OSError:
        pass
    return out


def _compute_table_sizes() -> None:
    from database import get_connection

    try:
        with get_connection() as connection:
            owner = {str(r[0]): str(r[1]) for r in connection.execute("SELECT name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index')")}
            totals: dict[str, dict[str, float]] = {}
            for name, pgsize in connection.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
                table = owner.get(str(name), str(name))
                entry = totals.setdefault(table, {"tableMb": 0.0, "indexMb": 0.0})
                entry["tableMb" if str(name) == table else "indexMb"] += (pgsize or 0) / 1024 / 1024
        tables = sorted(({"table": t, "tableMb": round(v["tableMb"], 1), "indexMb": round(v["indexMb"], 1),
                          "totalMb": round(v["tableMb"] + v["indexMb"], 1)} for t, v in totals.items()),
                        key=lambda x: -x["totalMb"])
        with _lock:
            _table_sizes.update({"computedAt": _now(), "at": time.time(), "tables": tables, "error": None})
    except Exception as exc:  # noqa: BLE001
        logger.exception("table size scan failed")
        with _lock:
            _table_sizes.update({"error": f"{type(exc).__name__}: {exc}"[:300], "at": time.time()})
    finally:
        with _lock:
            _table_sizes["running"] = False


def table_sizes(refresh: bool = False) -> dict[str, Any]:
    """每張表（含它的索引）佔多少 MB，大到小。要把整個資料庫檔讀一遍，所以在背景算、結果留 6 小時；
    第一次打（或 refresh）會先回 running，過一兩分鐘再打一次就有結果。"""
    with _lock:
        stale = time.time() - _table_sizes["at"] > TABLE_SIZES_TTL_SECONDS
        start = (refresh or stale) and not _table_sizes["running"]
        if start:
            _table_sizes["running"] = True
        snapshot = {k: v for k, v in _table_sizes.items() if k != "at"}
    if start:
        threading.Thread(target=_compute_table_sizes, name="hanstock-table-sizes", daemon=True).start()
        snapshot["running"] = True
    return {"files": _file_sizes(), **snapshot}


def payload(history_minutes: int = 120) -> dict[str, Any]:
    points = max(1, history_minutes * 60 // SAMPLE_SECONDS)
    with _lock:
        history = list(_history)[-points:]
        events = list(_events)
    return {
        "at": _now(),
        "process": process_memory(),
        "container": cgroup_memory(),
        "threads": thread_activity(),
        "events": events,
        "history": history,
        "tracemalloc": top_allocations(),
        "settings": {"sampleSeconds": SAMPLE_SECONDS, "spikeMb": SPIKE_MB, "requestSpikeMb": REQUEST_SPIKE_MB,
                     "tracemalloc": TRACEMALLOC_ENABLED},
    }
