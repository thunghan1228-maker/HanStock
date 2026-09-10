import { after, NextRequest, NextResponse } from "next/server";
import { readKlineSnapshot, saveKlineSnapshot } from "../../../../db/kline-snapshots";
import { createSavedForceCache } from "../../../../lib/saved-force-cache";
import { cleanKlineSnapshot, mergeKlineSnapshot, type KlineSnapshot } from "../../../../lib/kline-snapshot";
import { fetchCachedMarket, marketCacheWindow } from "../../../../lib/market-fetch-cache";
import { latestTradingDateLabel, taipeiDateLabel } from "../../../../lib/kline-daily";
import { hasIncompleteIntradaySession } from "../../../../lib/kline-history";
import { replayKlineSignals, mergeKlineSignals, type KlineReplaySignal } from "../../../../lib/kline-signals";
import { hasSparseMinuteVolume, hubLotsToShares, preferCompleteCumulativeVolume, sumMinuteVolumesToShares } from "../../../../lib/kline-volume";
import { successfulSourcesInCompletionOrder } from "../../../../lib/successful-source-stream";

const HANSTOCK_ORIGIN = "https://www.hanstock.xyz";
const HANSTOCK_HUB_ORIGINS = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];
const YAHOO_CHART_ORIGIN = "https://query2.finance.yahoo.com";

type RepairTarget = { ticker: string; interval: "1m" | "5m" | "1d"; responseIndex: number | null };
type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  [key: string]: unknown;
};
type HubRepair = {
  bars?: Record<string, unknown>[];
  bootstrap?: {
    history_ok?: unknown;
    auto_repair?: { waiting?: unknown };
    [key: string]: unknown;
  };
  fallback?: { source?: unknown; history_ok?: unknown; [key: string]: unknown };
  [key: string]: unknown;
};
type TrpcJson = { candles?: Candle[]; autoRepair?: Record<string, unknown>; [key: string]: unknown };
type TrpcEnvelope = { result?: { data?: { json?: TrpcJson } } };
type YahooChartPayload = {
  chart?: {
    result?: Array<{
      timestamp?: unknown[];
      indicators?: {
        quote?: Array<{
          open?: unknown[];
          high?: unknown[];
          low?: unknown[];
          close?: unknown[];
          volume?: unknown[];
        }>;
      };
    }>;
  };
};

const KLINE_REPAIR_BUDGET_MS = 80;
const KLINE_REPAIR_CACHE_MS = 10_000;
const repairCache = new Map<string, { expiresAt: number; promise: Promise<HubRepair | null> }>();
const signalReplayCache = new Map<string, { expiresAt: number; promise: Promise<KlineReplaySignal[]> }>();

async function loadHubRepairFromSources(path: string, ticker: string, timeoutMs: number, userAgent: string) {
  let empty: HubRepair | null = null;
  const sources = successfulSourcesInCompletionOrder(HANSTOCK_HUB_ORIGINS.map((origin) => async (signal) => {
    const response = await fetchCachedMarket(`${origin}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": userAgent },
      signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]),
    }, { ticker });
    if (!response.ok) throw new Error(`hub-repair-http-${response.status}`);
    return await response.json() as HubRepair;
  }));
  for await (const payload of sources) {
    // An empty fast backup must not hide a slower source with actual candles.
    // Returning also aborts the outstanding peer; repair/cache budgets stay unchanged.
    if (Array.isArray(payload?.bars) && payload.bars.length > 0) return payload;
    if (payload && typeof payload === "object") empty ??= payload;
  }
  return empty;
}

function repairTarget(request: NextRequest, path: string[]): RepairTarget | null {
  if (request.method !== "GET") return null;
  try {
    const operations = path.join("/").split(",");
    const operationIndex = operations.indexOf("stocks.candles");
    if (operationIndex < 0) return null;
    const raw = JSON.parse(request.nextUrl.searchParams.get("input") ?? "{}");
    const batched = request.nextUrl.searchParams.get("batch") === "1";
    const source = batched ? raw?.[String(operationIndex)] : raw;
    const input = source?.json ?? source;
    const ticker = String(input?.ticker ?? "").trim().toUpperCase();
    const interval = String(input?.interval ?? "");
    if (!/^[0-9A-Z]{2,12}$/.test(ticker) || !["1m", "5m", "1d"].includes(interval)) return null;
    return { ticker, interval: interval as RepairTarget["interval"], responseIndex: batched ? operationIndex : null };
  } catch {
    return null;
  }
}

type SignalRepairTarget = { ticker: string; responseIndex: number | null; sinceTs?: number; date?: string };

function signalRepairTargets(request: NextRequest, path: string[]): SignalRepairTarget[] {
  if (request.method !== "GET") return [];
  try {
    const operations = path.join("/").split(",");
    const raw = JSON.parse(request.nextUrl.searchParams.get("input") ?? "{}");
    const batched = request.nextUrl.searchParams.get("batch") === "1";
    return operations.flatMap((operation, index) => {
      if (operation !== "schedule.intradaySignalsByTicker") return [];
      const source = batched ? raw?.[String(index)] : raw;
      const input = source?.json ?? source;
      const ticker = String(input?.ticker ?? "").trim().toUpperCase();
      if (!/^\d{4,6}[A-Z]?$/.test(ticker)) return [];
      const sinceTs = Number(input.sinceTs);
      const date = /^\d{4}-\d{2}-\d{2}$/.test(String(input.date ?? "")) ? String(input.date) : undefined;
      return [{ ticker, responseIndex: batched ? index : null, sinceTs: Number.isFinite(sinceTs) && sinceTs > 0 ? sinceTs : undefined, date }];
    });
  } catch { return []; }
}

function taipeiLabel(value: unknown) {
  const ts = Number(value);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(ts));
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("month")}/${get("day")} ${get("hour")}:${get("minute")}`;
}

function repairCandle(raw: Record<string, unknown>, interval: RepairTarget["interval"]): Candle | null {
  const date = interval === "1d" ? String(raw.date ?? taipeiDateLabel(raw.ts)) : taipeiLabel(raw.ts);
  const open = Number(raw.open);
  const high = Number(raw.high);
  const low = Number(raw.low);
  const close = Number(raw.close);
  const volume = Number(raw.volume ?? 0);
  if (!date || ![open, high, low, close, volume].every(Number.isFinite) || Math.min(open, high, low, close) <= 0) return null;
  return {
    date, ts: Number(raw.ts), open, high, low, close, volume,
    buyVolume: Number(raw.buy_volume ?? 0),
    sellVolume: Number(raw.sell_volume ?? 0),
    neutralVolume: Number(raw.neutral_volume ?? 0),
    mainBuyVolume: Number(raw.main_buy_volume ?? 0),
    mainSellVolume: Number(raw.main_sell_volume ?? 0),
    mainNetVolume: Number(raw.main_net_volume ?? 0),
    mainBuyAmount: Number(raw.main_buy_amount ?? 0),
    mainSellAmount: Number(raw.main_sell_amount ?? 0),
    mainNetAmount: Number(raw.main_net_amount ?? 0),
    mainTickCount: Number(raw.main_tick_count ?? 0),
    mainForceAvailable: Boolean(raw.main_force_available),
  };
}

function aggregateLatestDailyBar(bars: Record<string, unknown>[], volumeUnit: "lots" | "shares") {
  // 日 K 必须采用行情资料里的最新交易日，不能限定为今天。周末或休市日打开时，
  // “今天”没有分钟线，但仍要把前一个正式交易日补进历史日线。
  const latestTradingDate = latestTradingDateLabel(bars);
  const current = bars
    .filter((bar) => taipeiDateLabel(bar.ts) === latestTradingDate)
    .sort((a, b) => Number(a.ts) - Number(b.ts));
  if (current.length === 0) return [] as Record<string, unknown>[];

  const first = current[0];
  const last = current[current.length - 1];
  const sum = (field: string) => current.reduce((total, bar) => total + Number(bar[field] ?? 0), 0);
  const sumVolume = (field: string) => sumMinuteVolumesToShares(current.map((bar) => bar[field]), volumeUnit);
  const prices = current.flatMap((bar) => [Number(bar.high), Number(bar.low)]).filter(Number.isFinite);
  return [{
    date: latestTradingDate,
    ts: Number(last.ts),
    open: Number(first.open),
    high: Math.max(...prices),
    low: Math.min(...prices),
    close: Number(last.close),
    // Hub 分鐘量是「張」、Yahoo 分鐘量已经是「股」；日 K 一律输出为股。
    volume: sumVolume("volume"),
    buy_volume: sumVolume("buy_volume"),
    sell_volume: sumVolume("sell_volume"),
    neutral_volume: sumVolume("neutral_volume"),
    main_buy_volume: sumVolume("main_buy_volume"),
    main_sell_volume: sumVolume("main_sell_volume"),
    main_net_volume: sumVolume("main_net_volume"),
    main_buy_amount: sum("main_buy_amount"),
    main_sell_amount: sum("main_sell_amount"),
    main_net_amount: sum("main_net_amount"),
    main_tick_count: sum("main_tick_count"),
    main_force_available: current.some((bar) => Boolean(bar.main_force_available)),
  }];
}

function withMovingAverages(candles: Candle[]) {
  return candles.map((candle, index) => {
    const average = (period: number) => {
      if (index + 1 < period) return null;
      const total = candles.slice(index + 1 - period, index + 1).reduce((sum, item) => sum + item.close, 0);
      return Math.round(total / period * 100) / 100;
    };
    return { ...candle, ma5: average(5), ma20: average(20) };
  });
}

function yahooMinuteBars(payload: YahooChartPayload) {
  const result = payload?.chart?.result?.[0];
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const quote = result?.indicators?.quote?.[0];
  if (!quote) return [] as Record<string, unknown>[];

  return timestamps.flatMap((seconds: unknown, index: number) => {
    const ts = Number(seconds) * 1_000;
    const priceValues = [quote.open?.[index], quote.high?.[index], quote.low?.[index], quote.close?.[index]];
    if (!priceValues.every((value) => typeof value === "number" && Number.isFinite(value))) return [];
    const [open, high, low, close] = priceValues as number[];
    const volume = Number(quote.volume?.[index] ?? 0);
    if (!Number.isFinite(ts) || !Number.isFinite(volume)) return [];

    const taipei = new Date(ts + 8 * 60 * 60 * 1_000);
    const minute = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
    if (minute < 9 * 60 || minute > 13 * 60 + 30) return [];
    return [{ ts, open, high, low, close, volume, main_force_available: false }];
  });
}

function yahooDailyBars(payload: YahooChartPayload) {
  const result = payload?.chart?.result?.[0];
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const quote = result?.indicators?.quote?.[0];
  if (!quote) return [] as Record<string, unknown>[];
  const today = taipeiDateLabel(Date.now());
  const byDate = new Map<string, Record<string, unknown>>();
  for (let index = 0; index < timestamps.length; index += 1) {
    const ts = Number(timestamps[index]) * 1_000;
    const date = taipeiDateLabel(ts);
    const open = Number(quote.open?.[index]);
    const high = Number(quote.high?.[index]);
    const low = Number(quote.low?.[index]);
    const close = Number(quote.close?.[index]);
    const volume = Number(quote.volume?.[index] ?? 0);
    if (!date || date > today || ![ts, open, high, low, close, volume].every(Number.isFinite) || Math.min(open, high, low, close) <= 0) continue;
    byDate.set(date, { date, ts, open, high, low, close, volume, main_force_available: false });
  }
  return [...byDate.values()].sort((left, right) => Number(left.ts) - Number(right.ts));
}

async function fetchYahooDailyHistory(target: RepairTarget) {
  if (!/^\d{4,6}[A-Z]?$/.test(target.ticker)) return [] as Record<string, unknown>[];
  const symbols = [`${target.ticker}.TW`, `${target.ticker}.TWO`];
  const candidates = await Promise.all(symbols.map(async (symbol) => {
    try {
      const endpoint = new URL(`/v8/finance/chart/${encodeURIComponent(symbol)}`, YAHOO_CHART_ORIGIN);
      endpoint.searchParams.set("range", "2y");
      endpoint.searchParams.set("interval", "1d");
      endpoint.searchParams.set("includePrePost", "false");
      const response = await fetch(endpoint, {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Daily-History/1.0" },
        signal: AbortSignal.timeout(15_000),
      });
      return response.ok ? yahooDailyBars(await response.json()) : [];
    } catch {
      return [] as Record<string, unknown>[];
    }
  }));
  return candidates.sort((left, right) => right.length - left.length)[0] ?? [];
}

function aggregateYahooFiveMinuteBars(oneMinuteBars: Record<string, unknown>[]) {
  const buckets = new Map<number, Record<string, unknown>>();
  for (const bar of oneMinuteBars) {
    const ts = Number(bar.ts);
    const taipei = new Date(ts + 8 * 60 * 60 * 1_000);
    const minuteOfDay = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
    const bucketMinute = minuteOfDay === 13 * 60 + 30 ? 13 * 60 + 25 : Math.floor(minuteOfDay / 5) * 5;
    const bucketTs = ts - (minuteOfDay - bucketMinute) * 60 * 1_000 - taipei.getUTCSeconds() * 1_000 - taipei.getUTCMilliseconds();
    const previous = buckets.get(bucketTs);
    if (!previous) {
      buckets.set(bucketTs, { ...bar, ts: bucketTs });
      continue;
    }
    previous.high = Math.max(Number(previous.high), Number(bar.high));
    previous.low = Math.min(Number(previous.low), Number(bar.low));
    previous.close = Number(bar.close);
    previous.volume = Number(previous.volume ?? 0) + Number(bar.volume ?? 0);
  }
  return [...buckets.values()].sort((a, b) => Number(a.ts) - Number(b.ts));
}

async function fetchYahooRepair(target: RepairTarget) {
  if (!/^\d{4,6}[A-Z]?$/.test(target.ticker)) return [] as Record<string, unknown>[];
  const fetchSymbol = async (symbol: string) => {
    try {
      const endpoint = new URL(`/v8/finance/chart/${encodeURIComponent(symbol)}`, YAHOO_CHART_ORIGIN);
      endpoint.searchParams.set("range", "5d");
      endpoint.searchParams.set("interval", "1m");
      endpoint.searchParams.set("includePrePost", "false");
      const response = await fetch(endpoint, {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Kline-Repair/1.0" },
        signal: AbortSignal.timeout(12_000),
      });
      if (!response.ok) return [] as Record<string, unknown>[];
      return yahooMinuteBars(await response.json());
    } catch {
      return [] as Record<string, unknown>[];
    }
  };

  const symbols = [`${target.ticker}.TW`, `${target.ticker}.TWO`];
  const candidates = await Promise.all(symbols.map(fetchSymbol));
  const oneMinuteBars = candidates.sort((a, b) => b.length - a.length)[0] ?? [];
  return target.interval === "5m" ? aggregateYahooFiveMinuteBars(oneMinuteBars) : oneMinuteBars;
}

async function fetchRepair(target: RepairTarget) {
  const yahooDailyHistoryPromise = target.interval === "1d"
    ? fetchYahooDailyHistory(target)
    : Promise.resolve([] as Record<string, unknown>[]);
  let hub: HubRepair | null = null;
  try {
    const endpoint = target.interval === "5m" ? "bars" : "bars1m";
    const query = target.interval === "1d" ? "?days=1&limit=600&backfill=false" : "";
    const hubPath = `/api/hub/${endpoint}/${encodeURIComponent(target.ticker)}${query}`;
    hub = await loadHubRepairFromSources(
      hubPath, target.ticker, target.interval === "1d" ? 15_000 : 5_000, "HanStock-Battle-Kline-Repair/1.0",
    );
  } catch {}

  const hubBars = Array.isArray(hub?.bars) ? hub.bars as Record<string, unknown>[] : [];
  if (target.interval === "1d") {
    const hubDaily = aggregateLatestDailyBar(hubBars, "lots");
    const yahooDaily = await yahooDailyHistoryPromise;
    if (yahooDaily.length === 0 && hubDaily.length === 0) return hub;
    const byDate = new Map(yahooDaily.map((bar) => [String(bar.date), bar]));
    for (const bar of hubDaily) byDate.set(String(bar.date), { ...byDate.get(String(bar.date)), ...bar });
    const bars = [...byDate.values()].sort((left, right) => String(left.date).localeCompare(String(right.date)));
    return {
      ...(hub ?? { status: "ok", code: target.ticker, interval: target.interval }),
      bars,
      bootstrap: {
        ...(hub?.bootstrap ?? {}),
        history_ok: yahooDaily.length >= 60,
        auto_repair: { ...(hub?.bootstrap?.auto_repair ?? {}), waiting: yahooDaily.length < 60 },
      },
      fallback: { source: "yahoo", history_ok: yahooDaily.length >= 60, bar_count: bars.length },
    };
  }
  // Hub 分鐘量的單位是「張」，原始 K 線與 Yahoo 的單位是「股」。先統一成股，
  // 否則今天的成交量會被畫成正確值的 1/1000，在四格圖上看起來像完全消失。
  const indexedMinutes = new Map<number, Record<string, unknown>>();
  for (const bar of hubBars) {
    const normalized: Record<string, unknown> = { ...indexedMinutes.get(Number(bar.ts)), ...bar, volume: hubLotsToShares(bar.volume) };
    for (const field of ["buy_volume", "sell_volume", "neutral_volume", "main_buy_volume", "main_sell_volume", "main_net_volume"]) {
      if (bar[field] != null && Number.isFinite(Number(bar[field]))) normalized[field] = Number(bar[field]) * 1_000;
    }
    indexedMinutes.set(Number(bar.ts), normalized);
  }
  const normalizedHubBars = [...indexedMinutes.values()].sort((left, right) => Number(left.ts) - Number(right.ts));
  const latestSession = latestTradingDateLabel(normalizedHubBars);
  const latestHubVolumes = normalizedHubBars
    .filter((bar) => taipeiDateLabel(bar.ts) === latestSession)
    .map((bar) => bar.volume);
  const needsFallback = hasIncompleteIntradaySession(normalizedHubBars, target.interval)
    || !hub?.bootstrap?.history_ok
    || Boolean(hub?.bootstrap?.auto_repair?.waiting)
    || hasSparseMinuteVolume(latestHubVolumes);
  if (!needsFallback) return hub ? { ...hub, bars: normalizedHubBars } : hub;

  const yahooBars = await fetchYahooRepair(target);
  if (yahooBars.length === 0) return hub ? { ...hub, bars: normalizedHubBars } : hub;
  const byTimestamp = new Map<number, Record<string, unknown>>();
  for (const bar of yahooBars) byTimestamp.set(Number(bar.ts), bar);
  // Hub bars win on duplicate timestamps for OHLC/main-force fields; cumulative volume keeps
  // the most complete value, so a partial live bucket can never erase a finished Yahoo bucket.
  for (const bar of normalizedHubBars) {
    const fallback = byTimestamp.get(Number(bar.ts));
    byTimestamp.set(Number(bar.ts), {
      ...fallback,
      ...bar,
      volume: preferCompleteCumulativeVolume(fallback?.volume, bar.volume),
    });
  }
  const bars = [...byTimestamp.values()].filter((bar) => Number.isFinite(Number(bar.ts))).sort((a, b) => Number(a.ts) - Number(b.ts));
  return {
    ...(hub ?? { status: "ok", code: target.ticker, interval: target.interval, bootstrap: {} }),
    bar_count: bars.length,
    bars,
    fallback: { source: "yahoo", history_ok: !hasIncompleteIntradaySession(bars, target.interval), bar_count: yahooBars.length },
  };
}

function loadCachedRepair(target: RepairTarget) {
  const window = marketCacheWindow({ticker: target.ticker, activeMs: KLINE_REPAIR_CACHE_MS});
  const key = `${target.ticker}:${target.interval}:${window.phase}`;
  const cached = repairCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;

  const entry = { expiresAt: Number.POSITIVE_INFINITY, promise: Promise.resolve<HubRepair | null>(null) };
  const promise = fetchRepair(target).catch(() => null).then((repair) => {
    // Keep in-flight work deduplicated; count the TTL from completion, not start.
    entry.expiresAt = repair?.bars?.length ? Date.now() + window.ttl : 0;
    return repair;
  });
  entry.promise = promise;
  if (repairCache.size >= 64) {
    const oldest = repairCache.keys().next().value;
    if (oldest) repairCache.delete(oldest);
  }
  repairCache.set(key, entry);
  return promise;
}

function repairWithinInitialBudget(promise: Promise<HubRepair | null>, budgetMs = KLINE_REPAIR_BUDGET_MS) {
  return Promise.race([
    promise.then((repair) => ({ ready: true as const, repair })),
    new Promise<{ ready: false; repair: null }>((resolve) => {
      setTimeout(() => resolve({ ready: false, repair: null }), budgetMs);
    }),
  ]);
}

function candleSortValue(date: string) {
  const daily = date.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (daily) return Date.UTC(Number(daily[1]), Number(daily[2]) - 1, Number(daily[3]));
  const match = date.match(/^(\d{2})\/(\d{2}) (\d{2}):(\d{2})$/);
  if (!match) return Number.MAX_SAFE_INTEGER;
  return Date.UTC(new Date().getUTCFullYear(), Number(match[1]) - 1, Number(match[2]), Number(match[3]), Number(match[4]));
}

function mergeRepair(payload: unknown, repair: HubRepair | null, responseIndex: number | null, interval: RepairTarget["interval"]) {
  const envelope = responseIndex === null
    ? payload as TrpcEnvelope
    : Array.isArray(payload) ? payload[responseIndex] as TrpcEnvelope | undefined : undefined;
  // A valid independent candle source can recover an upstream tRPC error.
  if (envelope && !envelope.result?.data?.json && repair?.bars?.length) {
    delete (envelope as Record<string, unknown>).error;
    envelope.result = {data: {json: {ticker: repair.code, name: repair.name, interval, candles: []}}};
  }
  const json = envelope?.result?.data?.json;
  if (json && Array.isArray(json.candles)) json.candles = cleanKlineSnapshot({ candles: json.candles }, interval).candles as Candle[];
  const existing = Array.isArray(json?.candles) ? json.candles.filter((item: unknown) => item && typeof item === "object") as Candle[] : [];
  const repaired = Array.isArray(repair?.bars)
    ? repair.bars.map((bar: Record<string, unknown>) => repairCandle(bar, interval)).filter(Boolean) as Candle[]
    : [];
  const fallbackSource = String(repair?.fallback?.source ?? "");
  const fallbackUsed = Boolean(fallbackSource);
  const incomplete = interval !== "1d" && hasIncompleteIntradaySession(repaired, interval);
  const waiting = incomplete || (Boolean(repair?.bootstrap?.auto_repair?.waiting) && !fallbackUsed);
  if (!json || repaired.length === 0) return { payload, merged: false, waiting, source: "original" };

  const byDate = new Map(existing.map((item) => [item.date, item]));
  for (const item of repaired) {
    const current = byDate.get(item.date);
    const merged: Candle = {
      ...current,
      ...item,
      volume: preferCompleteCumulativeVolume(current?.volume, item.volume),
    };
    // Price-only fallback must not erase real main-force observations.
    if (current?.mainForceAvailable && !item.mainForceAvailable) {
      for (const key of ["buyVolume", "sellVolume", "neutralVolume", "mainBuyVolume", "mainSellVolume", "mainNetVolume", "mainBuyAmount", "mainSellAmount", "mainNetAmount", "mainTickCount", "mainForceAvailable"]) {
        if (key in current) merged[key] = current[key];
      }
    }
    byDate.set(item.date, merged);
  }
  const candles = [...byDate.values()].sort((a, b) => candleSortValue(a.date) - candleSortValue(b.date));
  json.candles = withMovingAverages(candles);
  json.autoRepair = {
    enabled: true,
    historyOk: !waiting && Boolean(repair?.bootstrap?.history_ok || repair?.fallback?.history_ok),
    waiting,
    repairedBars: repaired.length,
    source: fallbackUsed ? `hub+${fallbackSource}` : "hub",
  };
  return { payload, merged: candles.length > existing.length, waiting, source: String(json.autoRepair.source) };
}

function loadCachedSignalReplay(ticker: string) {
  const cached = signalReplayCache.get(ticker);
  if (cached && cached.expiresAt > Date.now()) return cached.promise;
  const entry = { expiresAt: Infinity, promise: Promise.resolve<KlineReplaySignal[]>([]) };
  entry.promise = (async () => {
    const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval: "5m" } }));
    const [original, repair] = await Promise.all([
      fetch(`${HANSTOCK_ORIGIN}/api/trpc/stocks.candles?input=${input}`, {
        cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-Kline-Signal-Replay/1.0" }, signal: AbortSignal.timeout(12_000),
      }).then(async response => response.ok ? await response.json() : null).catch(() => null),
      loadCachedRepair({ ticker, interval: "5m", responseIndex: null }),
    ]);
    const envelope = original?.result?.data?.json?.candles ? original : { result: { data: { json: { candles: [] } } } };
    const merged = mergeRepair(envelope, repair, null, "5m");
    const candles = (merged.payload as TrpcEnvelope).result?.data?.json?.candles ?? [];
    return replayKlineSignals(ticker, candles);
  })().catch(() => []).then(signals => {
    entry.expiresAt = Date.now() + (signals.length ? KLINE_REPAIR_CACHE_MS : 2_000);
    return signals;
  });
  if (signalReplayCache.size >= 64) signalReplayCache.delete(signalReplayCache.keys().next().value!);
  signalReplayCache.set(ticker, entry);
  return entry.promise;
}

function mergeSignalRepair(payload: unknown, target: SignalRepairTarget, replay: KlineReplaySignal[]) {
  const envelope = target.responseIndex === null ? payload : Array.isArray(payload) ? payload[target.responseIndex] : null;
  const data = (envelope as { result?: { data?: { json?: unknown } } } | null)?.result?.data;
  if (!data || !Array.isArray(data.json)) return 0;
  const stored = data.json as Record<string, unknown>[];
  const date = target.date ?? (target.sinceTs ? undefined : taipeiDateLabel(Date.now()).replaceAll("/", "-"));
  const rows = mergeKlineSignals(stored, replay, target.ticker, target.sinceTs, date);
  const keys = new Set(stored.map(row => `${row.ticker}|${row.kind}|${row.barTs}`));
  data.json = rows;
  return rows.filter(row => !keys.has(`${row.ticker}|${row.kind}|${row.barTs}`)).length;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!Array.isArray(path) || path.length === 0 || path.some((part) => !/^[A-Za-z0-9_.,-]+$/.test(part))) {
    return NextResponse.json({ error: "invalid path" }, { status: 400 });
  }

  const target = new URL(`/api/trpc/${path.join("/")}`, HANSTOCK_ORIGIN);
  target.search = request.nextUrl.search;
  const targetRepair = repairTarget(request, path);
  const repairPromise = targetRepair ? loadCachedRepair(targetRepair) : Promise.resolve(null);
  const tickerSignalTargets = signalRepairTargets(request, path);
  const tickerSignalPromises = tickerSignalTargets.map(target => loadCachedSignalReplay(target.ticker));
  const headers = new Headers();
  for (const key of ["accept", "content-type"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  headers.set("user-agent", "HanStock-Battle/1.0");

  try {
    // Only the public candle GET is shared. Mixed tRPC batches, account calls,
    // and every POST retain their original forwarding behavior.
    const fetchUpstream = request.method === "GET" && path.join("/") === "stocks.candles" && targetRepair
      ? (url: URL, init: RequestInit) => fetchCachedMarket(url.toString(), init, {ticker: targetRepair.ticker})
      : fetch;
    const upstream = await fetchUpstream(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    const responseHeaders = new Headers();
    responseHeaders.set("Content-Type", upstream.headers.get("content-type") ?? "application/json; charset=utf-8");
    responseHeaders.set(
      "Cache-Control",
      targetRepair && request.method === "GET"
        ? "public, max-age=3, s-maxage=8, stale-while-revalidate=30"
        : upstream.headers.get("cache-control") ?? "no-store",
    );
    if (targetRepair || tickerSignalTargets.length && upstream.ok) {
      const upstreamBody = await upstream.text();
      try {
        const payload = JSON.parse(upstreamBody);
        if (tickerSignalTargets.length) {
          const replayed = await Promise.all(tickerSignalPromises);
          let added = 0;
          tickerSignalTargets.forEach((target, index) => { added += mergeSignalRepair(payload, target, replayed[index]); });
          responseHeaders.set("Cache-Control", "no-store");
          responseHeaders.set("X-HanStock-Kline-Signals-Repaired", String(added));
          responseHeaders.set("X-HanStock-Kline-Signal-Source", "stored+chart-replay");
        }
        if (!targetRepair) return NextResponse.json(payload, { status: upstream.status, headers: responseHeaders });
        // Missing intraday bars are required chart data. Await the bounded source
        // requests in this response: a detached 80ms race can be cancelled after
        // a Worker responds, and the chart may never refetch after market close.
        const repairResult = targetRepair.interval !== "1d"
          ? { ready: true as const, repair: await repairPromise }
          : await repairWithinInitialBudget(repairPromise, 5_000);
        if (!repairResult.ready) {
          responseHeaders.set("X-HanStock-Kline-Auto-Repair", "deferred");
          responseHeaders.set("X-HanStock-Kline-Repair-Source", "original-fast-path");
          return NextResponse.json(payload, { status: upstream.status, headers: responseHeaders });
        }
        const merged = mergeRepair(payload, repairResult.repair, targetRepair.responseIndex, targetRepair.interval);
        if (merged.waiting) responseHeaders.set("Cache-Control", "no-store");
        responseHeaders.set("X-HanStock-Kline-Auto-Repair", merged.waiting ? "waiting" : merged.merged ? "repaired" : "checked");
        responseHeaders.set("X-HanStock-Kline-Repair-Source", merged.source);
        return NextResponse.json(merged.payload, { status: merged.merged ? 200 : upstream.status, headers: responseHeaders });
      } catch {
        // 回補服務短暫不可用時仍回傳原始 K 線，不讓圖表整體失敗。
        return new NextResponse(upstreamBody, { status: upstream.status, headers: responseHeaders });
      }
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    if (targetRepair && path.join("/") === "stocks.candles") {
      const repair = await repairPromise;
      if (repair?.bars?.length) {
        const base = {result: {data: {json: {ticker: targetRepair.ticker, interval: targetRepair.interval, candles: []}}}};
        const payload = targetRepair.responseIndex === null ? base : [base];
        const recovered = mergeRepair(payload, repair, targetRepair.responseIndex, targetRepair.interval);
        if (recovered.merged) return NextResponse.json(recovered.payload, {headers: {"Cache-Control": "no-store", "X-HanStock-Kline-Repair-Source": recovered.source}});
      }
    }

    return NextResponse.json({ error: "upstream unavailable" }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}

const completedCandles = createSavedForceCache<KlineSnapshot | null>({ empty: () => null, weight: value => value?.candles.length ?? 0 });

export const GET = async (request: NextRequest, context: { params: Promise<{ path: string[] }> }) => {
  const { path } = await context.params;
  const target = repairTarget(request, path);
  if (!target || path.join("/") !== "stocks.candles" || request.headers.has("authorization")) return proxy(request, context);
  const policy = marketCacheWindow({ ticker: target.ticker, activeMs: target.interval === "1d" ? 60_000 : 5_000 });
  let fallback: Response | undefined;
  try {
    const snapshot = await completedCandles(`${target.ticker}:${target.interval}`, policy, async () =>
      await readKlineSnapshot(target.ticker, target.interval) ?? { value: null, available: false, refreshedAt: 0, phase: "" },
    async previous => {
      const response = await proxy(request, context);
      fallback = response.clone();
      const payload = await response.json();
      const envelope = target.responseIndex === null ? payload : payload[target.responseIndex];
      const data = envelope?.result?.data?.json;
      if (!response.ok || !Array.isArray(data?.candles) || !data.candles.length) throw Error("kline_source_unavailable");
      const value = mergeKlineSnapshot(previous, data, target.interval), refreshedAt = Date.now();
      const phase = data.autoRepair?.waiting || data.autoRepair?.historyOk === false ? `${policy.phase}:partial` : policy.phase;
      await saveKlineSnapshot(target.ticker, target.interval, value, refreshedAt, phase).catch(() => undefined);
      return { value, available: value.candles.length > 0, refreshedAt, phase };
    }, task => after(task));
    const envelope = { result: { data: { json: snapshot.value ? cleanKlineSnapshot(snapshot.value, target.interval) : snapshot.value } } };
    return NextResponse.json(target.responseIndex === null ? envelope : [envelope], { headers: {
      "Cache-Control": "public, max-age=3, stale-while-revalidate=10", "X-HanStock-Kline-Source": "saved-snapshot-first",
    } });
  } catch { return fallback ?? proxy(request, context); }
};
export const POST = proxy;
