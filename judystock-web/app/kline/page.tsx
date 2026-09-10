"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import StockTradingBadges from "../StockTradingBadges";
import { createVisibilityGatedInterval } from "../../lib/useVisibilityGatedInterval";
import { mergeForceHistory, type ForceHistoryBar } from "../../lib/kline-force";
import { alignDailyForce, readDailyCandleSlots, retainDailyForcePoints, type DailyCandleSlot } from "../../lib/kline-daily-force";

type Period = "1m" | "5m" | "day";
type SearchStock = { ticker: string; name: string; group?: string };
type ForceBar = ForceHistoryBar;
type ForceResponse = {
  ok: boolean;
  bars?: ForceBar[];
  dayNet?: number;
  updatedAt?: string;
  error?: string;
};
type DailyForcePoint = {
  date: string;
  net: number;
  barCount: number;
  sourceInterval: "1m" | "5m";
  lastBarAt: string;
};
type ForceHistoryResponse = {
  ok: boolean;
  points?: DailyForcePoint[];
  error?: string;
};
type StoredForceData = {
  bars: ForceBar[];
  dayNet: number;
  updatedAt: string;
  savedAt: number;
};

type DocumentPictureInPictureApi = {
  requestWindow(options: { width: number; height: number }): Promise<Window>;
};
type ForcePanelPosition = { x: number; y: number };
type ForcePanelDrag = { offsetX: number; offsetY: number; lastX: number; lastY: number };

const FORCE_PANEL_POSITION_KEY = "hanstock-force-panel-position-v1";

function clamp(value: number) { return Math.max(-100, Math.min(100, value)); }
function taipeiUpdatedAt() {
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}
function forceTimeKey(value: string) {
  const match = value.match(/(?:\d{4}[\/-])?(\d{1,2})[\/-](\d{1,2})[^\d]+(\d{1,2}):(\d{2})/);
  if (!match) return value.trim();
  return `${match[1].padStart(2, "0")}/${match[2].padStart(2, "0")} ${match[3].padStart(2, "0")}:${match[4]}`;
}
function forceClockKey(value: string) {
  return value.match(/(\d{1,2}:\d{2})(?::\d{2})?$/)?.[1]?.padStart(5, "0") ?? "";
}
function shiftForceClock(value: string, minutes: number) {
  const match = value.match(/^(.*?)(\d{1,2}):(\d{2})(?::\d{2})?$/);
  if (!match) return value;
  const total = (Number(match[2]) * 60 + Number(match[3]) + minutes + 1_440) % 1_440;
  return `${match[1]}${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export default function KlinePage() {
  const [period, setPeriod] = useState<Period>("5m");
  // The embedded chart can switch periods itself. Keep the iframe's initial
  // period separate so a 1m/5m/day click does not tear down the whole chart.
  const [framePeriod, setFramePeriod] = useState<Period>("5m");
  const [frameReloadRevision, setFrameReloadRevision] = useState(0);
  const [showForce, setShowForce] = useState(true);
  const [showDailyForce, setShowDailyForce] = useState(true);
  const [showVwap, setShowVwap] = useState(true);
  const [indicatorMenuOpen, setIndicatorMenuOpen] = useState(false);
  const [forceBars, setForceBars] = useState<ForceBar[]>([]);
  const [forceDayNet, setForceDayNet] = useState(0);
  const [forceStatus, setForceStatus] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [forceUpdatedAt, setForceUpdatedAt] = useState("");
  const [toolbarUpdatedAt, setToolbarUpdatedAt] = useState("");
  const [dailyForcePoints, setDailyForcePoints] = useState<DailyForcePoint[]>([]);
  const dailyForcePlotRef = useRef<HTMLDivElement>(null);
  const [dailyCandleSlots, setDailyCandleSlots] = useState<DailyCandleSlot[]>([]);

  const [dailyForceStatus, setDailyForceStatus] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [ticker, setTicker] = useState("2344");
  const [name, setName] = useState("華邦電");
  const [symbolReady, setSymbolReady] = useState(false);
  const [pinnedBoardWindow, setPinnedBoardWindow] = useState<Window | null>(null);
  const [cursorForceDate, setCursorForceDate] = useState<string | null>(null);
  const [signalFocusTs, setSignalFocusTs] = useState<number | null>(null);
  const [forcePanelPosition, setForcePanelPosition] = useState<ForcePanelPosition | null>(null);
  const [mobileSignalLegendHtml, setMobileSignalLegendHtml] = useState("");
  const [chartKeyboardActive, setChartKeyboardActive] = useState(false);
  const [quickCode, setQuickCode] = useState("");
  const [quickStatus, setQuickStatus] = useState<"idle" | "loading" | "error">("idle");
  const pinnedWindowRef = useRef<Window | null>(null);
  const klineFrameRef = useRef<HTMLIFrameElement>(null);
  const quickKeyHandlerRef = useRef<(event: KeyboardEvent) => void>(() => undefined);
  const boundFrameDocumentsRef = useRef(new WeakSet<Document>());
  const forcePanelRef = useRef<HTMLElement>(null);
  const indicatorPickerRef = useRef<HTMLDivElement>(null);
  const forcePanelPositionRef = useRef<ForcePanelPosition | null>(null);
  const forcePanelDragRef = useRef<ForcePanelDrag | null>(null);
  const latestCrosshairTimerRef = useRef<number | null>(null);
  const periodSwitchTimerRef = useRef<number | null>(null);
  const periodSwitchTargetRef = useRef<Period | null>(null);
  useEffect(() => {
    setDailyCandleSlots([]);
    if (period !== "day") return;
    let last = "";
    const sync = () => {
      try {
        const frame = klineFrameRef.current;
        const plot = dailyForcePlotRef.current;
        if (!frame || !plot) {
          if (last) { last = ""; setDailyCandleSlots([]); }
          return;
        }
        const slots = readDailyCandleSlots(frame, plot);
        const key = JSON.stringify(slots);
        if (key !== last) { last = key; setDailyCandleSlots(slots); }
      } catch { /* The iframe may be between navigations; retry after its next render. */ }
    };
    sync();
    const timer = createVisibilityGatedInterval(sync, 250);
    return () => { timer.cancel(); };
  }, [period, ticker]);
  const openStock = useCallback((nextTicker: string, nextName: string, nextPeriod: Period) => {
    setCursorForceDate(null);
    setSignalFocusTs(null);
    setTicker(nextTicker);
    setName(nextName);
    setPeriod(nextPeriod);
    setFramePeriod(nextPeriod);
    const url = new URL(window.location.href);
    url.searchParams.set("ticker", nextTicker);
    url.searchParams.set("name", nextName);
    url.searchParams.set("interval", nextPeriod === "day" ? "1d" : nextPeriod);
    url.searchParams.delete("signalTs");
    window.history.replaceState(null, "", url);
  }, []);

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams(window.location.search);
    queueMicrotask(() => {
      if (!active) return;
      setTicker(query.get("ticker") || "2344");
      setName(query.get("name") || "華邦電");
      const initialPeriod: Period = query.get("interval") === "1m" ? "1m" : query.get("interval") === "1d" ? "day" : "5m";
      setPeriod(initialPeriod);
      setFramePeriod(initialPeriod);
      const requestedSignalTs = Number(query.get("signalTs"));
      if (Number.isFinite(requestedSignalTs) && requestedSignalTs > 0) {
        setSignalFocusTs(Math.trunc(requestedSignalTs));
        setCursorForceDate(new Intl.DateTimeFormat("zh-TW", {
          timeZone: "Asia/Taipei",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(new Date(requestedSignalTs)));
      }
      setToolbarUpdatedAt(taipeiUpdatedAt());
      setSymbolReady(true);
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(FORCE_PANEL_POSITION_KEY) ?? "null") as ForcePanelPosition | null;
      if (stored && Number.isFinite(stored.x) && Number.isFinite(stored.y)) {
        forcePanelPositionRef.current = stored;
        setForcePanelPosition(stored);
      }
    } catch {}
  }, []);
  useEffect(() => {
    forcePanelPositionRef.current = forcePanelPosition;
  }, [forcePanelPosition]);
  useEffect(() => {
    if (!indicatorMenuOpen) return;
    const closeMenu = (event: PointerEvent) => {
      if (!indicatorPickerRef.current?.contains(event.target as Node)) setIndicatorMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIndicatorMenuOpen(false);
    };
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [indicatorMenuOpen]);
  useEffect(() => {
    const clampPosition = (position: ForcePanelPosition) => {
      const panel = forcePanelRef.current;
      const container = panel?.parentElement;
      if (!panel || !container || window.matchMedia("(max-width: 700px)").matches) return position;
      const maxX = Math.max(0, container.clientWidth - panel.offsetWidth);
      const maxY = Math.max(0, container.clientHeight - panel.offsetHeight);
      return { x: Math.max(0, Math.min(maxX, position.x)), y: Math.max(0, Math.min(maxY, position.y)) };
    };
    const onPointerMove = (event: PointerEvent) => {
      const drag = forcePanelDragRef.current;
      const panel = forcePanelRef.current;
      const container = panel?.parentElement;
      if (!drag || !panel || !container) return;
      const bounds = container.getBoundingClientRect();
      const next = clampPosition({ x: event.clientX - bounds.left - drag.offsetX, y: event.clientY - bounds.top - drag.offsetY });
      drag.lastX = next.x;
      drag.lastY = next.y;
      forcePanelPositionRef.current = next;
      setForcePanelPosition(next);
    };
    const finishDrag = () => {
      const drag = forcePanelDragRef.current;
      if (!drag) return;
      forcePanelDragRef.current = null;
      const next = { x: drag.lastX, y: drag.lastY };
      forcePanelPositionRef.current = next;
      try { window.localStorage.setItem(FORCE_PANEL_POSITION_KEY, JSON.stringify(next)); } catch {}
    };
    const onResize = () => {
      const current = forcePanelPositionRef.current;
      if (!current) return;
      const next = clampPosition(current);
      if (next.x === current.x && next.y === current.y) return;
      forcePanelPositionRef.current = next;
      setForcePanelPosition(next);
      try { window.localStorage.setItem(FORCE_PANEL_POSITION_KEY, JSON.stringify(next)); } catch {}
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", finishDrag);
    window.addEventListener("pointercancel", finishDrag);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", finishDrag);
      window.removeEventListener("resize", onResize);
    };
  }, []);
  useEffect(() => {
    const syncEmbeddedKline = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin || event.source !== klineFrameRef.current?.contentWindow) return;
      const message = event.data as { type?: unknown; ticker?: unknown; name?: unknown; interval?: unknown; date?: unknown; time?: unknown; index?: unknown; html?: unknown } | null;
      if (!message) return;
      if (message.type === "hanstock-kline-data-ready") {
        const readyPeriod: Period = message.interval === "1m" ? "1m" : message.interval === "1d" ? "day" : "5m";
        if (periodSwitchTargetRef.current === readyPeriod) {
          periodSwitchTargetRef.current = null;
          if (periodSwitchTimerRef.current !== null) window.clearTimeout(periodSwitchTimerRef.current);
          periodSwitchTimerRef.current = null;
          setToolbarUpdatedAt(taipeiUpdatedAt());
        }
        return;
      }
      if (message.type === "hanstock-mobile-signal-legend") {
        if (typeof message.html === "string" && message.html.length <= 40_000) setMobileSignalLegendHtml(message.html);
        return;
      }
      if (message.type === "hanstock-battle-open-kline") {
        const nextTicker = typeof message.ticker === "string" ? message.ticker.trim().toUpperCase() : "";
        if (!/^[0-9A-Z]{2,12}$/.test(nextTicker)) return;
        const nextName = typeof message.name === "string" && message.name.trim() ? message.name.trim().slice(0, 40) : nextTicker;
        const nextPeriod: Period = message.interval === "1m" ? "1m" : message.interval === "1d" ? "day" : "5m";
        openStock(nextTicker, nextName, nextPeriod);
        return;
      }
      if (message.type === "hanstock-kline-period") {
        const nextPeriod: Period = message.interval === "1m" ? "1m" : message.interval === "1d" ? "day" : "5m";
        setCursorForceDate(null);
        setPeriod(nextPeriod);
        periodSwitchTargetRef.current = nextPeriod;
        if (periodSwitchTimerRef.current !== null) window.clearTimeout(periodSwitchTimerRef.current);
        // Keep the current chart visible while the embedded runtime paints
        // the next period. Rebuild only after a genuine long stall; the
        // runtime posts ready after the visible candle set actually changes.
        periodSwitchTimerRef.current = window.setTimeout(() => {
          if (periodSwitchTargetRef.current !== nextPeriod) return;
          periodSwitchTargetRef.current = null;
          periodSwitchTimerRef.current = null;
          setFramePeriod(nextPeriod);
          setFrameReloadRevision((current) => current + 1);
        }, 5_000);
        const url = new URL(window.location.href);
        url.searchParams.set("interval", nextPeriod === "day" ? "1d" : nextPeriod);
        window.history.replaceState(null, "", url);
        return;
      }
      if (message.type !== "hanstock-kline-crosshair") return;
      const nextCursorDate = typeof message.date === "string"
        ? message.date
        : typeof message.time === "string"
          ? period === "5m" ? shiftForceClock(message.time, -5) : message.time
          : null;
      setCursorForceDate(nextCursorDate);
      if (latestCrosshairTimerRef.current !== null) window.clearTimeout(latestCrosshairTimerRef.current);
      latestCrosshairTimerRef.current = window.setTimeout(() => setCursorForceDate(null), 10_000);
    };
    window.addEventListener("message", syncEmbeddedKline);
    return () => {
      window.removeEventListener("message", syncEmbeddedKline);
      if (latestCrosshairTimerRef.current !== null) window.clearTimeout(latestCrosshairTimerRef.current);
    };
  }, [openStock, period]);
  useEffect(() => () => {
    if (periodSwitchTimerRef.current !== null) window.clearTimeout(periodSwitchTimerRef.current);
  }, []);
  useEffect(() => {
    if (period !== "5m") setMobileSignalLegendHtml("");
  }, [period, ticker]);
  useEffect(() => {
    if (period === "day") return;
    klineFrameRef.current?.contentWindow?.postMessage({ type: "hanstock-vwap-visibility", visible: showVwap }, window.location.origin);
  }, [period, showVwap, ticker]);
  useEffect(() => {
    if (!symbolReady || period !== "5m") return;
    const syncMobileSignalLegend = () => {
      try {
        const frameDocument = klineFrameRef.current?.contentDocument;
        const locatedLegend = frameDocument?.querySelector<HTMLElement>(
          'div[data-loc="client/src/components/CandleChartDialog.tsx:1960"],div[data-loc="client/src/components/CandleChartDialog.tsx:1947"]',
        );
        const semanticLegend = frameDocument
          ? [...frameDocument.querySelectorAll<HTMLElement>("div")]
              .filter((node) => {
                const text = node.textContent?.replace(/\s+/g, "") ?? "";
                return text.includes("過905高") && text.includes("站上20MA") && text.includes("20MA轉向") && node.querySelectorAll("span").length >= 8;
              })
              .sort((a, b) => (a.textContent?.length ?? 0) - (b.textContent?.length ?? 0))[0]
          : undefined;
        const legend = semanticLegend ?? locatedLegend;
        if (!legend) return;
        if (!window.matchMedia("(max-width: 700px)").matches) {
          legend.style.removeProperty("display");
          setMobileSignalLegendHtml("");
          return;
        }
        legend.style.setProperty("display", "none", "important");
        const html = legend.innerHTML;
        if (html && html.length <= 40_000) setMobileSignalLegendHtml((current) => current === html ? current : html);
      } catch {}
    };
    syncMobileSignalLegend();
    const timer = createVisibilityGatedInterval(syncMobileSignalLegend, 2_000);
    return () => { timer.cancel(); };
  }, [period, symbolReady, ticker, name]);
  useEffect(() => {
    if (!symbolReady || period === "day") return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      try {
        const frameDocument = klineFrameRef.current?.contentDocument;
        if (!frameDocument) return;
        const candidates = [
          ...frameDocument.querySelectorAll<SVGTextElement>('text[data-loc="client/src/components/CandleChartDialog.tsx:2666"],text[data-loc="client/src/components/CandleChartDialog.tsx:2646"]'),
        ].map((node) => node.textContent?.trim() ?? "").filter((value) => /^\d{1,2}:\d{2}$/.test(value));
        if (candidates[0]) setCursorForceDate(period === "5m" ? shiftForceClock(candidates[0], -5) : candidates[0]);
      } catch {}
    };
    onVisible();
    const timer = createVisibilityGatedInterval(onVisible, 1_000);
    return () => { timer.cancel(); };
  }, [period, symbolReady, ticker, name]);
  const frameInterval = framePeriod === "day" ? "1d" : framePeriod;
  const forceInterval = period === "1m" ? "1m" : "5m";
  const embeddedKlineUrl = `/api/kline-embed/${encodeURIComponent(ticker)}?interval=${frameInterval}&name=${encodeURIComponent(name)}&uiRev=20260905-kline-signal-toggle-v35${signalFocusTs ? `&signalTs=${signalFocusTs}` : ""}`;
  const klineFrameUrl = embeddedKlineUrl;
  const isPinned = Boolean(pinnedBoardWindow && !pinnedBoardWindow.closed);

  const resolveQuickCode = useCallback(async (code: string, currentPeriod: Period) => {
    setQuickStatus("loading");
    try {
      const response = await fetch(`/api/stock-search?q=${encodeURIComponent(code)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("search-failed");
      const payload = await response.json() as { stocks?: SearchStock[] };
      const results = Array.isArray(payload.stocks) ? payload.stocks : [];
      const normalized = code.toUpperCase();
      const candidate = results.find((item) => item.ticker.toUpperCase() === normalized) ?? results[0]
        ?? (/^[0-9A-Z]{2,12}$/.test(normalized) ? { ticker: normalized, name: normalized } : undefined);
      if (!candidate) throw new Error("stock-not-found");
      openStock(candidate.ticker.toUpperCase(), candidate.name, currentPeriod);
      setQuickCode("");
      setQuickStatus("idle");
    } catch {
      setQuickStatus("error");
    }
  }, [openStock]);

  const handleQuickKey = useCallback((event: KeyboardEvent) => {
    if (!chartKeyboardActive || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const target = event.target as HTMLElement | null;
    if (target?.matches("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setQuickCode("");
      setQuickStatus("idle");
      setChartKeyboardActive(false);
      return;
    }
    if (event.key === "Backspace") {
      if (!quickCode) return;
      event.preventDefault();
      setQuickCode((current) => current.slice(0, -1));
      setQuickStatus("idle");
      return;
    }
    if (event.key === "Enter") {
      if (!quickCode || quickStatus === "loading") return;
      event.preventDefault();
      void resolveQuickCode(quickCode, period);
      return;
    }
    if (event.key.length !== 1 || !/[0-9a-z]/i.test(event.key)) return;
    event.preventDefault();
    setQuickCode((current) => `${current}${event.key.toUpperCase()}`.slice(0, 12));
    setQuickStatus("idle");
  }, [chartKeyboardActive, period, quickCode, quickStatus, resolveQuickCode]);

  useEffect(() => {
    quickKeyHandlerRef.current = handleQuickKey;
  }, [handleQuickKey]);

  useEffect(() => {
    const forwardKey = (event: KeyboardEvent) => quickKeyHandlerRef.current(event);
    window.addEventListener("keydown", forwardKey);
    return () => window.removeEventListener("keydown", forwardKey);
  }, []);

  const bindFrameKeyboard = useCallback(() => {
    try {
      const frameDocument = klineFrameRef.current?.contentDocument;
      if (!frameDocument || boundFrameDocumentsRef.current.has(frameDocument)) return;
      boundFrameDocumentsRef.current.add(frameDocument);
      frameDocument.addEventListener("pointerdown", () => {
        setChartKeyboardActive(true);
        setQuickCode("");
        setQuickStatus("idle");
      }, { passive: true });
      frameDocument.addEventListener("keydown", (event) => quickKeyHandlerRef.current(event));
    } catch {}
  }, []);

  useEffect(() => {
    if (!symbolReady) return;
    let active = true;
    const storageKey = `hanstock-force-bars-v1:${ticker}:${forceInterval}`;
    const controller = new AbortController();
    let loading = false;
    const readStored = (): StoredForceData | null => {
      try {
        const stored = JSON.parse(window.localStorage.getItem(storageKey) ?? "null") as StoredForceData | null;
        if (!stored || !Array.isArray(stored.bars) || stored.bars.length === 0) return null;
        if (!Number.isFinite(stored.savedAt) || Date.now() - stored.savedAt > 400 * 864e5) return null;
        return stored;
      } catch {
        return null;
      }
    };
    const restoreStored = () => {
      const stored = readStored();
      if (!active || !stored) return false;
      setForceBars(stored.bars);
      setForceDayNet(stored.dayNet);
      setForceUpdatedAt(stored.updatedAt);
      setForceStatus("ready");
      return true;
    };
    const restored = restoreStored();
    const load = async () => {
      if (loading || !active) return;
      loading = true;
      try {
        const response = await fetch(`/api/force-bars?ticker=${encodeURIComponent(ticker)}&interval=${forceInterval}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as ForceResponse;
        if (!active) return;
        setToolbarUpdatedAt(taipeiUpdatedAt());
        const nextBars = mergeForceHistory(readStored()?.bars ?? [], payload.ok && Array.isArray(payload.bars) ? payload.bars : []);
        if (nextBars.length > 0) {
          const nextDayNet = nextBars.at(-1)?.dayNet ?? 0;
          const nextUpdatedAt = nextBars.at(-1)?.date ?? "";
          setForceBars(nextBars);
          setForceDayNet(nextDayNet);
          setForceUpdatedAt(nextUpdatedAt);
          setForceStatus("ready");
          try {
            window.localStorage.setItem(storageKey, JSON.stringify({
              bars: nextBars,
              dayNet: nextDayNet,
              updatedAt: nextUpdatedAt,
              savedAt: Date.now(),
            } satisfies StoredForceData));
          } catch {}
        } else if (!restoreStored()) {
          setForceBars([]);
          setForceDayNet(0);
          setForceUpdatedAt("");
          setForceStatus(payload.ok ? "empty" : "error");
        }
      } catch {
        if (active && !restoreStored()) {
          setForceBars([]);
          setForceDayNet(0);
          setForceStatus("error");
        }
      } finally { loading = false; }
    };
    queueMicrotask(() => { if (active && !restored) setForceStatus("loading"); });
    void load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 30_000);
    return () => { active = false; controller.abort(); timer.cancel(); };
  }, [ticker, forceInterval, symbolReady]);
  useEffect(() => {
    setDailyForcePoints([]);
  }, [ticker]);
  useEffect(() => {
    if (!symbolReady || !showDailyForce || period !== "day") return;
    let active = true;
    const load = async () => {
      try {
        const response = await fetch(`/api/force-history?ticker=${encodeURIComponent(ticker)}`, { cache: "no-store" });
        const payload = await response.json() as ForceHistoryResponse;
        if (!active) return;
        const points = payload.ok && Array.isArray(payload.points) ? payload.points : [];
        if (payload.ok) setDailyForcePoints(previous => retainDailyForcePoints(previous, points));
        setDailyForceStatus(payload.ok ? points.length > 0 ? "ready" : "empty" : "error");
      } catch {
        if (active) setDailyForceStatus("error");
      }
    };
    queueMicrotask(() => { if (active) setDailyForceStatus("loading"); });
    const initial = window.setTimeout(load, 250);
    const timer = createVisibilityGatedInterval(() => { void load(); }, 30_000);
    return () => {
      active = false;
      window.clearTimeout(initial);
      timer.cancel();
    };
  }, [ticker, symbolReady, showDailyForce, period, forceUpdatedAt]);

  const latest=forceBars.at(-1) ?? { date: "", net: 0, ultra: false };
  const cursorForceKey = cursorForceDate ? forceTimeKey(cursorForceDate) : "";
  const cursorClockKey = cursorForceDate ? forceClockKey(cursorForceDate) : "";
  const exactCursorForceIndex = cursorForceKey ? forceBars.findIndex((bar) =>
    forceTimeKey(bar.date) === cursorForceKey || Boolean(cursorClockKey && forceClockKey(bar.date) === cursorClockKey),
  ) : -1;
  const clockMinutes = (value: string) => {
    const match = forceClockKey(value).match(/(\d{1,2}):(\d{2})/);
    return match ? Number(match[1]) * 60 + Number(match[2]) : Number.NaN;
  };
  const cursorMinute = cursorForceDate ? clockMinutes(cursorForceDate) : Number.NaN;
  const nearestCursorForceIndex = exactCursorForceIndex >= 0 || !Number.isFinite(cursorMinute) ? { index: -1, distance: Number.POSITIVE_INFINITY } : forceBars.reduce((best, bar, index) => {
    const distance = Math.abs(clockMinutes(bar.date) - cursorMinute);
    return distance < best.distance ? { index, distance } : best;
  }, { index: -1, distance: Number.POSITIVE_INFINITY });
  const cursorForceIndex = exactCursorForceIndex >= 0 ? exactCursorForceIndex : nearestCursorForceIndex.distance <= (forceInterval === "5m" ? 3 : 1) ? nearestCursorForceIndex.index : -1;
  const isHistoricalCursor = cursorForceIndex >= 0 && cursorForceIndex < forceBars.length - 1;
  const displayedForceBar = isHistoricalCursor ? forceBars[cursorForceIndex] : latest;
  const displayedDayNet = isHistoricalCursor
    ? displayedForceBar.dayNet ?? forceBars.slice(0, cursorForceIndex + 1).reduce((total, bar) => total + bar.net, 0)
    : forceDayNet;
  const scoreBarIndex = cursorForceIndex >= 0 ? cursorForceIndex : Math.max(0, forceBars.length - 1);
  const scoreBar = forceBars[scoreBarIndex] ?? latest;
  const scoreHistory = forceBars.slice(0, scoreBarIndex + 1);
  const maxAbs = Math.max(...scoreHistory.map((bar)=>Math.abs(bar.net)),0.01);
  const recent = scoreHistory.slice(-5);
  const sameSide = recent.filter((bar)=>Math.sign(bar.net)===Math.sign(scoreBar.net)).length;
  const continuity = scoreBar.net === 0 ? 0 : (sameSide / Math.max(recent.length,1)) * 15 * Math.sign(scoreBar.net);
  const score=Math.round(clamp((scoreBar.net/maxAbs)*70+continuity+(scoreBar.ultra?5*Math.sign(scoreBar.net):0)));
  const judgement=score>=60?"主力強力偏多":score>=25?"主力偏多":score<=-60?"主力強力偏空":score<=-25?"主力偏空":score>0?"買盤略占優":score<0?"賣盤略占優":"買賣平衡";
  const scoreText = `${score > 0 ? "+" : ""}${score}`;
  const judgementTone = score <= -25 ? "force-state-bear" : score >= 25 ? "force-state-bull" : "force-state-watch";
  const judgementColor = score <= -25 ? "#66d5a4" : score >= 25 ? "#ff777b" : "#d7dee5";
  const thresholdNote = score >= 25
    ? "已達主力偏多門檻"
    : score <= -25
      ? "已達主力偏空門檻"
      : score > 0
        ? `距主力偏多門檻還差 ${25 - score} 分`
        : score < 0
          ? `距主力偏空門檻還差 ${25 - Math.abs(score)} 分`
          : "買賣力道相當，尚未形成明確方向";
  const formatLots = (millionShares: number) => {
    const lots = Math.round(millionShares * 1_000);
    return `${lots > 0 ? "+" : ""}${lots.toLocaleString("zh-TW")}`;
  };
  const rawDisplayedTime = displayedForceBar.date.match(/(\d{1,2}:\d{2})(?::\d{2})?$/)?.[1] ?? "--";
  const displayedTime = isHistoricalCursor && forceInterval === "5m" ? shiftForceClock(rawDisplayedTime, 5) : rawDisplayedTime;
  const visibleDailyForcePoints = dailyForcePoints.slice(-60);
  const { aligned: alignedDailyForce, segments: dailyForceSegments, total: dailyForceTotal, maxAbs: dailyForceMaxAbs } = alignDailyForce(dailyCandleSlots, visibleDailyForcePoints);
  const missingDailyForceDates = dailyForceSegments.flatMap(segment => segment.missingDates);
  const closePinnedBoard = () => {
    const board = pinnedWindowRef.current;
    pinnedWindowRef.current = null;
    setPinnedBoardWindow(null);
    if (board && !board.closed) board.close();
  };
  const openPinnedBoard = async () => {
    const currentBoard = pinnedWindowRef.current;
    if (currentBoard && !currentBoard.closed) {
      currentBoard.focus();
      return;
    }
    const pictureInPicture = (window as Window & { documentPictureInPicture?: DocumentPictureInPictureApi }).documentPictureInPicture;
    if (!pictureInPicture) {
      window.alert("這個瀏覽器無法開啟螢幕最上層釘選視窗，請使用最新版 Chrome 或 Edge 電腦版。");
      return;
    }
    try {
      const pinnedWindow = await pictureInPicture.requestWindow({ width: 1420, height: 880 });
      document.querySelectorAll('link[rel="stylesheet"], style').forEach((node) => {
        pinnedWindow.document.head.appendChild(node.cloneNode(true));
      });
      const pinnedStyle = pinnedWindow.document.createElement("style");
      pinnedStyle.textContent = "html,body{margin:0;width:100%;height:100%;background:#07090b;overflow:hidden}";
      pinnedWindow.document.head.appendChild(pinnedStyle);
      pinnedWindow.document.title = `HanStock ${ticker} ${name} K 線置頂`;
      pinnedWindowRef.current = pinnedWindow;
      setPinnedBoardWindow(pinnedWindow);
      pinnedWindow.addEventListener("pagehide", () => {
        pinnedWindowRef.current = null;
        setPinnedBoardWindow(null);
      }, { once: true });
    } catch {
      closePinnedBoard();
    }
  };
  const togglePinnedChart = async () => {
    if (pinnedWindowRef.current && !pinnedWindowRef.current.closed) {
      closePinnedBoard();
      return;
    }
    // Keep requestWindow in the original click handler. Delaying it to an
    // effect loses browser user activation and makes the pin appear to do
    // nothing. Document PiP is the browser's always-on-top window.
    await openPinnedBoard();
  };
  const beginForcePanelDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (window.matchMedia("(max-width: 700px)").matches) return;
    const panel = forcePanelRef.current;
    const container = panel?.parentElement;
    if (!panel || !container) return;
    event.preventDefault();
    const panelBounds = panel.getBoundingClientRect();
    const containerBounds = container.getBoundingClientRect();
    const initialX = panelBounds.left - containerBounds.left;
    const initialY = panelBounds.top - containerBounds.top;
    forcePanelDragRef.current = {
      offsetX: event.clientX - panelBounds.left,
      offsetY: event.clientY - panelBounds.top,
      lastX: initialX,
      lastY: initialY,
    };
    const initial = { x: initialX, y: initialY };
    forcePanelPositionRef.current = initial;
    setForcePanelPosition(initial);
  };

  const pinnedBoard = pinnedBoardWindow && !pinnedBoardWindow.closed ? createPortal(
    <main className="pinned-kline-board pinned-count-1">
      <header>
        <div><strong>HanStock 目前 K 線置頂</strong><span>螢幕最上層 · 單一固定視圖</span></div>
        <button onClick={closePinnedBoard}>解除置頂</button>
      </header>
      <section className="pinned-kline-grid">
        <article className="pinned-kline-card">
          <div><strong>{ticker} {name}</strong><span>{period === "1m" ? "1分" : period === "5m" ? "5分" : "日線"}</span><button onClick={closePinnedBoard} aria-label={`解除置頂 ${ticker} ${name}`}>×</button></div>
          <iframe src={`${window.location.origin}/api/kline-embed/${encodeURIComponent(ticker)}?interval=${period === "day" ? "1d" : period}&name=${encodeURIComponent(name)}&uiRev=20260905-kline-signal-toggle-v35`} title={`${ticker} ${name} 置頂 K 線`} />
        </article>
      </section>
    </main>,
    pinnedBoardWindow.document.body,
  ) : null;
  return <main className={`kline-shell ${period === "day" ? "is-day-mode" : ""}`}>
    {chartKeyboardActive && <aside className={`kline-quick-switch${quickStatus === "error" ? " is-error" : ""}`} aria-live="polite">
      <span>鍵盤快速換股</span>
      <form role="search" onSubmit={(event) => {
        event.preventDefault();
        if (!quickCode.trim() || quickStatus === "loading") return;
        void resolveQuickCode(quickCode.trim(), period);
      }}>
        <input
          autoFocus
          type="search"
          value={quickCode}
          onChange={(event) => { setQuickCode(event.target.value.slice(0, 40)); setQuickStatus("idle"); }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            setQuickCode("");
            setQuickStatus("idle");
            setChartKeyboardActive(false);
          }}
          placeholder="直接輸入股票代號或名稱"
          aria-label="直接輸入股票代號或名稱"
          autoComplete="off"
          enterKeyHint="go"
        />
        <button type="submit" disabled={!quickCode.trim() || quickStatus === "loading"}>{quickStatus === "loading" ? "搜尋中" : "換股"}</button>
      </form>
      <small>{quickStatus === "loading" ? "搜尋中…" : quickStatus === "error" ? "找不到股票；可修改代號再按 Enter" : "Enter 確認 · Backspace 刪除 · Esc 取消"}</small>
    </aside>}
    <header className="kline-commandbar">
      <div className="kline-commandbar-main">
        <button className="kline-back" onClick={() => {
          const returnTo = new URLSearchParams(window.location.search).get("returnTo");
          if (returnTo?.startsWith("/") && !returnTo.startsWith("//")) window.location.assign(returnTo);
          else if (window.history.length > 1) window.history.back();
          else window.location.assign("/");
        }} aria-label="返回上一頁" title="返回上一頁">‹</button>
        {signalFocusTs && <span className="kline-signal-focus">🔔 訊號時間 <b>{new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(signalFocusTs))}</b></span>}
        <span className="kline-trading-status"><b>{ticker} {name}</b><StockTradingBadges ticker={ticker} compact /></span>
      </div>
      <div className="kline-commandbar-right">
        <div className="indicator-picker" ref={indicatorPickerRef}>
          <button className={showForce || showDailyForce || (period !== "day" && showVwap) ? "active" : ""} onClick={()=>setIndicatorMenuOpen((value)=>!value)} aria-expanded={indicatorMenuOpen}>技術指標 <b>已開 {(showForce ? 1 : 0) + (showDailyForce ? 1 : 0) + (period !== "day" && showVwap ? 1 : 0)} 項</b></button>
          {indicatorMenuOpen&&<div className="indicator-menu" role="group" aria-label="技術指標選單">
            <header><strong>技術指標</strong><small>勾選要顯示在 K 線上的資訊</small></header>
            <label className={period === "day" ? "is-disabled" : ""}><input type="checkbox" checked={period !== "day" && showVwap} disabled={period === "day"} onChange={(event)=>setShowVwap(event.target.checked)} /><span><b>VWAP 成交量加權均價</b><small>亮黃色粗線疊加在 1 分／5 分 K 主圖</small></span><i>分</i></label>
            <label><input type="checkbox" checked={showForce} onChange={(event)=>setShowForce(event.target.checked)} /><span><b>盤中主力大單進出</b><small>1 分／5 分 K 浮動訊息框</small></span><i>分</i></label>
            <label><input type="checkbox" checked={showDailyForce} onChange={(event)=>setShowDailyForce(event.target.checked)} /><span><b>日線主力大單累積</b><small>每日淨量柱狀＋累積趨勢線</small></span><i>日</i></label>
          </div>}
        </div>
        <button className="kline-grid-open" onClick={() => {
          const url = new URL("/kline-grid", window.location.origin);
          url.searchParams.set("ticker", ticker);
          url.searchParams.set("name", name);
          if (window.innerWidth <= 700 || window.matchMedia("(pointer: coarse)").matches) window.location.assign(url.toString());
          else {
            const child = window.open(url.toString(), "_blank", "popup=yes,width=1580,height=980,resizable=yes,scrollbars=yes");
            if (!child) window.location.assign(url.toString());
          }
        }} title="在同一個視窗開啟四個五分鐘 K 線">▦ 四格 K 線</button>
        <button className={`kline-pin ${isPinned ? "active" : ""}`} onClick={()=>void togglePinnedChart()} title={isPinned ? "取消目前 K 線的螢幕最上層置頂" : "將目前 K 線置頂，切換其他視窗仍保持顯示"}>{isPinned ? "取消置頂" : "📌 置頂目前 K 線"}</button>
        <button className="kline-refresh" onClick={()=>window.location.reload()} aria-label="重新整理 K 線與主力資料" title="重新整理 K 線與主力資料">↻ 重新整理</button>
        <time className="kline-updated">更新於 {toolbarUpdatedAt || "讀取中"}</time>
      </div>
    </header>
    <section className={`price-chart original-kline-panel ${period === "day" ? "is-day" : ""}${chartKeyboardActive ? " is-keyboard-active" : ""}`} aria-label={`${period} 原始版 K線圖`} onPointerDown={() => { if (window.matchMedia("(max-width: 700px)").matches) return; setChartKeyboardActive(true); setQuickCode(""); setQuickStatus("idle"); }}>
      {symbolReady&&<iframe
        key={`${klineFrameUrl}:${frameReloadRevision}`}
        ref={klineFrameRef}
        className="original-kline-frame"
        src={klineFrameUrl}
        title={`${ticker} ${name} 原始版完整 K 線`}
        loading="eager"
        onLoad={() => {
          klineFrameRef.current?.contentWindow?.postMessage({ type: "hanstock-vwap-visibility", visible: period !== "day" && showVwap }, window.location.origin);
          bindFrameKeyboard();
        }}
      />}
      {false&&period !== "day"&&showForce&&<aside
        ref={forcePanelRef}
        className="force-chart-sidecar"
        aria-label="可拖曳的盤中主力大單進出說明"
        style={forcePanelPosition ? { left: forcePanelPosition.x, top: forcePanelPosition.y, bottom: "auto" } : undefined}
      >
        <div className="force-panel-handle" onPointerDown={beginForcePanelDrag} title="按住並拖曳可移動訊息視窗">
          <div><strong>盤中主力大單進出</strong><small>拖曳移動</small></div>
          <button type="button" onPointerDown={(event)=>event.stopPropagation()} onClick={()=>setShowForce(false)} aria-label="關閉盤中主力大單訊息" title="關閉">×</button>
        </div>
        <span>{forceInterval === "1m" ? "1 分 K" : "5 分 K"} · 已併入 K 線時間軸</span>
        <div className="force-metrics">
          <span>{isHistoricalCursor ? "游標所在" : "最新"}{forceInterval === "1m" ? "1分K" : "5分K"}主力淨量 <b className={displayedForceBar.net < 0 ? "force-negative" : displayedForceBar.net > 0 ? "force-positive" : "force-neutral"}>{formatLots(displayedForceBar.net)} 張（{displayedTime}）</b></span>
          <span>{isHistoricalCursor ? "截至游標時間今日累計" : "今日主力大單累計淨量"} <strong>{formatLots(displayedDayNet)} 張</strong></span>
          <span>目前短線主力狀態 <em className={judgementTone} style={{ color: judgementColor }}>{judgement}（{scoreText}）</em></span>
          <div className="force-threshold-scale" aria-label="主力狀態判讀範圍">
            <b className="bear">≤ -25 主力偏空</b>
            <b className="watch">-24～+24 觀察區</b>
            <b className="bull">≥ +25 主力偏多</b>
          </div>
          <small className="force-threshold-note">{thresholdNote}</small>
        </div>
        <div className="force-score-grid"><span><b>50%</b>大單淨買賣</span><span><b>20%</b>價格推動</span><span><b>15%</b>連續程度</span><span><b>10%</b>五檔失衡</span><span><b>5%</b>特大單占比</span></div>
        <small className={forceStatus === "ready" ? "data-ok" : "data-degraded"}>{forceStatus === "ready" ? `${ticker} 共 ${forceBars.length} 根${forceUpdatedAt ? ` · ${forceUpdatedAt}` : ""}` : forceStatus === "loading" ? "正在讀取主力資料…" : "主力資料將自動重試"}</small>
      </aside>}
      {period === "day"&&<section className={`daily-force-indicator ${showDailyForce ? "" : "is-paused"}`} aria-label="日線主力大單累積指標">
        <header>
          <div><strong>日線主力大單累積</strong><small>紅柱為當日淨買、綠柱為當日淨賣、黃線為保存期間累積{missingDailyForceDates.length > 0 ? "；虛線跨越缺資料日期" : ""}</small></div>
          <span className={showDailyForce ? dailyForceTotal > 0 ? "positive" : dailyForceTotal < 0 ? "negative" : "neutral" : "neutral"}>{showDailyForce ? `累積 ${formatLots(dailyForceTotal)} 張` : "已預留"}</span>
          <button type="button" onClick={()=>setShowDailyForce((value)=>!value)} aria-label={showDailyForce ? "暫停顯示日線主力大單累積" : "顯示日線主力大單累積"} title={showDailyForce ? "暫停顯示" : "顯示"}>{showDailyForce ? "×" : "＋"}</button>
        </header>
        {!showDailyForce ? <div className="daily-force-empty">已保留「日線主力大單累積」副圖位置，點右上角＋即可顯示</div> : visibleDailyForcePoints.length > 0 ? <>
          <div className="daily-force-plot" ref={dailyForcePlotRef}>
            <div className="daily-force-zero" />
            {!alignedDailyForce.some(Boolean) && <div className="daily-force-empty">{dailyCandleSlots.length ? "目前日 K 範圍沒有已保存的主力資料" : "正在對齊日 K 線…"}</div>}
            <svg viewBox="0 0 1000 120" preserveAspectRatio="none" role="img" aria-label="依日 K 日期對齊的主力淨量與累積線" style={{ pointerEvents: "auto" }}>
              {alignedDailyForce.map((point) => {
                if (!point) return null;
                const height = Math.max(1, Math.abs(point.net) / dailyForceMaxAbs * 57.6);
                return <g key={point.date}><title>{`${point.date} 淨量 ${formatLots(point.net)} 張；累積 ${formatLots(point.cumulative)} 張`}</title>
                  <rect x={point.x - point.width / 2} width={point.width} y={point.net > 0 ? 60 - height : 60} height={height} fill={point.net > 0 ? "#e95a60" : point.net < 0 ? "#24b77c" : "#b7c0c8"} />
                </g>;
              })}
              {dailyForceSegments.map(({ from, to, missingDates }) => <polyline key={`${from.date}:${to.date}`}
                points={`${from.x},${from.y} ${to.x},${to.y}`} strokeDasharray={missingDates.length ? "6 5" : undefined}>
                <title>{missingDates.length ? `${missingDates.join("、")} 主力資料未取得；虛線僅銜接已保存累積值` : `${from.date} 至 ${to.date} 已保存主力累積`}</title>
              </polyline>)}
            </svg>
          </div>
          <footer><span>{visibleDailyForcePoints[0]?.date.slice(5)}</span><b title={missingDailyForceDates.join("、")}>{missingDailyForceDates.length ? `缺資料：${missingDailyForceDates.map(date => date.slice(5)).join("、")}；柱狀留白` : `資料自 ${dailyForcePoints[0]?.date} 開始保存`}</b><span>{visibleDailyForcePoints.at(-1)?.date.slice(5)}</span></footer>
        </> : <div className={`daily-force-empty ${dailyForceStatus === "error" ? "is-error" : ""}`}>{dailyForceStatus === "loading" ? "正在載入每日主力大單資料…" : dailyForceStatus === "error" ? "每日資料庫暫時無法讀取，將自動重試" : "全市場資料尚未完成首次盤中收集"}</div>}
      </section>}
    </section>
    {period !== "day"&&showForce&&<aside
      ref={forcePanelRef}
      className="force-chart-sidecar force-chart-after"
      aria-label="可拖曳的盤中主力大單進出說明"
      style={forcePanelPosition ? { left: forcePanelPosition.x, top: forcePanelPosition.y, bottom: "auto" } : undefined}
    >
      <div className="force-panel-handle" onPointerDown={beginForcePanelDrag} title="按住並拖曳可移動訊息視窗">
        <div><strong>盤中主力大單進出</strong><small>拖曳移動</small></div>
        <button type="button" onPointerDown={(event)=>event.stopPropagation()} onClick={()=>setShowForce(false)} aria-label="關閉盤中主力大單訊息" title="關閉">×</button>
      </div>
      <span>{forceInterval === "1m" ? "1 分 K" : "5 分 K"} · 已併入 K 線時間軸</span>
      <div className="force-metrics">
        <span>{isHistoricalCursor ? "游標所在" : "最新"}{forceInterval === "1m" ? "1分K" : "5分K"}主力淨量 <b className={displayedForceBar.net < 0 ? "force-negative" : displayedForceBar.net > 0 ? "force-positive" : "force-neutral"}>{formatLots(displayedForceBar.net)} 張（{displayedTime}）</b></span>
        <span>{isHistoricalCursor ? "截至游標時間今日累計" : "今日主力大單累計淨量"} <strong>{formatLots(displayedDayNet)} 張</strong></span>
        <span>目前短線主力狀態 <em className={judgementTone} style={{ color: judgementColor }}>{judgement}（{scoreText}）</em></span>
        <div className="force-threshold-scale" aria-label="主力狀態判讀範圍"><b className="bear">≤ -25 主力偏空</b><b className="watch">-24～+24 觀察區</b><b className="bull">≥ +25 主力偏多</b></div>
        <small className="force-threshold-note">{thresholdNote}</small>
      </div>
      <div className="force-score-grid"><span><b>50%</b>大單淨買賣</span><span><b>20%</b>價格推動</span><span><b>15%</b>連續程度</span><span><b>10%</b>五檔失衡</span><span><b>5%</b>特大單占比</span></div>
      <small className={forceStatus === "ready" ? "data-ok" : "data-degraded"}>{forceStatus === "ready" ? `${ticker} 共 ${forceBars.length} 根${forceUpdatedAt ? ` · ${forceUpdatedAt}` : ""}` : forceStatus === "loading" ? "正在讀取主力資料…" : "主力資料將自動重試"}</small>
    </aside>}
    <p className="chart-completeness kline-bottom-note">上方直接使用 HanStock 原始版完整 K 線；可縮放、拖曳、切換指標與查看真實價格刻度。</p>
    {period === "5m"&&mobileSignalLegendHtml&&<section
      className="mobile-signal-legend"
      aria-label="五分 K 盤中訊號圖例"
      dangerouslySetInnerHTML={{ __html: mobileSignalLegendHtml }}
    />}
    {pinnedBoard}
  </main>;
}
