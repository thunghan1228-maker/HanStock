/** Shared session policy for fetched signals, retained signals and both SVG layers. */
export function createKlineSignalSessionBootstrap(ticker: string, closedDates: ReadonlySet<string>) {
  return `<script id="hanstock-signal-session-filter-bootstrap">
(() => {
  const closedDates = new Set(${JSON.stringify([...closedDates])});
  const tickerLatchKey = "hanstock-ticker-signal-latch-v2:" + ${JSON.stringify(ticker)};
  const marketLatchKey = "hanstock-market-break15k-latch-v1";
  const originalFetch = window.fetch.bind(window);
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  });
  const parts = value => {
    const items = formatter.formatToParts(new Date(value));
    const part = type => items.find(item => item.type === type)?.value || "";
    return { date: part("year") + "-" + part("month") + "-" + part("day"),
      weekday: part("weekday"), minute: Number(part("hour")) * 60 + Number(part("minute")) };
  };
  const displayDate = () => {
    const clock = parts(Date.now());
    if (clock.weekday !== "Sat" && clock.weekday !== "Sun" &&
        !closedDates.has(clock.date) && clock.minute >= 8 * 60 + 45) return clock.date;
    const cursor = new Date(clock.date + "T00:00:00Z");
    do { cursor.setUTCDate(cursor.getUTCDate() - 1); }
    while (cursor.getUTCDay() === 0 || cursor.getUTCDay() === 6 || closedDates.has(cursor.toISOString().slice(0, 10)));
    return cursor.toISOString().slice(0, 10);
  };
  const signalDate = value => Number.isFinite(value) ? parts(value).date : "";
  const visible = row => signalDate(row?.barTs) === displayDate();
  const candleVisible = bar => {
    if (Number.isFinite(bar?.ts)) return signalDate(bar.ts) === displayDate();
    const date = String(bar?.date || "");
    const target = displayDate();
    return date.startsWith(target) || date.startsWith(target.replaceAll("-", "/")) ||
      date.startsWith(target.slice(5).replace("-", "/") + " ");
  };
  const readLatch = key => { try { const rows = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(rows) ? rows : []; } catch { return []; } };
  const writeLatch = (key, rows) => { try { localStorage.setItem(key, JSON.stringify(rows)); } catch {} };
  const mergeRows = (...groups) => {
    const merged = new Map();
    for (const row of groups.flat()) if (visible(row))
      merged.set([row.ticker || "", row.kind, row.barTs, row.note || ""].join("|"), row);
    return [...merged.values()].sort((a, b) => a.barTs - b.barTs);
  };
  const maskNativeMarkers = () => {
    for (const marker of document.querySelectorAll("[data-hanstock-native-signal-date]")) {
      const display = candleVisible({date: marker.getAttribute("data-hanstock-native-signal-date")}) ? "" : "none";
      if (marker.style.display !== display) marker.style.display = display;
    }
  };
  const refreshSession = () => {
    const target = displayDate();
    if (window.__hanstockSignalDisplayDate !== target) {
      window.__hanstockSignalDisplayDate = target;
      writeLatch(tickerLatchKey, mergeRows(readLatch(tickerLatchKey)));
      writeLatch(marketLatchKey, mergeRows(readLatch(marketLatchKey)));
      window.dispatchEvent(new CustomEvent("hanstock-signal-session-change", {detail: {date: target}}));
    }
    maskNativeMarkers();
    return target;
  };
  window.__hanstockSignalSessionDate = displayDate;
  window.__hanstockIsSignalVisible = visible;
  window.__hanstockIsSignalCandleVisible = candleVisible;
  window.__hanstockSignalSinceTs = () => Date.parse(displayDate() + "T00:00:00+08:00");
  window.__hanstockSignalCutoverMinute = 8 * 60 + 45;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    const url = String(typeof args[0] === "string" ? args[0] : args[0]?.url || "");
    const tickerRequest = url.includes("schedule.intradaySignalsByTicker");
    const marketRequest = !tickerRequest && url.includes("schedule.intradaySignals");
    if (!response.ok || !tickerRequest && !marketRequest) return response;
    try {
      const payload = await response.clone().json();
      const data = payload?.[0]?.result?.data?.json;
      const target = refreshSession();
      if (tickerRequest && Array.isArray(data)) {
        const tickerHistory = mergeRows(readLatch(tickerLatchKey), data);
        writeLatch(tickerLatchKey, tickerHistory);
        const rows = mergeRows(tickerHistory, readLatch(marketLatchKey));
        payload[0].result.data.json = rows;
        window.__hanstockSignalDisplayCount = rows.length;
        window.__hanstockTickerSignalLatchCount = tickerHistory.length;
      } else if (marketRequest && data && !Array.isArray(data)) {
        const incoming = Array.isArray(data.marketSignals) ? data.marketSignals : [];
        const latched = mergeRows(readLatch(marketLatchKey), incoming).filter(row => row.kind === "break15kLow");
        writeLatch(marketLatchKey, latched);
        data.marketSignals = mergeRows(incoming, latched);
        data.date = target;
        window.__hanstockBreak15kLatchDate = target;
        window.__hanstockBreak15kLatchCount = latched.length;
        window.dispatchEvent(new CustomEvent("hanstock-market-signals-latched", {detail: {date: target, signals: latched}}));
      }
      const headers = new Headers(response.headers);
      headers.set("content-type", "application/json; charset=utf-8");
      headers.delete("content-length");
      headers.delete("content-encoding");
      return new Response(JSON.stringify(payload), {status: response.status, statusText: response.statusText, headers});
    } catch { return response; }
  };
  new MutationObserver(maskNativeMarkers).observe(document.documentElement,
    {subtree: true, childList: true, attributes: true, attributeFilter: ["data-hanstock-native-signal-date"]});
  window.addEventListener("focus", refreshSession);
  document.addEventListener("visibilitychange", refreshSession);
  refreshSession();
  let sessionTimer = null;
  const syncSessionTimer = () => {
    if (sessionTimer !== null) { clearInterval(sessionTimer); sessionTimer = null; }
    if (!document.hidden) sessionTimer = setInterval(refreshSession, 60000);
  };
  document.addEventListener("visibilitychange", syncSessionTimer);
  syncSessionTimer();
})();
</script>`;
}
