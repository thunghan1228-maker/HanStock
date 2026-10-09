"""網站例行檢查：API 都通、資料沒過期、每個分頁／功能點下去不出錯。

用法：
    python scripts/site_check.py                 # API＋瀏覽器（桌機、手機）
    python scripts/site_check.py --no-browser    # 只檢查 API
    python scripts/site_check.py --chromium /opt/pw-browsers/chromium-1194/chrome-linux/chrome

瀏覽器檢查要先 pip install playwright（並有 Chromium）。有問題時結束碼為 1。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trading_days import is_trading_day, previous_trading_day  # noqa: E402

DEFAULT_BASE = "https://tw-groups.judystock.workers.dev"
TW_TZ = timezone(timedelta(hours=8))
SLOW_SECONDS = 10.0
# 盤後收集器最晚約 23:15 跑完；過了這個時間才要求資料日是今天。
DATA_READY_AT = (23, 30)

# (路徑, 資料日欄位)；欄位是 None 表示只檢查回應正常。
ENDPOINTS: list[tuple[str, str | None]] = [
    ("version", None),
    ("groups", "quoteDate"),
    ("otc-strength", None),
    ("intraday-signals", None),
    ("group-daily-changes", None),
    ("main-force-ranking", "tradeDate"),
    ("chip-radar", "latest"),
    ("grail-radar", "date"),
    ("brew-launch", "session"),
    ("brew-launch-history", None),
    ("heilong", "date"),
    ("picker", "date"),
    ("picker-live", None),
    ("revenue", None),
    ("jail", "dataDate"),
    ("swing-report", "date"),
    ("chips-daily", "date"),
    ("kline-backfill-status", None),
    ("stock-flags", None),
    ("watch-quotes", None),
    ("checkup?code=2330", None),
    ("diag?code=2330", "date"),
    ("jail-stock?code=2330", None),
    ("revenue-stock?code=2330", None),
    ("chip-radar-stock?code=2330", None),
]

BAD_TEXT = re.compile(r".{0,30}(undefined|NaN|\bnull\b|Infinity|\[object Object\]).{0,30}")


def expected_data_date(now: datetime) -> str:
    """現在應該看得到的最新交易日資料。"""
    today = now.date()
    if is_trading_day(today) and (now.hour, now.minute) >= DATA_READY_AT:
        return today.isoformat()
    return previous_trading_day(today).isoformat()


def check_api(base: str, now: datetime) -> list[str]:
    problems: list[str] = []
    expected = expected_data_date(now)
    print(f"== API（資料日應 ≥ {expected}）")
    for path, date_key in ENDPOINTS:
        url = f"{base}/api/{path}"
        started = time.monotonic()
        try:
            # Cloudflare 會擋 Python 預設的 User-Agent（403），要自己帶一個。
            request = urllib.request.Request(url, headers={"User-Agent": "HanStock-site-check/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=60) as resp:
                body = resp.read()
                code = resp.status
        except urllib.error.HTTPError as exc:
            code, body = exc.code, b""
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path}: 連不上（{exc}）")
            print(f"  ✗ {path:28} 連不上")
            continue
        elapsed = time.monotonic() - started
        notes: list[str] = []
        if code != 200:
            notes.append(f"HTTP {code}")
        else:
            try:
                data = json.loads(body)
            except ValueError:
                data = None
                notes.append("回的不是 JSON")
            if isinstance(data, dict):
                if str(data.get("status", "ok")).lower() in {"error", "failed"}:
                    notes.append(f"status={data.get('status')} {data.get('error') or data.get('message') or ''}".strip())
                if date_key:
                    value = data.get(date_key)
                    if not value:
                        notes.append(f"沒有 {date_key}")
                    elif str(value)[:10] < expected:
                        notes.append(f"資料過期：{date_key}={value}")
        if notes:
            problems.extend(f"{path}: {note}" for note in notes)
        slow = f"  ⚠ 慢（{elapsed:.1f}s）" if elapsed > SLOW_SECONDS else ""
        print(f"  {'✗' if notes else '✓'} {path:28} {elapsed:5.1f}s{slow}{'  ' + '；'.join(notes) if notes else ''}")
    return problems


def check_browser(base: str, chromium: str | None) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ["瀏覽器檢查需要 playwright（pip install playwright），或加 --no-browser 略過"]
    problems: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium, args=["--no-sandbox"]) if chromium else pw.chromium.launch()
        for label, viewport in (("桌機", {"width": 1400, "height": 900}), ("手機", {"width": 390, "height": 844})):
            print(f"== 瀏覽器（{label} {viewport['width']}px）")
            page = browser.new_page(viewport=viewport)
            errors: list[str] = []
            page.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(f"JS 錯誤: {exc}"))
            page.on("response", lambda resp: errors.append(f"HTTP {resp.status} {resp.url}") if resp.status >= 400 else None)
            page.on("dialog", lambda dialog: (errors.append(f"跳出對話框: {dialog.message}"), dialog.dismiss()))

            def load() -> None:
                page.goto(base + "/", wait_until="networkidle", timeout=90_000)
                page.wait_for_timeout(2500)

            def inspect(name: str) -> None:
                page.wait_for_timeout(3500)
                text = page.evaluate("document.body.innerText")
                hits = sorted({m.group(0).strip() for m in BAD_TEXT.finditer(text)})[:3]
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                notes = list(errors) + [f"畫面出現「{hit}」" for hit in hits]
                if overflow > 0:
                    notes.append(f"左右溢出 {overflow}px")
                errors.clear()
                problems.extend(f"{label}／{name}: {note}" for note in notes)
                print(f"  {'✗' if notes else '✓'} {name}{'  ' + '；'.join(notes) if notes else ''}")

            def click_all(selector: str, reload_each: bool) -> None:
                count = len(page.query_selector_all(selector))
                for index in range(count):
                    if reload_each:
                        load()
                    elements = page.query_selector_all(selector)
                    if index >= len(elements):
                        break
                    name = re.sub(r"\s+", "", elements[index].inner_text())
                    try:
                        elements[index].click(timeout=8000)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{label}／{name}: 點不下去（{str(exc).splitlines()[0]}）")
                        print(f"  ✗ {name}  點不下去")
                        continue
                    inspect(name)

            load()
            inspect("首頁")
            click_all(".signal-tab", reload_each=False)
            click_all(".tb-btn", reload_each=True)  # 功能視窗會蓋住其他按鈕，每個都重新載入
            page.close()
        browser.close()
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--chromium", help="Chromium 執行檔路徑（不用 playwright 內建的）")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    now = datetime.now(TW_TZ)
    print(f"網站例行檢查 {base}  {now:%Y-%m-%d %H:%M}（台北）{'' if is_trading_day(now.date()) else '・今天休市'}")
    problems = check_api(base, now)
    if not args.no_browser:
        problems += check_browser(base, args.chromium)
    print()
    if problems:
        print(f"發現 {len(problems)} 個問題：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("全部正常 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
