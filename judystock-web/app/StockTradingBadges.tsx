"use client";

import { useEffect, useState, type ReactNode } from "react";
import { futureStatusLabel, isIndividualStockCode, type StockTradingStatus } from "../lib/stock-trading-status";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";

const STATUS_REFRESH_MS = 30 * 60_000;
const STATUS_STALE_MS = 7 * 24 * 60 * 60_000;
const STATUS_RETRY_MS = 5_000;
const STATUS_PARTIAL_REFRESH_MS = 10_000;
const STATUS_PARTIAL_STALE_MS = 5 * 60_000;
const STATUS_STORAGE_KEY = "hanstock:stock-trading-status:v3";
type CachedStatus = { status: StockTradingStatus; refreshAt: number; staleUntil: number };

const statusCache = new Map<string, CachedStatus>();
const listeners = new Map<string, Set<(status: StockTradingStatus | null) => void>>();
const pending = new Set<string>();
let flushTimer: number | null = null;
let refreshTimer: ReturnType<typeof createVisibilityGatedInterval> | null = null;
let persistTimer: number | null = null;
let retryTimer: number | null = null;
const retryPending = new Set<string>();
let storageHydrated = false;

function needsRetry(status: StockTradingStatus | null | undefined) {
  return !status || status.margin === "unknown" || status.short === "unknown" || status.dayTrade === "unknown" || status.futuresReady === false || status.dispositionReady === false;
}

function mergeStatus(previous: StockTradingStatus | undefined, next: StockTradingStatus) {
  if (!previous) return next;
  return {
    ...next,
    margin: next.margin === "unknown" && previous.margin !== "unknown" ? previous.margin : next.margin,
    short: next.short === "unknown" && previous.short !== "unknown" ? previous.short : next.short,
    dayTrade: next.dayTrade === "unknown" && previous.dayTrade !== "unknown" ? previous.dayTrade : next.dayTrade,
    stockFuture: next.futuresReady === false && previous.futuresReady !== false ? previous.stockFuture : next.stockFuture,
    miniStockFuture: next.futuresReady === false && previous.futuresReady !== false ? previous.miniStockFuture : next.miniStockFuture,
    futuresReady: next.futuresReady === false && previous.futuresReady !== false ? previous.futuresReady : next.futuresReady,
    disposition: next.dispositionReady === false && previous.dispositionReady !== false ? previous.disposition : next.disposition,
    dispositionReady: next.dispositionReady === false && previous.dispositionReady !== false ? previous.dispositionReady : next.dispositionReady,
  };
}

function hydrateStatusCache() {
  if (storageHydrated || typeof window === "undefined") return;
  storageHydrated = true;
  try {
    const saved = JSON.parse(window.localStorage.getItem(STATUS_STORAGE_KEY) ?? "{}") as Record<string, CachedStatus>;
    const now = Date.now();
    Object.entries(saved).forEach(([code, item]) => {
      if (item?.status?.code === code && item.staleUntil > now) statusCache.set(code, item);
    });
  } catch {
    // A damaged browser cache is disposable; the normal batched request repairs it.
  }
}

function persistStatusCache() {
  if (persistTimer !== null || typeof window === "undefined") return;
  persistTimer = window.setTimeout(() => {
    persistTimer = null;
    try {
      const now = Date.now();
      const saved = Object.fromEntries([...statusCache].filter(([, item]) => item.staleUntil > now));
      window.localStorage.setItem(STATUS_STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage can be unavailable in private browsing; the in-memory cache still works.
    }
  }, 0);
}

function publish(code: string, status: StockTradingStatus | null) {
  let visibleStatus = statusCache.get(code)?.status ?? null;
  if (status) {
    const now = Date.now();
    const resolved = mergeStatus(statusCache.get(code)?.status, status);
    const partial = needsRetry(resolved);
    statusCache.set(code, { status: resolved, refreshAt: now + (partial ? STATUS_PARTIAL_REFRESH_MS : STATUS_REFRESH_MS), staleUntil: now + (partial ? STATUS_PARTIAL_STALE_MS : STATUS_STALE_MS) });
    visibleStatus = resolved;
    persistStatusCache();
  }
  listeners.get(code)?.forEach((listener) => listener(visibleStatus));
}

function ensureRefreshTimer() {
  if (refreshTimer !== null) return;
  refreshTimer = createVisibilityGatedInterval(() => listeners.forEach((_group, code) => requestStatus(code)), STATUS_REFRESH_MS);
}

function scheduleRetry(codes: string[]) {
  codes.forEach((code) => { if (listeners.has(code) && needsRetry(statusCache.get(code)?.status)) retryPending.add(code); });
  if (!retryPending.size || retryTimer !== null) return;
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    [...retryPending].forEach((code) => {
      retryPending.delete(code);
      if (listeners.has(code) && needsRetry(statusCache.get(code)?.status)) requestStatus(code);
    });
  }, STATUS_RETRY_MS);
}

async function flush() {
  flushTimer = null;
  const codes = [...pending].slice(0, 400);
  codes.forEach((code) => pending.delete(code));
  if (!codes.length) return;
  try {
    const retrying = codes.some((code) => needsRetry(statusCache.get(code)?.status));
    const response = await fetch(`/api/stock-trading-status?tickers=${encodeURIComponent(codes.join(","))}${retrying ? "&refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json() as { rows?: StockTradingStatus[] };
    const received = new Set((payload.rows ?? []).map((row) => row.code));
    (payload.rows ?? []).forEach((row) => publish(row.code, row));
    const missing = codes.filter((code) => !received.has(code));
    missing.forEach((code) => publish(code, null));
    scheduleRetry([...missing, ...(payload.rows ?? []).filter(needsRetry).map((row) => row.code)]);
  } catch {
    codes.forEach((code) => publish(code, null));
    scheduleRetry(codes);
  }
  if (pending.size) flushTimer = window.setTimeout(() => void flush(), 25);
}

function requestStatus(code: string) {
  pending.add(code);
  if (flushTimer === null) flushTimer = window.setTimeout(() => void flush(), 25);
}

export default function StockTradingBadges({
  ticker,
  compact = false,
  detailed = false,
  dense = false,
  trailingBadge,
}: {
  ticker: string;
  compact?: boolean;
  detailed?: boolean;
  dense?: boolean;
  trailingBadge?: ReactNode;
}) {
  const code = ticker.trim().toUpperCase();
  const [status, setStatus] = useState<StockTradingStatus | null>(null);

  useEffect(() => {
    if (!isIndividualStockCode(code)) return;
    hydrateStatusCache();
    const now = Date.now();
    const cached = statusCache.get(code);
    if (cached && cached.staleUntil > now) queueMicrotask(() => setStatus(cached.status));
    else if (cached) statusCache.delete(code);
    const group = listeners.get(code) ?? new Set();
    group.add(setStatus);
    listeners.set(code, group);
    if (!cached || cached.refreshAt <= now) requestStatus(code);
    ensureRefreshTimer();
    return () => {
      const current = listeners.get(code);
      current?.delete(setStatus);
      if (current?.size === 0) listeners.delete(code);
      if (listeners.size === 0 && refreshTimer !== null) {
        refreshTimer.cancel();
        refreshTimer = null;
      }
    };
  }, [code]);

  if (!isIndividualStockCode(code)) return null;
  if (!status) return <span className={`stock-trading-badges${compact ? " compact" : ""}${dense ? " dense" : ""}`} aria-label="交易資格讀取中">{dense ? <>
    <i className="margin pending">融資查核中</i><i className="short pending">融券查核中</i><i className="day-trade pending">當沖查核中</i><i className="pending">股期查核中</i><i className="pending">小型期貨查核中</i>{trailingBadge}
  </> : <i className="pending">交易資格讀取中</i>}</span>;
  const future = futureStatusLabel(status);
  const dayTrade = status.dayTrade ?? "unknown";
  const dispositionClassName = `disposition${status.disposition === "處置中" ? " disposition-flash" : ""}`;
  if (dense) return <span className="stock-trading-badges compact dense" aria-label={`${code} 融資、融券、現股當沖與股票期貨資格`}>
    <i className={`margin ${status.margin === "available" ? "available" : status.margin === "unknown" ? "pending" : "unavailable"}`}>{status.margin === "available" ? "可融資" : status.margin === "unknown" ? "融資查核中" : "不可融資"}</i>
    <i className={`short ${status.short === "available" ? "available" : status.short === "unknown" ? "pending" : "unavailable"}`}>{status.short === "available" ? "可融券" : status.short === "unknown" ? "融券查核中" : "不可融券"}</i>
    <i className={`day-trade ${dayTrade === "available" ? "available" : dayTrade === "unknown" ? "pending" : "unavailable"}`}>{dayTrade === "available" ? "可現股當沖" : dayTrade === "unknown" ? "當沖查核中" : "不可現股當沖"}</i>
    <i className={status.futuresReady === false ? "pending" : status.stockFuture ? "future" : "unavailable"}>{status.futuresReady === false ? "股期查核中" : status.stockFuture ? "有股期" : "無股期"}</i>
    <i className={status.futuresReady === false ? "pending" : status.miniStockFuture ? "future" : "unavailable"}>{status.futuresReady === false ? "小型期貨查核中" : status.miniStockFuture ? "有小型期貨" : "無小型期貨"}</i>
    {trailingBadge}
    {status.disposition ? <i className={dispositionClassName}>{status.disposition}</i> : null}
  </span>;
  return <span className={`stock-trading-badges${compact ? " compact" : ""}`} aria-label={`${code} 交易資格`}>
    <i className={`margin ${status.margin === "available" ? "available" : status.margin === "unknown" ? "pending" : "unavailable"}`}>{status.margin === "available" ? "可融資" : status.margin === "unknown" ? "融資待補" : "無融資"}</i>
    <i className={`short ${status.short === "available" ? "available" : status.short === "unknown" ? "pending" : "unavailable"}`}>{status.short === "available" ? "可融券" : status.short === "unknown" ? "融券待補" : "無融券"}</i>
    <i className={`day-trade ${dayTrade === "available" ? "available" : dayTrade === "unknown" ? "pending" : "unavailable"}`}>{dayTrade === "available" ? "可現股當沖" : dayTrade === "unknown" ? "當沖待補" : "不可現股當沖"}</i>
    {status.disposition
      ? <i className={dispositionClassName}>{status.disposition}</i>
      : detailed && <i className="available">非處置</i>}
    {detailed ? status.futuresReady === false ? <>
      <i className="pending">股期查核中</i>
      <i className="pending">小型期貨查核中</i>
    </> : <>
      <i className={status.stockFuture ? "future" : "unavailable"}>{status.stockFuture ? "有股期" : "無股期"}</i>
      <i className={status.miniStockFuture ? "future" : "unavailable"}>{status.miniStockFuture ? "有小型期貨" : "無小型期貨"}</i>
    </> : status.futuresReady === false ? <i className="pending">期貨資格查核中</i> : future && <i className="future">{future}</i>}
  </span>;
}
