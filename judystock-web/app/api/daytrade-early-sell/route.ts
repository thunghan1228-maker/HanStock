import { loadConfiguredGroups, loadSharedLatestQuotes } from "../../../lib/live-group-quotes";
import { timedKeyedSingleFlight } from "../../../lib/timed-single-flight";
import { successfulSourcesInCompletionOrder } from "../../../lib/successful-source-stream";
import { isAfterHoursTradeSignal } from "../../../lib/after-hours-trades";
import { NextRequest, NextResponse } from "next/server";
import { fetchBoundedMarketJson, mapMarketBatches } from "../../../lib/bounded-market-data";
import {
  readEarlySellDates,
  readEarlySellSignalSummary,
  readEarlySellSignals,
  deleteEarlySellSignalsForTickers,
  deleteEarlySellSignalsForKindTickers,
  appendEarlySellChipRankAnnotations,
  saveEarlySellSignals,
  type EarlySellSignalRecord,
} from "@/db/early-sell-history";
import {
  calculateFourGateSignals,
  fourGateCandidateTickers,
  passesPreviousEstimatedSellPressureFilter,
  passesTodayImmediatePressureRatioFilter,
  type FourGateMinuteBar,
  type FourGateSourceSignal,
} from "../../../lib/four-gate-signals";
import {
  calculateIntradayExtraLargeSellSignals,
  calculateIntradayExtraLargeBuySignals,
  extraLargeBuyCandidateTickers,
  extraLargeBuyPreviousNetAmount,
  extraLargeSellCandidateTickers,
  extraLargeSellNetFundingRate,
  extraLargeSellPreviousNetAmount,
  mergePreviousLargeNetBaselines,
  hasQualifiedExtraLargeTriggerForce,
  qualifyExtraLargeSignalByTriggerBars,
  INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE,
  INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT,
  type PreviousLargeNetBaseline,
} from "../../../lib/intraday-extra-large-sell";
import { readPreviousDaytradeFlow } from "../../../db/daytrade-flow";
import { readFullMarketLargeFlowBaselines, saveFullMarketLargeFlowBaselines } from "../../../db/full-market-large-flow";
import { readIntradayForceForTickers } from "../../../db/force-history";
import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";
import {
  calculateMainForceZeroSignals,
  hasPotentialMainForceABCDSignal,
  MAIN_FORCE_FILTER_MARKER,
  type MainForceHistoricalVolumes,
  type MainForceMinuteBar,
} from "../../../lib/main-force-zero-signals";
import {
  annotateMainForceGroupRanks,
  hasCapturedOrLegacyInstantLargeGroup,
  hasCapturedMatchingTopGroup,
  hasMatchingTopGroup,
  type MainForceGroupRankings,
} from "../../../lib/main-force-group-ranks";
import stockGroupsSource from "../../../data/stock_groups.py?raw";
import { loadTwseClosedTradingDates, resolveIntradaySignalDisplayDate, resolveIntradaySignalCutoverDate } from "../../../lib/intraday-signal-session";
import { readLatestMarketRanking } from "../../../db/market-ranking-snapshot";
import { readBattleSettings } from "../../../db/admin";
import { DEFAULT_BATTLE_SETTINGS } from "../../../lib/battle-settings";
import {
  calculateFourGateSourceSignalsFromMinuteBars,
  calculateImmediateSignalsFromMinuteBars,
  IMMEDIATE_SIGNAL_MIN_PREVIOUS_PRESSURE,
} from "../../../lib/live-immediate-signals";
import {
  annotateMainForceChipRanks,
  buildPreviousSessionChipTopRanks,
  normalizeChipRankingDate,
  type MainForceChipRank,
  type MainForceChipRankings,
  type MarketRankingChipSnapshot,
} from "../../../lib/main-force-chip-ranks";
import { normalizeInstantLargeOrderSignal } from "../../../lib/instant-large-thresholds.mjs";
import {
  calculateIntradayLargeForceValue,
  calculateIntradayLargeForceSignals,
  hasQualifiedInstantLargeTriggerForce,
  qualifyInstantLargeSignalByTriggerForce,
  type IntradayLargeForceMinuteBar,
} from "../../../lib/intraday-large-force";
import {
  calculateFiveMinutePatternSignals,
  VERIFIED_905_SOURCE_MARKER,
  type FiveMinutePatternHistoryContext,
  type FiveMinutePatternMinuteBar,
} from "../../../lib/five-minute-pattern-signals";
import {
  acquireIntradayLargeForceScanBatch,
  failIntradayLargeForceScanBatch,
  finishIntradayLargeForceScanBatch,
  readIntradayLargeForceScanProgress,
  saveIntradayLargeForceMonitorRows,
} from "../../../db/intraday-large-force-scan";

import { isActiveIntradayCenterSignal } from "../../../lib/intraday-center-signals";

const HUB_BASES = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];
const SIGNAL_WINDOW_START = "09:00";
const SIGNAL_WINDOW_END = "13:30";
const SIGNAL_GRANULARITY = "1m";
const MAIN_FORCE_BATCH_SIZE = 40;
const MAIN_FORCE_FALLBACK_SIZE = 32;
const LARGE_FORCE_BATCH_SIZE = 16;
const SIGNAL_GROUP_RANK_LIMIT = 10;
const INSTANT_LARGE_GROUP_RANK_LIMIT = 20;
const GENERAL_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = [
  "daytradeEarlySell50",
  "daytradeEarlyBuy50",
  "fiveMinuteTwelveShort",
  "fiveMinuteOnePlusTwoLong",
];
const FOUR_GATE_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = ["fourGateBullish", "fourGateBearish"];
const EXTRA_LARGE_SELL_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = ["intradayExtraLargeSell"];
const EXTRA_LARGE_BUY_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = ["intradayExtraLargeBuy"];
const LARGE_FORCE_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = ["intradayLargeForceBuy", "intradayLargeForceSell"];
const INSTANT_LARGE_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = ["instantLargeBuy", "instantLargeSell"];
const MAIN_FORCE_SIGNAL_KINDS: EarlySellSignalRecord["kind"][] = [
  "mainForceTurnBullish",
  "mainForceStrongBullish",
  "mainForceTurnBearish",
  "mainForceStrongBearish",
];
let storedSnapshotCache = new Map<string, { expiresAt: number; payload: Awaited<ReturnType<typeof storedPayload>> }>();
let instantLargeCollectorCache: { expiresAt: number; payload: unknown } | null = null;
type MarketStock = { ticker: string; name: string; exchange: "twse" | "tpex" | "" };
type StockGroupMap = Record<string, Array<[string, string]>>;
const marketStocks = [...new Map((marketRankingSnapshot.rows as Array<{ code?: string; name?: string; exchange?: string }>).flatMap((row) =>
  row.code && row.name ? [[row.code.toUpperCase(), {
    ticker: row.code.toUpperCase(),
    name: row.name,
    exchange: row.exchange === "tpex" ? "tpex" : row.exchange === "twse" ? "twse" : "",
  } satisfies MarketStock]] as const : [],
)).values()];
const listedLargeForceMarketStocks = marketStocks.filter((stock) => /^\d{4}$/.test(stock.ticker) && !stock.ticker.startsWith("00"));
const signalGroupsByTicker = (() => {
  const normalized = stockGroupsSource.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return new Map<string, string[]>();
  try {
    const groups = JSON.parse(
      normalized
        .slice(start, end + 2)
        .replace(/\(/g, "[")
        .replace(/\)/g, "]")
        .replace(/'/g, '"')
        .replace(/,\s*([}\]])/g, "$1"),
    ) as StockGroupMap;
    const excluded = new Set(["股期標的", "小型股票期貨", "ETF"]);
    const byTicker = new Map<string, string[]>();
    for (const [group, members] of Object.entries(groups)) {
      if (excluded.has(group)) continue;
      for (const [ticker] of members) {
        const current = byTicker.get(ticker) ?? [];
        if (!current.includes(group)) current.push(group);
        byTicker.set(ticker, current);
      }
    }
    return byTicker;
  } catch {
    return new Map<string, string[]>();
  }
})();
// 這個排行榜的母體是產品定義的 67 族群，不是上市櫃全部 2,000 多檔。
// 先縮成有族群歸屬的個股，第一批就能產生完整 20 多／20 空，不再出現
// 掃了 40 檔卻只有 12 檔屬於 67 族群的錯誤。
const largeForceMarketStocks = listedLargeForceMarketStocks.filter((stock) => signalGroupsByTicker.has(stock.ticker));
let mainForceScanCursor = 0;
let mainForceScanStatus = { processed: 0, eligible: 0, total: marketStocks.length, nextCursor: 0, mode: "waiting" };
const historicalVolumeCache = new Map<string, Promise<MainForceHistoricalVolumes>>();
const previousChipRankCache = new Map<string, { expiresAt: number; pending: Promise<MainForceChipRankings> }>();
const previousLargeNetCache = new Map<string, { expiresAt: number; pending: Promise<PreviousLargeNetBaseline[]> }>();
const extraLargeSellBackfillAttempts = new Map<string, number>();
let fiveMinutePatternHistoryCache = new Map<string, { expiresAt: number; context: FiveMinutePatternHistoryContext | null }>();
const instantLargeTriggerForceCache = new Map<string, number | null>();
let signalGroupRankingsCache: { expiresAt: number; value: MainForceGroupRankings } | null = null;

function signedGroupChange(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

async function loadSignalGroupRankings() {
  if (signalGroupRankingsCache && signalGroupRankingsCache.expiresAt > Date.now()) return signalGroupRankingsCache.value;
  const [groups, quotes] = await Promise.all([loadConfiguredGroups(), loadSharedLatestQuotes()]);
  const rows = [...groups].flatMap(([name, members]) => {
    const values = members.flatMap(member => {
      const quote = quotes.byCode.get(member.code);
      return quote && typeof quote.changePct === "number" ? [quote.changePct] : [];
    });
    return values.length ? [{name, avgChange: values.reduce((a, b) => a + b, 0) / values.length}] : [];
  });
  if (rows.length < INSTANT_LARGE_GROUP_RANK_LIMIT) throw new Error("signal-group-ranking-malformed");
  const buildRows = (direction: "strong" | "weak") => [...rows]
    .sort((left, right) => direction === "strong" ? right.avgChange - left.avgChange : left.avgChange - right.avgChange)
    .slice(0, INSTANT_LARGE_GROUP_RANK_LIMIT)
    .map((row, index) => ({ rank: index + 1, name: row.name, change: signedGroupChange(row.avgChange) }));
  const rankings = {
    strong: buildRows("strong"),
    weak: buildRows("weak"),
  } satisfies MainForceGroupRankings;
  signalGroupRankingsCache = { expiresAt: Date.now() + 60_000, value: rankings };
  return rankings;
}

function clientGroupRankings(request: NextRequest) {
  const encoded = request.headers.get("x-hanstock-group-rankings");
  if (!encoded || encoded.length > 12_000) return undefined;
  try {
    const payload = JSON.parse(decodeURIComponent(encoded)) as MainForceGroupRankings;
    const normalize = (rows: MainForceGroupRankings["strong"]) => (Array.isArray(rows) ? rows : [])
      .flatMap((row) => {
        const rank = Number(row?.rank);
        const name = String(row?.name ?? "").trim();
        const change = String(row?.change ?? "").trim();
        return Number.isInteger(rank) && rank >= 1 && rank <= INSTANT_LARGE_GROUP_RANK_LIMIT && name
          ? [{ rank, name, change }]
          : [];
      })
      .sort((left, right) => left.rank - right.rank)
      .slice(0, INSTANT_LARGE_GROUP_RANK_LIMIT);
    const strong = normalize(payload.strong);
    const weak = normalize(payload.weak);
    return strong.length >= SIGNAL_GROUP_RANK_LIMIT && weak.length >= SIGNAL_GROUP_RANK_LIMIT
      ? { strong, weak } satisfies MainForceGroupRankings
      : undefined;
  } catch {
    return undefined;
  }
}

function taipeiTradeDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function isTaipeiIntradayCollectionWindow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = value("weekday");
  const minute = Number(value("hour")) * 60 + Number(value("minute"));
  return weekday !== "Sat" && weekday !== "Sun" && minute >= 9 * 60 && minute <= 13 * 60 + 35;
}

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function taipeiMinuteParts(timestamp: number) {
  const date = new Date(timestamp + 8 * 60 * 60 * 1_000);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function isSignal(value: unknown): value is EarlySellSignalRecord {
  if (!value || typeof value !== "object") return false;
  const signal = value as Record<string, unknown>;
  return /^\d{4}-\d{2}-\d{2}$/.test(String(signal.tradeDate ?? ""))
    && /^[0-9A-Z]{2,12}$/.test(String(signal.ticker ?? ""))
    && typeof signal.name === "string"
    && ["daytradeEarlySell50", "daytradeEarlyBuy50", "intradayLargeForceBuy", "intradayLargeForceSell", "instantLargeBuy", "instantLargeSell", "triangleNearBreakout", "triangleBreakoutPendingVolume", "triangleVolumeBreakout", "fiveMinuteTwelveShort", "fiveMinuteOnePlusTwoLong"].includes(String(signal.kind ?? ""))
    && typeof signal.label === "string"
    && Number.isFinite(Number(signal.barTs))
    && Number.isFinite(Number(signal.price))
    && typeof signal.note === "string";
}

function normalizeSignals(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter(isSignal).map((signal) => ({
    ...signal,
    ticker: signal.ticker.toUpperCase(),
    label: signal.label.replaceAll("早盤", "盤中"),
    barTs: Math.trunc(Number(signal.barTs)),
    price: Number(signal.price),
    note: signal.note.replaceAll("早盤", "盤中"),
  })).flatMap((signal) => INSTANT_LARGE_SIGNAL_KINDS.includes(signal.kind)
    ? (normalizeInstantLargeOrderSignal(signal) ? [normalizeInstantLargeOrderSignal(signal) as EarlySellSignalRecord] : [])
    : [signal]);
}

function formatTodayImmediateSignal(signal: EarlySellSignalRecord) {
  if (signal.kind === "daytradeEarlyBuy50") {
    return { ...signal, label: "盤中大單買進達前日預估隔日賣壓 200%" };
  }
  if (signal.kind === "daytradeEarlySell50") {
    return { ...signal, label: "盤中大單賣出達前日預估隔日賣壓 200%" };
  }
  return signal;
}

/**
 * 特大買／賣單的定義是「同一交易日、同一檔股票首次達標」；回補來源
 * 更新後可能會算出不同的第一根分 K。舊資料表以 barTs 當唯一鍵，
 * 因而會暫時留下兩筆。對外回傳時必須先壓成一檔一筆，不能把訊號筆數
 * 誤當成股票檔數。
 */
function oneExtraLargeSignalPerTicker(signals: EarlySellSignalRecord[]) {
  const firstByTicker = new Map<string, EarlySellSignalRecord>();
  for (const signal of [...signals].sort((left, right) => left.barTs - right.barTs || left.ticker.localeCompare(right.ticker))) {
    if (!firstByTicker.has(signal.ticker)) firstByTicker.set(signal.ticker, signal);
  }
  return [...firstByTicker.values()].sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker));
}

async function loadMinuteBars(base: string, tickers: string[], signal?: AbortSignal) {
  const barsByTicker = new Map<string, FourGateMinuteBar[]>();
  const batches = Array.from({ length: Math.ceil(tickers.length / 40) }, (_, index) => tickers.slice(index * 40, index * 40 + 40));
  const settled = await mapMarketBatches(batches, async (codes) => {
    signal?.throwIfAborted();
    const payload = await fetchBoundedMarketJson<{ data?: Record<string, FourGateMinuteBar[]> }>(new URL("/api/hub/bars1m/batch", base), () => ({
      method: "POST",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "User-Agent": "HanStock-Battle-Four-Gate/1.0",
      },
      body: JSON.stringify({ codes }),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(6_000)]) : AbortSignal.timeout(6_000),
    }));
    for (const code of codes) {
      const bars = payload.data?.[code];
      barsByTicker.set(code, Array.isArray(bars) ? bars : []);
    }
  });
  const failed = settled.find(result => result.status === 'rejected');
  if (tickers.length && ![...barsByTicker.values()].some(bars => bars.length)) {
    throw failed?.status === 'rejected' ? failed.reason : new Error('minute-feed-empty');
  }
  return barsByTicker;
}

function instantLargeTriggerForceKey(signal: EarlySellSignalRecord) {
  return `${signal.tradeDate}:${signal.ticker}:${signal.barTs}`;
}

async function qualifyIncomingInstantLargeSignals(
  signals: EarlySellSignalRecord[],
) {
  if (signals.length === 0) return signals;
  const afterHours = signals.filter(isAfterHoursTradeSignal);
  signals = signals.filter(signal => !isAfterHoursTradeSignal(signal));
  const missing = signals.filter((signal) => !instantLargeTriggerForceCache.has(instantLargeTriggerForceKey(signal)));
  if (missing.length > 0) {
    const tickers = [...new Set(missing.map((signal) => signal.ticker))];
    const results = await Promise.allSettled(HUB_BASES.map(hubBase => loadMinuteBars(hubBase, tickers)));
    const sourceBars = results.flatMap(result => result.status === "fulfilled" ? [result.value] : []);
    if (!sourceBars.length) {
      const failed = results.find(result => result.status === "rejected");
      throw failed?.status === "rejected" ? failed.reason : new Error("instant-large-trigger-force-source-unavailable");
    }
    for (const signal of missing) {
      // Evaluate each source independently at this trigger; never combine cumulative volumes
      // across sources or select another time/direction just to fill a missing value.
      const value = sourceBars.map(barsByTicker => calculateIntradayLargeForceValue(
        (barsByTicker.get(signal.ticker) ?? []) as IntradayLargeForceMinuteBar[],
        signal.tradeDate,
        signal.barTs,
      )).find(value => value !== null);
      instantLargeTriggerForceCache.set(instantLargeTriggerForceKey(signal), value?.forcePct ?? null);
    }
  }
  if (instantLargeTriggerForceCache.size > 20_000) instantLargeTriggerForceCache.clear();
  return [...afterHours, ...signals.flatMap((signal) => {
    const qualified = qualifyInstantLargeSignalByTriggerForce(
      signal,
      instantLargeTriggerForceCache.get(instantLargeTriggerForceKey(signal)) ?? null,
    );
    return qualified ? [qualified] : [];
  })];
}

/**
 * The batch endpoint is optimized for the newest bars and may omit 09:00.
 * Four-gate needs the opening five-minute range and the whole-session VWAP,
 * so its candidates must use the complete per-symbol minute-bar endpoint.
 */
async function loadCompleteMinuteBars(base: string, tickers: string[], fallback = new Map<string, FourGateMinuteBar[]>()) {
  const barsByTicker = new Map<string, FourGateMinuteBar[]>();
  await mapMarketBatches(tickers, async (ticker) => {
    try {
      const payload = await fetchBoundedMarketJson<{ bars?: FourGateMinuteBar[] }>(new URL(`/api/hub/bars1m/${encodeURIComponent(ticker)}`, base), () => ({
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Four-Gate-Complete/1.0" },
        signal: AbortSignal.timeout(6_000),
      }));
      const complete = Array.isArray(payload?.bars) ? payload.bars : [];
      barsByTicker.set(ticker, complete.length ? complete : (fallback.get(ticker) ?? []));
    } catch {
      barsByTicker.set(ticker, fallback.get(ticker) ?? []);
    }
  });
  return barsByTicker;
}

type PatternCandleEnvelope = { result?: { data?: { json?: { candles?: Array<Record<string, unknown>> } } } };

function patternHistoryDate(value: unknown, tradeDate: string) {
  const text = String(value ?? "").trim();
  const full = text.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/);
  if (full) return `${full[1]}-${full[2]}-${full[3]}`;
  const short = text.match(/^(\d{2})\/(\d{2})\s+\d{2}:\d{2}/);
  if (!short) return "";
  let year = Number(tradeDate.slice(0, 4));
  let result = `${year}-${short[1]}-${short[2]}`;
  // 1 月盤中的歷史可能包含上一年 12 月，短日期需補回正確年度。
  if (result > tradeDate && Number(short[1]) >= 11 && Number(tradeDate.slice(5, 7)) <= 2) {
    year -= 1;
    result = `${year}-${short[1]}-${short[2]}`;
  }
  return result;
}

function fiveMinutePatternHistoryContext(rows: Array<Record<string, unknown>>, tradeDate: string) {
  const sessions = new Map<string, Array<{ high: number; close: number }>>();
  let openingBar: FiveMinutePatternMinuteBar | undefined;
  for (const row of rows) {
    const rawDate = String(row.date ?? row.time ?? "").trim();
    const date = patternHistoryDate(rawDate, tradeDate);
    const high = finiteNumber(row.high);
    const close = finiteNumber(row.close);
    if (!date || high === null || close === null || high <= 0 || close <= 0) continue;
    if (date === tradeDate) {
      const time = rawDate.match(/(?:^|[ T])(\d{2}):(\d{2})/) ?? rawDate.match(/^\d{2}\/\d{2}\s+(\d{2}):(\d{2})/);
      const open = finiteNumber(row.open) ?? close;
      const low = finiteNumber(row.low) ?? close;
      if (time?.[1] === "09" && time?.[2] === "00" && low > 0) {
        openingBar = {
          ts: Date.parse(`${tradeDate}T09:00:00+08:00`),
          open,
          high,
          low,
          close,
          volume: finiteNumber(row.volume) ?? 0,
        };
      }
      continue;
    }
    if (date > tradeDate) continue;
    const session = sessions.get(date) ?? [];
    session.push({ high, close });
    sessions.set(date, session);
  }
  const previousTradeDate = [...sessions.keys()].sort().at(-1);
  const previous = previousTradeDate ? sessions.get(previousTradeDate) ?? [] : [];
  if (!previousTradeDate || previous.length < 19) return null;
  return {
    previousTradeDate,
    previousHigh: Math.max(...previous.map((row) => row.high)),
    previousCloses: previous.slice(-19).map((row) => row.close),
    openingBar,
  } satisfies FiveMinutePatternHistoryContext;
}

async function fetchFiveMinutePatternHistoryBatch(codes: string[], tradeDate: string) {
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "5m" } }]));
  const payload = await fetchBoundedMarketJson<PatternCandleEnvelope[]>(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, () => ({
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Five-Minute-Patterns/1.0" },
    signal: AbortSignal.timeout(18_000),
  }));
  if (!Array.isArray(payload) || payload.length !== codes.length
    || payload.some(item => !Array.isArray(item?.result?.data?.json?.candles))) {
    throw new Error("five-minute-history-batch-incomplete");
  }
  return new Map(codes.map((code, index) => [
    code,
    fiveMinutePatternHistoryContext(payload[index]?.result?.data?.json?.candles ?? [], tradeDate),
  ]));
}

async function loadFiveMinutePatternHistories(codes: string[], tradeDate: string) {
  const now = Date.now();
  const result = new Map<string, FiveMinutePatternHistoryContext | null>();
  const missing: string[] = [];
  for (const code of codes) {
    const cached = fiveMinutePatternHistoryCache.get(`${tradeDate}:${code}`);
    if (cached && cached.expiresAt > now) result.set(code, cached.context);
    else missing.push(code);
  }
  const batches = Array.from({ length: Math.ceil(missing.length / 8) }, (_, index) => missing.slice(index * 8, index * 8 + 8));
  const settled = await mapMarketBatches(batches, batch => fetchFiveMinutePatternHistoryBatch(batch, tradeDate));
  for (const item of settled) {
    if (item.status !== "fulfilled") continue;
    item.value.forEach((context, code) => {
      result.set(code, context);
      const taipei = new Date(now + 8 * 60 * 60_000);
      const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
      // 09:10 前持續更新 905 K，避免把尚未收完的第一根高低點鎖住。
      const ttl = minutes < 9 * 60 + 10 || !context?.openingBar ? 5_000 : 30 * 60_000;
      fiveMinutePatternHistoryCache.set(`${tradeDate}:${code}`, { expiresAt: now + ttl, context });
    });
  }
  if (fiveMinutePatternHistoryCache.size > 2_500) {
    fiveMinutePatternHistoryCache = new Map([...fiveMinutePatternHistoryCache].filter(([, item]) => item.expiresAt > now));
  }
  if (settled.some(item => item.status === "rejected")) throw new Error("five-minute-history-retry-required");
  return result;
}

function signalFeedTimestamp(payload: HubSignalSource["payload"], instantLargePayload: HubSignalSource["instantLargePayload"]) {
  const collector = instantLargePayload.collector && typeof instantLargePayload.collector === "object"
    ? instantLargePayload.collector as Record<string, unknown>
    : null;
  const collectorAt = Number(collector?.snapshotTs) || 0;
  const signalAt = Math.max(0, ...normalizeSignals(payload.signals).map((signal) => signal.barTs), ...normalizeSignals(instantLargePayload.signals).map((signal) => signal.barTs));
  return Math.max(collectorAt, signalAt);
}

function shouldUseMinuteBarFallback(tradeDate: string, feedAt: number) {
  if (tradeDate !== taipeiTradeDate()) return false;
  const now = new Date(Date.now() + 8 * 60 * 60 * 1_000);
  const minute = now.getUTCHours() * 60 + now.getUTCMinutes();
  return minute >= 9 * 60 && minute <= 13 * 60 + 35 && (!feedAt || Date.now() - feedAt >= 3 * 60_000);
}

const loadImmediateFallback = timedKeyedSingleFlight(3_000, async (key: string) => {
  const [tradeDate, base] = JSON.parse(key) as [string, string];
    const baselines = (await readPreviousDaytradeFlow(tradeDate, 1000))
      .filter((row) => row.mainForceDataAvailable && row.estimatedNextDaySellAmount > IMMEDIATE_SIGNAL_MIN_PREVIOUS_PRESSURE);
    if (!baselines.length) return { signals: [], fourGateSourceSignals: [], fourGateSignals: [], latestBarAt: 0, scanned: 0 };
    const barsByTicker = await loadMinuteBars(base, baselines.map((row) => row.ticker));
    const calculated = calculateImmediateSignalsFromMinuteBars(tradeDate, baselines, barsByTicker);
    if (!calculated.latestBarAt) throw new Error('minute-feed-current-session-empty');
    const preliminaryFourGateSourceSignals = calculateFourGateSourceSignalsFromMinuteBars(tradeDate, baselines, barsByTicker);
    const fourGateTickers = [...new Set(preliminaryFourGateSourceSignals.map((signal) => signal.ticker))];
    const completeBarsByTicker = await loadCompleteMinuteBars(base, fourGateTickers, barsByTicker);
    const fourGateBaselines = baselines.filter((baseline) => fourGateTickers.includes(baseline.ticker));
    const fourGateSourceSignals = calculateFourGateSourceSignalsFromMinuteBars(tradeDate, fourGateBaselines, completeBarsByTicker);
    const fourGateSignals = calculateFourGateSignals(fourGateSourceSignals, completeBarsByTicker);
    return { ...calculated, fourGateSourceSignals, fourGateSignals, scanned: baselines.length };
});
function calculateLiveImmediateFallback(base: string, tradeDate: string) {
  return loadImmediateFallback(JSON.stringify([tradeDate, base]));
}

function mergeImmediateFallback(current: EarlySellSignalRecord[], fallback: EarlySellSignalRecord[]) {
  const regular = current.filter((signal) => signal.kind !== "daytradeEarlyBuy50" && signal.kind !== "daytradeEarlySell50");
  const firstByDirection = new Map<string, EarlySellSignalRecord>();
  for (const signal of [...current, ...fallback].filter((item) => item.kind === "daytradeEarlyBuy50" || item.kind === "daytradeEarlySell50")) {
    const key = `${signal.tradeDate}:${signal.ticker}:${signal.kind}`;
    const existing = firstByDirection.get(key);
    if (!existing || signal.barTs < existing.barTs) firstByDirection.set(key, signal);
  }
  return [...regular, ...firstByDirection.values()].sort((left, right) => right.barTs - left.barTs);
}

type HubPreviousFlowRow = {
  ticker?: string;
  name?: string;
  trade_date?: string;
  large_buy_amount?: number;
  large_sell_amount?: number;
  total_turnover_amount?: number;
  turnover_amount?: number;
  main_force_data_available?: boolean | number;
  main_force_data_status?: string;
  close_price?: number;
};

async function loadCurrentLargeForceMonitorRows(stocks: MarketStock[], tradeDate: string) {
  let lastError: unknown = null;
  const sources = successfulSourcesInCompletionOrder(
    HUB_BASES.map(base => (signal: AbortSignal) => loadMinuteBars(base, stocks.map(stock => stock.ticker), signal)),
    error => { lastError = error; },
  );
  for await (const barsByTicker of sources) {
    try {
      const rows = stocks.flatMap((stock) => {
        const value = calculateIntradayLargeForceValue(barsByTicker.get(stock.ticker) ?? [], tradeDate);
        return value ? [{ ...value, ticker: stock.ticker, name: stock.name, group: signalGroupsByTicker.get(stock.ticker)?.[0] ?? "未分類" }] : [];
      });
      const signals = stocks.flatMap(stock => calculateIntradayLargeForceSignals(
        stock.ticker, stock.name, barsByTicker.get(stock.ticker) ?? [],
      )).filter(signal => signal.tradeDate === tradeDate);
      return {rows, signals, requestedStocks: stocks, barsByTicker, sourceReached: true};
    } catch (error) { lastError = error; }
  }
  throw lastError ?? new Error("large-force-live-source-unavailable");
}

type FullMarketBaselineScan = { rows: PreviousLargeNetBaseline[]; scanned: number; total: number };
// 避免首次全市場回補佔住單次訊號 API；每輪小批次持續補齊，畫面不等待整體掃描。
const FULL_MARKET_BASELINE_SCAN_SIZE = 16;
const fullMarketBaselineCursor = new Map<string, number>();
const fullMarketBaselineProgress = new Map<string, { available: number; total: number }>();
async function scanFullMarketPreviousLargeNetBaselines(base: string, tradeDate: string): Promise<FullMarketBaselineScan> {
  const stored = await readFullMarketLargeFlowBaselines(tradeDate).catch(() => []);
  const storedByTicker = new Map(stored.map((row) => [row.ticker, row]));
  const missing = marketStocks.filter((stock) => !storedByTicker.has(stock.ticker));
  // A missing first batch must not permanently starve every later stock.
  const offset = (fullMarketBaselineCursor.get(tradeDate) ?? 0) % Math.max(1, missing.length);
  const remaining = [...missing.slice(offset), ...missing.slice(0, offset)].slice(0, FULL_MARKET_BASELINE_SCAN_SIZE);
  fullMarketBaselineCursor.set(tradeDate, offset + remaining.length);
  fullMarketBaselineProgress.set(tradeDate, { available: stored.length, total: marketStocks.length });
  if (!remaining.length) return {
    rows: stored.map((row) => ({ ticker: row.ticker, name: row.name, dataDate: row.tradeDate, netLargeAmount: row.netLargeAmount, turnoverAmount: row.turnoverAmount, fallbackPrice: row.closePrice })),
    scanned: stored.length,
    total: marketStocks.length,
  };

  // Fetch money and turnover from the SAME historical session. The exchange's
  // latest daily quote cannot be used as an old session's funding denominator.
  const url = new URL("/api/hub/daytrade-flow-ranking", base);
  url.searchParams.set("date", tradeDate);
  url.searchParams.set("codes", remaining.map((stock) => stock.ticker).join(","));
  url.searchParams.set("include_all", "true");
  url.searchParams.set("scan_limit", String(FULL_MARKET_BASELINE_SCAN_SIZE));
  url.searchParams.set("limit", String(FULL_MARKET_BASELINE_SCAN_SIZE));
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(18_000) });
  if (!response.ok) throw new Error(`extra-large-baseline-${response.status}`);
  const payload = await response.json() as { data_date?: string; rows?: HubPreviousFlowRow[] };
  const requested = new Set(remaining.map((stock) => stock.ticker));
  const rows = (payload.rows ?? []).flatMap((row) => {
    const ticker = String(row.ticker ?? "").trim().toUpperCase();
    const dataDate = String(row.trade_date ?? payload.data_date ?? "");
    const buy = row.large_buy_amount == null ? NaN : Number(row.large_buy_amount);
    const sell = row.large_sell_amount == null ? NaN : Number(row.large_sell_amount);
    const turnoverAmount = Number(row.total_turnover_amount ?? row.turnover_amount);
    const closePrice = Number(row.close_price);
    if (!requested.has(ticker) || dataDate !== tradeDate || !row.main_force_data_available
      || !Number.isFinite(buy) || !Number.isFinite(sell) || buy < 0 || sell < 0
      || !Number.isFinite(turnoverAmount) || turnoverAmount <= 0 || !Number.isFinite(closePrice) || closePrice <= 0) return [];
    return [{ ticker, name: String(row.name ?? ticker), tradeDate, netLargeAmount: buy - sell, turnoverAmount, closePrice }];
  });
  await saveFullMarketLargeFlowBaselines(rows);
  const all = [...storedByTicker.values(), ...rows];
  fullMarketBaselineProgress.set(tradeDate, { available: all.length, total: marketStocks.length });
  return {
    rows: all.map((row) => ({ ticker: row.ticker, name: row.name, dataDate: row.tradeDate, netLargeAmount: row.netLargeAmount, turnoverAmount: row.turnoverAmount, fallbackPrice: row.closePrice })),
    scanned: all.length,
    total: marketStocks.length,
  };
}

async function loadPreviousLargeNetBaselines(base: string, signalTradeDate: string) {
  const cached = previousLargeNetCache.get(signalTradeDate);
  if (cached && cached.expiresAt > Date.now()) return cached.pending;
  const pending = (async () => {
    const previousTradeDate = resolveIntradaySignalCutoverDate(
      new Date(`${signalTradeDate}T08:00:00+08:00`), await loadTwseClosedTradingDates(),
    );
    // 特大買／賣單不再借用「疑似隔日沖強勢大單」排行；直接輪詢全市場前日逐筆大單資料。
    const [fullMarket, stored] = await Promise.all([
      scanFullMarketPreviousLargeNetBaselines(base, previousTradeDate).catch(() => null),
      readPreviousDaytradeFlow(signalTradeDate, 2000).catch(() => []),
    ]);
    const storedBaselines = stored.flatMap((row) => {
      if (row.tradeDate !== previousTradeDate || !row.mainForceDataAvailable || row.netLargeAmount === 0) return [];
      return [{ ticker: row.ticker, name: row.name, dataDate: row.tradeDate, netLargeAmount: row.netLargeAmount, turnoverAmount: row.turnoverAmount, fallbackPrice: row.closePrice }];
    });
    // The full-market backfill is incremental.  A partial batch must be merged
    // with yesterday's permanent D1 rows; returning the partial batch alone is
    // what incorrectly made both extra-large tabs stay at zero.
    const availableBaselines = mergePreviousLargeNetBaselines(fullMarket?.rows ?? [], storedBaselines);
    // Always query the exact previous session: a partial local batch is not full coverage.

    try {
      const response = await fetch(new URL(`/api/hub/daytrade-flow-ranking?date=${encodeURIComponent(previousTradeDate)}&limit=2000`, base), {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Extra-Large-Sell/1.0" },
        signal: AbortSignal.timeout(18_000),
      });
      if (!response.ok) return availableBaselines;
      const payload = await response.json() as { data_date?: string; rows?: HubPreviousFlowRow[] };
      const datedBaselines = (payload.rows ?? []).flatMap((row) => {
        const ticker = String(row.ticker ?? "").trim().toUpperCase();
        const dataDate = String(row.trade_date ?? payload.data_date ?? "").trim();
        const buy = Math.max(0, Number(row.large_buy_amount) || 0);
        const sell = Math.max(0, Number(row.large_sell_amount) || 0);
        const netLargeAmount = buy - sell;
        const turnoverAmount = Math.max(0, Number(row.total_turnover_amount ?? row.turnover_amount) || 0);
        const available = row.main_force_data_available === undefined || (row.main_force_data_available !== false && row.main_force_data_available !== 0);
        if (!/^[0-9A-Z]{2,12}$/.test(ticker) || !available || dataDate !== previousTradeDate || netLargeAmount === 0) return [];
        return [{ ticker, name: String(row.name ?? ticker), dataDate, netLargeAmount, turnoverAmount, fallbackPrice: Number(row.close_price) || undefined }];
      });
      return mergePreviousLargeNetBaselines(datedBaselines, availableBaselines);
    } catch {
      return availableBaselines;
    }
  })();
  // 全市場母體採分批回補；短暫去重即可，不能把第一批結果快取五分鐘。
  previousLargeNetCache.set(signalTradeDate, { expiresAt: Date.now() + 7_000, pending });
  return pending.catch((error) => {
    previousLargeNetCache.delete(signalTradeDate);
    throw error;
  });
}

async function calculateLiveDerivedSignals(base: string, signals: EarlySellSignalRecord[], signalTradeDate: string) {
  const sourceSignals = signals.filter((signal): signal is EarlySellSignalRecord & FourGateSourceSignal =>
    signal.kind === "daytradeEarlyBuy50" || signal.kind === "daytradeEarlySell50",
  );
  const baselines = await loadPreviousLargeNetBaselines(base, signalTradeDate).catch(() => []);
  const fourGateTickers = fourGateCandidateTickers(sourceSignals);
  const tickers = [...new Set([...fourGateTickers, ...extraLargeSellCandidateTickers(baselines), ...extraLargeBuyCandidateTickers(baselines)])];
  if (tickers.length === 0) return { fourGateSignals: [], extraLargeSellSignals: [], extraLargeBuySignals: [] };
  const barsByTicker = await loadMinuteBars(base, tickers);
  const completeFourGateBarsByTicker = await loadCompleteMinuteBars(base, fourGateTickers, barsByTicker);
  return {
    fourGateSignals: calculateFourGateSignals(sourceSignals, completeFourGateBarsByTicker),
    extraLargeSellSignals: calculateIntradayExtraLargeSellSignals(signalTradeDate, baselines, barsByTicker),
    extraLargeBuySignals: calculateIntradayExtraLargeBuySignals(signalTradeDate, baselines, barsByTicker),
  };
}

function nextMarketStocks(size: number) {
  if (marketStocks.length === 0) return [];
  const rows = Array.from({ length: Math.min(size, marketStocks.length) }, (_, index) => marketStocks[(mainForceScanCursor + index) % marketStocks.length]);
  mainForceScanCursor = (mainForceScanCursor + rows.length) % marketStocks.length;
  return rows;
}

async function fetchIndividualMinuteBars(base: string, stocks: typeof marketStocks) {
  const barsByTicker = new Map<string, MainForceMinuteBar[]>();
  for (let start = 0; start < stocks.length; start += 8) {
    await Promise.all(stocks.slice(start, start + 8).map(async (stock) => {
      try {
        const response = await fetch(new URL(`/api/hub/bars1m/${encodeURIComponent(stock.ticker)}`, base), {
          cache: "no-store",
          headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Main-Force-Zero/1.0" },
          signal: AbortSignal.timeout(8_000),
        });
        const payload = response.ok ? await response.json() as { bars?: MainForceMinuteBar[] } : null;
        barsByTicker.set(stock.ticker, Array.isArray(payload?.bars) ? payload.bars : []);
      } catch {
        barsByTicker.set(stock.ticker, []);
      }
    }));
  }
  return barsByTicker;
}

function parseYahooHistoricalVolumes(payload: unknown) {
  const chart = payload as {
    chart?: { result?: Array<{ timestamp?: number[]; indicators?: { quote?: Array<{ volume?: Array<number | null> }> } }> };
  };
  const result = chart.chart?.result?.[0];
  const timestamps = Array.isArray(result?.timestamp) ? result.timestamp : [];
  const volumes = Array.isArray(result?.indicators?.quote?.[0]?.volume) ? result.indicators.quote[0].volume : [];
  const days: MainForceHistoricalVolumes = new Map();
  for (let index = 0; index < timestamps.length; index += 1) {
    const ts = finiteNumber(timestamps[index]);
    const volumeShares = finiteNumber(volumes[index]);
    if (ts === null || volumeShares === null || volumeShares < 0) continue;
    const { tradeDate, minute } = taipeiMinuteParts(ts * 1_000);
    if (minute < 9 * 60 || minute > 13 * 60 + 30) continue;
    const day = days.get(tradeDate) ?? new Map<number, number>();
    day.set(minute, volumeShares / 1_000);
    days.set(tradeDate, day);
  }
  return days;
}

async function fetchHistoricalVolumes(stock: MarketStock) {
  const suffixes = stock.exchange === "tpex" ? [".TWO"] : stock.exchange === "twse" ? [".TW"] : [".TW", ".TWO"];
  for (const suffix of suffixes) {
    for (const host of ["query2.finance.yahoo.com", "query1.finance.yahoo.com"]) {
      try {
        const url = new URL(`/v8/finance/chart/${encodeURIComponent(stock.ticker + suffix)}`, `https://${host}`);
        url.searchParams.set("range", "7d");
        url.searchParams.set("interval", "1m");
        url.searchParams.set("includePrePost", "false");
        url.searchParams.set("events", "history");
        const response = await fetch(url, {
          cache: "no-store",
          headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-ABCD/1.0" },
          signal: AbortSignal.timeout(12_000),
        });
        if (!response.ok) continue;
        const days = parseYahooHistoricalVolumes(await response.json());
        if (days.size >= 5) return days;
      } catch {
        // 同一代號依序改試另一個 Yahoo 節點或上市櫃後綴。
      }
    }
  }
  return new Map<string, Map<number, number>>();
}

async function cachedHistoricalVolumes(stock: MarketStock) {
  const key = `${taipeiTradeDate()}:${stock.ticker}`;
  const cached = historicalVolumeCache.get(key);
  if (cached) return cached;
  const pending = fetchHistoricalVolumes(stock).then((allDays) => {
    const days = new Map([...allDays].filter(([date]) => date < taipeiTradeDate()).sort(([a], [b]) => a.localeCompare(b)).slice(-5));
    if (days.size < 5) historicalVolumeCache.delete(key);
    return days;
  }).catch(() => {
    historicalVolumeCache.delete(key);
    return new Map<string, Map<number, number>>();
  });
  historicalVolumeCache.set(key, pending);
  while (historicalVolumeCache.size > 32) historicalVolumeCache.delete(historicalVolumeCache.keys().next().value!);
  return pending;
}

async function loadCandidateHistories(stocks: MarketStock[]) {
  const result = new Map<string, MainForceHistoricalVolumes>();
  for (let start = 0; start < stocks.length; start += 6) {
    await Promise.all(stocks.slice(start, start + 6).map(async (stock) => {
      result.set(stock.ticker, await cachedHistoricalVolumes(stock));
    }));
  }
  return result;
}

async function scanMainForceZeroBatch(base: string) {
  const cursorBefore = mainForceScanCursor;
  const batchStocks = nextMarketStocks(MAIN_FORCE_BATCH_SIZE);
  const batchBars = await loadMinuteBars(base, batchStocks.map((stock) => stock.ticker)).catch(() => new Map<string, FourGateMinuteBar[]>());
  const available = batchStocks.filter((stock) => (batchBars.get(stock.ticker)?.length ?? 0) > 0);
  let scannedStocks = batchStocks;
  let barsByTicker = batchBars as Map<string, MainForceMinuteBar[]>;
  let mode = "batch";
  if (available.length < Math.min(20, batchStocks.length)) {
    mainForceScanCursor = cursorBefore;
    scannedStocks = nextMarketStocks(MAIN_FORCE_FALLBACK_SIZE);
    barsByTicker = await fetchIndividualMinuteBars(base, scannedStocks);
    mode = "individual-fallback";
  }
  const eligibleStocks = scannedStocks.filter((stock) => hasPotentialMainForceABCDSignal(barsByTicker.get(stock.ticker) ?? []));
  const histories = await loadCandidateHistories(eligibleStocks);
  mainForceScanStatus = { processed: scannedStocks.length, eligible: eligibleStocks.length, total: marketStocks.length, nextCursor: mainForceScanCursor, mode };
  return {
    mainForceSignals: eligibleStocks.flatMap((stock) => calculateMainForceZeroSignals(
      stock.ticker,
      stock.name,
      barsByTicker.get(stock.ticker) ?? [],
      histories.get(stock.ticker) ?? new Map(),
    )),
    // 盤中大戶力與主力累計共用同一批 1 分 K，不增加第二輪全市場抓取。
    // 四碼普通股才納入，排除 ETF、權證與其他非個股商品。
    largeForceSignals: scannedStocks
      .filter((stock) => /^\d{4}$/.test(stock.ticker) && !stock.ticker.startsWith("00"))
      .flatMap((stock) => calculateIntradayLargeForceSignals(
        stock.ticker,
        stock.name,
        (barsByTicker.get(stock.ticker) ?? []) as IntradayLargeForceMinuteBar[],
      )),
  };
}

const calculateLiveMainForceZeroSignals = timedKeyedSingleFlight(1_000, (base: string) => scanMainForceZeroBatch(base), 2);

async function collectIntradayLargeForceBatch(tradeDate: string, suppliedGroupRankings?: MainForceGroupRankings) {
  const claim = await acquireIntradayLargeForceScanBatch({
    tradeDate,
    total: largeForceMarketStocks.length,
    // 盤中完成一輪後持續重掃，才能捕捉稍晚才突破門檻的股票；收盤後
    // 只跑完最後一輪，不再反覆重算同一個交易日。
    restartCompleted: isTaipeiIntradayCollectionWindow(),
    restartCooldownMs: 45_000,
  });
  if (!claim.acquired || !claim.progress) {
    return {
      saved: 0,
      patternSaved: 0,
      busy: claim.progress?.status === "running",
      progress: claim.progress,
    };
  }

  const progress = claim.progress;
  const stocks = largeForceMarketStocks.slice(progress.nextIndex, progress.nextIndex + LARGE_FORCE_BATCH_SIZE);
  try {
    const monitor = await loadCurrentLargeForceMonitorRows(stocks, tradeDate);
    // 大戶力先保存供畫面讀取；五分 K 補算成功才推進游標。
    // 來源失敗時重試同一批，不把漏算的股票標記成完成。
    if (monitor.rows.length > 0) {
      await saveIntradayLargeForceMonitorRows({ tradeDate, batchStart: progress.nextIndex, rows: monitor.rows });
    }
    await saveEarlySellSignals(monitor.signals);
    storedSnapshotCache.clear();
    let patternSaved = 0;
    try {
      const availableStocks = monitor.requestedStocks.filter(
        (stock) => (monitor.barsByTicker.get(stock.ticker)?.length ?? 0) > 0,
      );
      const patternStocks = availableStocks;
      if (patternStocks.length > 0) {
        const histories = await loadFiveMinutePatternHistories(patternStocks.map((stock) => stock.ticker), tradeDate);
        let patternSignals: EarlySellSignalRecord[] = patternStocks.flatMap((stock) => calculateFiveMinutePatternSignals(
          stock.ticker,
          stock.name,
          (monitor.barsByTicker.get(stock.ticker) ?? []) as FiveMinutePatternMinuteBar[],
          histories.get(stock.ticker),
          tradeDate,
        ));
        const rankings = suppliedGroupRankings ?? await loadSignalGroupRankings().catch(() => undefined);
        if (!rankings) throw new Error("five-minute-group-ranking-retry-required");
        patternSignals = annotateMainForceGroupRanks(patternSignals, signalGroupsByTicker, rankings)
          .filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, rankings));
        // 每批重新驗證後先移除舊的 1+2 多結果；缺少真正 09:00 五分 K
        // 的股票會保持不入選，不再保留先前用午盤第一根誤判的訊號。
        await deleteEarlySellSignalsForKindTickers(
          tradeDate,
          "fiveMinuteOnePlusTwoLong",
          patternStocks.map((stock) => stock.ticker),
        );
        if (patternSignals.length > 0) await saveEarlySellSignals(patternSignals);
        patternSaved = patternSignals.length;
        storedSnapshotCache.clear();
      }
    } catch (error) {
      console.warn("[daytrade-early-sell] five-minute pattern batch will retry", {
        tradeDate,
        nextIndex: progress.nextIndex,
        message: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
    const completed = await finishIntradayLargeForceScanBatch({
      progress,
      processed: stocks.length,
      available: monitor.rows.length,
      signalCount: monitor.signals.length,
    });
    return { saved: monitor.signals.length, patternSaved, busy: false, progress: completed };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await failIntradayLargeForceScanBatch(progress, message).catch(() => undefined);
    console.error("[daytrade-early-sell] intraday large-force backfill failed", {
      tradeDate,
      nextIndex: progress.nextIndex,
      message,
    });
    throw error;
  }
}

async function loadPreviousChipTopRanks(signalTradeDate: string) {
  const cached = previousChipRankCache.get(signalTradeDate);
  if (cached && cached.expiresAt > Date.now()) return cached.pending;
  const pending = (async () => {
    const [savedResult, settingsResult] = await Promise.allSettled([
      readLatestMarketRanking(),
      readBattleSettings(),
    ]);
    const candidates = [
      savedResult.status === "fulfilled" ? savedResult.value as MarketRankingChipSnapshot | null : null,
      marketRankingSnapshot as unknown as MarketRankingChipSnapshot,
    ].filter((candidate): candidate is MarketRankingChipSnapshot => Boolean(candidate?.dataDate && Array.isArray(candidate.rows)))
      .filter((candidate) => {
        const dataDate = normalizeChipRankingDate(candidate.dataDate);
        return dataDate && dataDate < signalTradeDate;
      })
      .sort((left, right) => normalizeChipRankingDate(right.dataDate).localeCompare(normalizeChipRankingDate(left.dataDate)));
    const snapshot = candidates[0];
    if (!snapshot) return {
      increasing: new Map<string, MainForceChipRank>(),
      decreasing: new Map<string, MainForceChipRank>(),
    };
    const settings = settingsResult.status === "fulfilled" ? settingsResult.value : DEFAULT_BATTLE_SETTINGS;
    return buildPreviousSessionChipTopRanks(snapshot, settings.chipWeights, signalTradeDate);
  })();
  previousChipRankCache.set(signalTradeDate, { expiresAt: Date.now() + 5 * 60 * 1_000, pending });
  return pending.catch((error) => {
    previousChipRankCache.delete(signalTradeDate);
    throw error;
  });
}

async function annotatePreviousChipRanks<T extends EarlySellSignalRecord>(signals: T[], tradeDate: string) {
  if (signals.length === 0) return signals;
  try {
    return annotateMainForceChipRanks(signals, await loadPreviousChipTopRanks(tradeDate));
  } catch {
    return signals;
  }
}

function taipeiStoredBarTimestamp(barTime: string) {
  const matched = barTime.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (!matched) return null;
  const timestamp = Date.UTC(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3]), Number(matched[4]) - 8, Number(matched[5]));
  return Number.isFinite(timestamp) ? timestamp : null;
}

async function loadStoredMinuteBars(tradeDate: string, baselines: PreviousLargeNetBaseline[]) {
  const barsByTicker = new Map<string, FourGateMinuteBar[]>();
  const fallbackPrices = new Map(baselines.map((baseline) => [baseline.ticker.toUpperCase(), Number(baseline.fallbackPrice) || 0]));
  const records = await readIntradayForceForTickers([...new Set([
    ...extraLargeSellCandidateTickers(baselines),
    ...extraLargeBuyCandidateTickers(baselines),
  ])], tradeDate, "1m");
  for (const record of records) {
    const ts = taipeiStoredBarTimestamp(record.barTime);
    if (ts === null) continue;
    const ticker = record.ticker.toUpperCase();
    const close = fallbackPrices.get(ticker) ?? 0;
    const bars = barsByTicker.get(ticker) ?? [];
    bars.push({
      ts,
      high: close,
      low: close,
      close,
      volume: 0,
      main_buy_amount: record.buyAmount,
      main_sell_amount: record.sellAmount,
    });
    barsByTicker.set(ticker, bars);
  }
  return barsByTicker;
}

const extraLargeHistoryCursor = new Map<string, number>();
async function loadExtraLargeHistoricalBars(base: string, tradeDate: string, tickers: string[]) {
  const result = new Map<string, FourGateMinuteBar[]>();
  if (!tickers.length) return result;
  const offset = (extraLargeHistoryCursor.get(tradeDate) ?? 0) % tickers.length;
  const codes = [...tickers.slice(offset), ...tickers.slice(0, offset)].slice(0, 24);
  extraLargeHistoryCursor.set(tradeDate, offset + codes.length);
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((ticker, index) => [index, { json: { ticker, interval: "1m" } }]));
  const [prices, forces] = await Promise.all([
    fetch(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, { cache: "no-store", signal: AbortSignal.timeout(20_000) })
      .then(async (response) => response.ok ? response.json() as Promise<PatternCandleEnvelope[]> : []).catch(() => [] as PatternCandleEnvelope[]),
    Promise.all(codes.map(async (ticker) => {
      try {
        const response = await fetch(new URL(`/api/hub/force/bars/${encodeURIComponent(ticker)}?interval=1m&trade_date=${tradeDate}&days=1&limit=5000&backfill=false`, base), { cache: "no-store", signal: AbortSignal.timeout(12_000) });
        const payload = response.ok ? await response.json() as { bars?: FourGateMinuteBar[] } : null;
        return payload?.bars ?? [];
      } catch { return []; }
    })),
  ]);
  codes.forEach((ticker, index) => {
    const priceByTime = new Map<number, { high: number; low: number; close: number; volume: number }>();
    for (const candle of prices[index]?.result?.data?.json?.candles ?? []) {
      const raw = String(candle.date ?? "");
      if (patternHistoryDate(raw, tradeDate) !== tradeDate) continue;
      const time = raw.match(/(?:^|[ T])(\d{2}:\d{2})/);
      const close = Number(candle.close);
      if (!time || !Number.isFinite(close) || close <= 0) continue;
      priceByTime.set(Date.parse(`${tradeDate}T${time[1]}:00+08:00`), { high: Number(candle.high) || close, low: Number(candle.low) || close, close, volume: Number(candle.volume) / 1000 });
    }
    const bars = forces[index].flatMap((force) => {
      const price = priceByTime.get(Number(force.ts));
      return price && taipeiMinuteParts(force.ts).tradeDate === tradeDate ? [{ ...force, ...price }] : [];
    });
    if (bars.length) result.set(ticker, bars);
  });
  return result;
}

async function backfillExtraLargeSellSignals(base: string, tradeDate: string, groupRankings?: MainForceGroupRankings) {
  const lastAttempt = extraLargeSellBackfillAttempts.get(tradeDate) ?? 0;
  if (!tradeDate || Date.now() - lastAttempt < 7_000) return false;
  extraLargeSellBackfillAttempts.set(tradeDate, Date.now());
  try {
    const baselines = await loadPreviousLargeNetBaselines(base, tradeDate);
    const tickers = [...new Set([...extraLargeSellCandidateTickers(baselines), ...extraLargeBuyCandidateTickers(baselines)])];
    const barsByTicker = await loadMinuteBars(base, tickers).catch(() => new Map<string, FourGateMinuteBar[]>());
    const missing = tickers.filter((ticker) => !(barsByTicker.get(ticker) ?? []).some((bar) => taipeiMinuteParts(bar.ts).tradeDate === tradeDate && bar.main_buy_amount != null && bar.main_sell_amount != null));
    const historical = await loadExtraLargeHistoricalBars(base, tradeDate, missing);
    for (const [ticker, bars] of historical) barsByTicker.set(ticker, bars);
    let signals: EarlySellSignalRecord[] = [
      ...calculateIntradayExtraLargeSellSignals(tradeDate, baselines, barsByTicker),
      ...calculateIntradayExtraLargeBuySignals(tradeDate, baselines, barsByTicker),
    ];
    const storedBars = await loadStoredMinuteBars(tradeDate, baselines.map((baseline) => ({ ...baseline, fallbackPrice: undefined })));
    const storedSignals = [
      ...calculateIntradayExtraLargeSellSignals(tradeDate, baselines.map((baseline) => ({ ...baseline, fallbackPrice: undefined })), storedBars),
      ...calculateIntradayExtraLargeBuySignals(tradeDate, baselines.map((baseline) => ({ ...baseline, fallbackPrice: undefined })), storedBars),
    ];
    const liveTickers = new Set(signals.map((signal) => signal.ticker));
    signals = [...signals, ...storedSignals.filter((signal) => !liveTickers.has(signal.ticker))];
    if (groupRankings) signals = annotateMainForceGroupRanks(signals, signalGroupsByTicker, groupRankings);
    signals = await annotatePreviousChipRanks(signals, tradeDate);
    await saveEarlySellSignals(signals);
    storedSnapshotCache.clear();
    const previousDates = [...new Set(baselines.map((row) => row.dataDate))];
    const coverage = previousDates.length === 1 ? fullMarketBaselineProgress.get(previousDates[0]) : undefined;
    const minuteAvailable = tickers.filter((ticker) => [...(barsByTicker.get(ticker) ?? []), ...(storedBars.get(ticker) ?? [])]
      .some((bar) => taipeiMinuteParts(bar.ts).tradeDate === tradeDate && bar.main_buy_amount != null && bar.main_sell_amount != null)).length;
    return {
      completed: Boolean(coverage && coverage.available >= coverage.total && minuteAvailable === tickers.length && groupRankings),
      baselineDates: previousDates, baselines: baselines.length,
      baselineAvailable: coverage?.available ?? 0, baselineTotal: marketStocks.length,
      sellCandidates: extraLargeSellCandidateTickers(baselines).length,
      buyCandidates: extraLargeBuyCandidateTickers(baselines).length,
      minuteAvailable, candidates: tickers.length, generated: signals.length,
      groupRankingsAvailable: Boolean(groupRankings),
    };
  } catch (error) {
    extraLargeSellBackfillAttempts.delete(tradeDate);
    console.warn("[daytrade-early-sell] extra-large sell backfill unavailable", error);
    return false;
  }
}

async function qualifyStoredExtraLargeSignals(signals: EarlySellSignalRecord[]) {
  const pending = signals.filter((signal) => !hasQualifiedExtraLargeTriggerForce(signal));
  if (pending.length === 0) return signals;
  const qualified = signals.filter(hasQualifiedExtraLargeTriggerForce);
  const repaired: EarlySellSignalRecord[] = [];
  for (const tradeDate of new Set(pending.map((signal) => signal.tradeDate))) {
    const candidates = pending.filter((signal) => signal.tradeDate === tradeDate);
    const records = await readIntradayForceForTickers([...new Set(candidates.map((signal) => signal.ticker))], tradeDate, "1m").catch(() => []);
    const barsByTicker = new Map<string, FourGateMinuteBar[]>();
    for (const record of records) {
      const ts = taipeiStoredBarTimestamp(record.barTime);
      if (ts === null) continue;
      const bars = barsByTicker.get(record.ticker) ?? [];
      bars.push({ ts, high: 0, low: 0, close: 0, volume: 0, main_buy_amount: record.buyAmount, main_sell_amount: record.sellAmount });
      barsByTicker.set(record.ticker, bars);
    }
    for (const signal of candidates) {
      const row = qualifyExtraLargeSignalByTriggerBars(signal, barsByTicker.get(signal.ticker) ?? []);
      if (row) repaired.push(row);
    }
  }
  // 保留原始歷史；只補上有資料證明的觸發方向，衝突／缺資料者不對外發出。
  if (repaired.length) await appendEarlySellChipRankAnnotations(repaired);
  return [...qualified, ...repaired];
}

async function storedPayload(tradeDate: string, query: string, limit: number, groupRankings?: MainForceGroupRankings) {
  const [rawStoredSignals, rawFourGateSignals, rawMainForceSignals, rawExtraLargeSellSignals, rawExtraLargeBuySignals, rawLargeForceSignals, rawInstantLargeSignals, dates, summary] = await Promise.all([
    readEarlySellSignals({ tradeDate, query, limit, kinds: GENERAL_SIGNAL_KINDS }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: FOUR_GATE_SIGNAL_KINDS }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: MAIN_FORCE_SIGNAL_KINDS, noteIncludes: MAIN_FORCE_FILTER_MARKER }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: EXTRA_LARGE_SELL_SIGNAL_KINDS }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: EXTRA_LARGE_BUY_SIGNAL_KINDS }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: LARGE_FORCE_SIGNAL_KINDS }),
    readEarlySellSignals({ tradeDate, query, limit, kinds: INSTANT_LARGE_SIGNAL_KINDS }),
    readEarlySellDates(),
    readEarlySellSignalSummary(tradeDate, { mainForceNoteIncludes: MAIN_FORCE_FILTER_MARKER }),
  ]);
  const [allRawStoredSignals, allRawFourGateSignals] = query
    ? await Promise.all([
      readEarlySellSignals({ tradeDate, limit: 2000, kinds: GENERAL_SIGNAL_KINDS }),
      readEarlySellSignals({ tradeDate, limit: 2000, kinds: FOUR_GATE_SIGNAL_KINDS }),
    ])
    : [rawStoredSignals, rawFourGateSignals];
  const hasVerified905Source = (signal: EarlySellSignalRecord) => signal.kind !== "fiveMinuteOnePlusTwoLong"
    || tradeDate < "2026-09-07"
    || signal.note.includes(VERIFIED_905_SOURCE_MARKER);
  // 9/7 起的舊 1+2 訊號在重新掃描完成前先停止對外顯示，避免錯誤的
  // 905 高繼續留在名單；只有帶有完整 09:00 五分 K 證明的新結果可顯示。
  const pressureAmountEligibleAllStoredSignals = allRawStoredSignals
    .filter(hasVerified905Source)
    .filter(passesPreviousEstimatedSellPressureFilter);
  const filteredAllStoredSignals = pressureAmountEligibleAllStoredSignals.filter(passesTodayImmediatePressureRatioFilter);
  const eligiblePressureTickers = new Set(pressureAmountEligibleAllStoredSignals.flatMap((signal) =>
    signal.kind === "daytradeEarlyBuy50" || signal.kind === "daytradeEarlySell50" ? [signal.ticker] : [],
  ));
  const filteredAllFourGateSignals = allRawFourGateSignals.filter((signal) => eligiblePressureTickers.has(signal.ticker));
  const filteredRawStoredSignals = rawStoredSignals
    .filter(hasVerified905Source)
    .filter(passesPreviousEstimatedSellPressureFilter)
    .filter(passesTodayImmediatePressureRatioFilter)
    .map(formatTodayImmediateSignal);
  const filteredRawFourGateSignals = rawFourGateSignals.filter((signal) => eligiblePressureTickers.has(signal.ticker));
  const eligibleExtraLargeSellTickers = new Set(extraLargeSellCandidateTickers(
    await loadPreviousLargeNetBaselines(HUB_BASES[0], tradeDate).catch(() => []),
  ));
  const filteredRawExtraLargeSellSignals = rawExtraLargeSellSignals.filter((signal) => {
    if (eligibleExtraLargeSellTickers.size > 0) return eligibleExtraLargeSellTickers.has(signal.ticker);
    const previousNetAmount = extraLargeSellPreviousNetAmount(signal.note);
    const netFundingRate = extraLargeSellNetFundingRate(signal.note);
    return previousNetAmount !== null
      && previousNetAmount > INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      && netFundingRate !== null
      && netFundingRate > INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE;
  });
  const eligibleExtraLargeBuyTickers = new Set(extraLargeBuyCandidateTickers(
    await loadPreviousLargeNetBaselines(HUB_BASES[0], tradeDate).catch(() => []),
  ));
  const filteredRawExtraLargeBuySignals = rawExtraLargeBuySignals.filter((signal) => {
    if (eligibleExtraLargeBuyTickers.size > 0) return eligibleExtraLargeBuyTickers.has(signal.ticker);
    const previousNetAmount = extraLargeBuyPreviousNetAmount(signal.note);
    const netFundingRate = extraLargeSellNetFundingRate(signal.note);
    return previousNetAmount !== null
      && previousNetAmount > INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      && netFundingRate !== null
      && netFundingRate > INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE;
  });
  // 已發出的訊號採用觸發當下永久保存的族群名次，不可因盤中排行變動
  // 或族群來源短暫逾時而消失。只有舊資料缺少標記時才用目前排行補註。
  const keepQualifiedGroupSignals = (rows: EarlySellSignalRecord[]) => rows.flatMap((signal) => {
    if (hasCapturedMatchingTopGroup(signal)) return [signal];
    if (!groupRankings) return [];
    const [annotated] = annotateMainForceGroupRanks([signal], signalGroupsByTicker, groupRankings);
    return annotated && hasCapturedMatchingTopGroup(annotated) ? [annotated] : [];
  });
  const groupedSignals = keepQualifiedGroupSignals(filteredRawStoredSignals);
  const groupedFourGateSignals = keepQualifiedGroupSignals(filteredRawFourGateSignals);
  const groupedMainForceSignals = keepQualifiedGroupSignals(rawMainForceSignals);
  const qualifiedExtraLargeSignals = await qualifyStoredExtraLargeSignals([...filteredRawExtraLargeSellSignals, ...filteredRawExtraLargeBuySignals]);
  const groupedExtraLargeSellSignals = oneExtraLargeSignalPerTicker(keepQualifiedGroupSignals(qualifiedExtraLargeSignals.filter((signal) => signal.kind === "intradayExtraLargeSell")));
  const groupedExtraLargeBuySignals = oneExtraLargeSignalPerTicker(keepQualifiedGroupSignals(qualifiedExtraLargeSignals.filter((signal) => signal.kind === "intradayExtraLargeBuy")));
  const thresholdedRawInstantLargeSignals = rawInstantLargeSignals.flatMap((signal) => {
    const normalized = normalizeInstantLargeOrderSignal(signal);
    return normalized ? [normalized as EarlySellSignalRecord] : [];
  });
  const triggerQualifiedRawInstantLargeSignals = thresholdedRawInstantLargeSignals.filter(signal => isAfterHoursTradeSignal(signal) || hasQualifiedInstantLargeTriggerForce(signal));
  // 早期瞬間大單由同一個前後 20 族群監看器產生，但舊格式沒有保存
  // 「族群同步」標記。已帶標記者照目前方向嚴格重驗；未帶標記者保留
  // 原始監看結果，再由 100 張／3,000 萬門檻重驗後回到歷史清單。
  const groupedInstantLargeSignals = triggerQualifiedRawInstantLargeSignals.filter(hasCapturedOrLegacyInstantLargeGroup);
  const [signals, fourGateSignals, mainForceSignals, extraLargeSellSignals, extraLargeBuySignals, largeForceSignals, instantLargeSignals] = await Promise.all([
    annotatePreviousChipRanks(groupedSignals, tradeDate),
    annotatePreviousChipRanks(groupedFourGateSignals, tradeDate),
    annotatePreviousChipRanks(groupedMainForceSignals, tradeDate),
    annotatePreviousChipRanks(groupedExtraLargeSellSignals, tradeDate),
    annotatePreviousChipRanks(groupedExtraLargeBuySignals, tradeDate),
    Promise.resolve(rawLargeForceSignals),
    annotatePreviousChipRanks(groupedInstantLargeSignals, tradeDate),
  ]);
  const newlyAnnotated = [
    ...signals.filter((signal, index) => signal.note !== filteredRawStoredSignals[index]?.note),
    ...fourGateSignals.filter((signal, index) => signal.note !== filteredRawFourGateSignals[index]?.note),
    ...mainForceSignals.filter((signal, index) => signal.note !== rawMainForceSignals[index]?.note),
    ...extraLargeSellSignals.filter((signal, index) => signal.note !== filteredRawExtraLargeSellSignals[index]?.note),
    ...extraLargeBuySignals.filter((signal, index) => signal.note !== filteredRawExtraLargeBuySignals[index]?.note),
    ...instantLargeSignals.filter((signal, index) => signal.note !== triggerQualifiedRawInstantLargeSignals[index]?.note),
  ];
  if (newlyAnnotated.length) await appendEarlySellChipRankAnnotations(newlyAnnotated);
  const filteredByKind = { ...summary.byKind };
  let filteredTotal = summary.total;
  for (const kind of Object.keys(filteredByKind)) {
    if (!isActiveIntradayCenterSignal({ kind })) {
      filteredTotal -= Number(filteredByKind[kind]?.events ?? 0);
      delete filteredByKind[kind];
    }
  }
  for (const kind of ["daytradeEarlyBuy50", "daytradeEarlySell50", "fourGateBullish", "fourGateBearish"] as const) {
    const rows = kind === "fourGateBullish" || kind === "fourGateBearish"
      ? filteredAllFourGateSignals.filter((signal) => signal.kind === kind)
      : filteredAllStoredSignals.filter((signal) => signal.kind === kind);
    const previousCount = Number(summary.byKind[kind]?.events ?? 0);
    filteredTotal += rows.length - previousCount;
    filteredByKind[kind] = { events: rows.length, stocks: new Set(rows.map((signal) => signal.ticker)).size };
  }
  const verifiedOnePlusTwoRows = signals.filter((signal) => signal.kind === "fiveMinuteOnePlusTwoLong");
  const previousOnePlusTwoCount = Number(summary.byKind.fiveMinuteOnePlusTwoLong?.events ?? 0);
  filteredTotal += verifiedOnePlusTwoRows.length - previousOnePlusTwoCount;
  filteredByKind.fiveMinuteOnePlusTwoLong = {
    events: verifiedOnePlusTwoRows.length,
    stocks: new Set(verifiedOnePlusTwoRows.map((signal) => signal.ticker)).size,
  };
  const previousExtraLargeSellCount = Number(summary.byKind.intradayExtraLargeSell?.events ?? 0);
  const previousExtraLargeBuyCount = Number(summary.byKind.intradayExtraLargeBuy?.events ?? 0);
  const previousInstantLargeBuyCount = Number(summary.byKind.instantLargeBuy?.events ?? 0);
  const previousInstantLargeSellCount = Number(summary.byKind.instantLargeSell?.events ?? 0);
  const filteredSummary = {
    ...summary,
    total: Math.max(0, filteredTotal - previousExtraLargeSellCount - previousExtraLargeBuyCount - previousInstantLargeBuyCount - previousInstantLargeSellCount + extraLargeSellSignals.length + extraLargeBuySignals.length + instantLargeSignals.length),
    byKind: {
      ...filteredByKind,
      intradayExtraLargeSell: { events: extraLargeSellSignals.length, stocks: new Set(extraLargeSellSignals.map((signal) => signal.ticker)).size },
      intradayExtraLargeBuy: { events: extraLargeBuySignals.length, stocks: new Set(extraLargeBuySignals.map((signal) => signal.ticker)).size },
      instantLargeBuy: { events: instantLargeSignals.filter((signal) => signal.kind === "instantLargeBuy").length, stocks: new Set(instantLargeSignals.filter((signal) => signal.kind === "instantLargeBuy").map((signal) => signal.ticker)).size },
      instantLargeSell: { events: instantLargeSignals.filter((signal) => signal.kind === "instantLargeSell").length, stocks: new Set(instantLargeSignals.filter((signal) => signal.kind === "instantLargeSell").map((signal) => signal.ticker)).size },
    },
  };
  return {
    signals,
    fourGateSignals,
    mainForceSignals,
    extraLargeSellSignals,
    extraLargeBuySignals,
    largeForceSignals,
    instantLargeSignals,
    dates,
    summary: filteredSummary,
  };
}

function storedSignalCount(payload: Awaited<ReturnType<typeof storedPayload>>) {
  return payload.signals.length + payload.fourGateSignals.length + payload.mainForceSignals.length + payload.extraLargeSellSignals.length + payload.extraLargeBuySignals.length + payload.largeForceSignals.length + payload.instantLargeSignals.length;
}

async function displayStoredPayload(query: string, limit: number, groupRankings?: MainForceGroupRankings) {
  const [dates, closedDates] = await Promise.all([readEarlySellDates(), loadTwseClosedTradingDates()]);
  const tradeDate = resolveIntradaySignalDisplayDate(dates, new Date(), closedDates);
  return { tradeDate, stored: await storedPayload(tradeDate, query, limit, groupRankings) };
}

async function fastStoredPayload(tradeDate: string, query: string, limit: number, dates: string[]) {
  const cacheKey = `${tradeDate}:${query}:${limit}`;
  const cached = storedSnapshotCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) return cached.payload;
  // 快照路径一次读回当日记录后在内存分类，不再为了六个页签执行六至八次 D1 查询。
  // 一次讀回完整交易日再依頁籤分類；不可讓全種類共用的 2,000 筆
  // 上限先吃掉早盤族群瞬間大單。
  const rawRows = await readEarlySellSignals({ tradeDate, query, limit: 10_000 });
  // Snapshot reads must never repair or write historical force bars.
  // Legacy unqualified rows are repaired by the full/history path.
  const qualifiedRows = rawRows.filter(isActiveIntradayCenterSignal).filter(hasQualifiedExtraLargeTriggerForce);
  const rows = qualifiedRows.flatMap((signal) => INSTANT_LARGE_SIGNAL_KINDS.includes(signal.kind)
    ? (normalizeInstantLargeOrderSignal(signal) ? [normalizeInstantLargeOrderSignal(signal) as EarlySellSignalRecord] : [])
    : [signal]);
  const byKind = Object.fromEntries([...new Set(rows.map((signal) => signal.kind))].map((kind) => {
    const kindRows = rows.filter((signal) => signal.kind === kind);
    return [kind, { events: kindRows.length, stocks: new Set(kindRows.map((signal) => signal.ticker)).size }];
  }));
  const summary = { total: rows.length, uniqueStocks: new Set(rows.map((signal) => signal.ticker)).size, byKind };
  const pick = (kinds: readonly EarlySellSignalRecord["kind"][]) => rows.filter((signal) => kinds.includes(signal.kind)).slice(0, limit);
  const rawSignals = pick(GENERAL_SIGNAL_KINDS);
  const rawFourGateSignals = pick(FOUR_GATE_SIGNAL_KINDS);
  const rawMainForceSignals = pick(MAIN_FORCE_SIGNAL_KINDS).filter((signal) => signal.note.includes(MAIN_FORCE_FILTER_MARKER));
  const rawExtraLargeSellSignals = pick(EXTRA_LARGE_SELL_SIGNAL_KINDS);
  const rawExtraLargeBuySignals = pick(EXTRA_LARGE_BUY_SIGNAL_KINDS);
  const rawLargeForceSignals = pick(LARGE_FORCE_SIGNAL_KINDS);
  const rawInstantLargeSignals = pick(INSTANT_LARGE_SIGNAL_KINDS);
  const captured = (rows: EarlySellSignalRecord[]) => rows.filter(hasCapturedMatchingTopGroup);
  const payload = {
    signals: captured(rawSignals.filter(passesPreviousEstimatedSellPressureFilter).filter(passesTodayImmediatePressureRatioFilter).map(formatTodayImmediateSignal)),
    fourGateSignals: captured(rawFourGateSignals),
    mainForceSignals: captured(rawMainForceSignals),
    extraLargeDiagnostics: {
      storedSell: rawRows.filter((row) => row.kind === "intradayExtraLargeSell").length,
      storedBuy: rawRows.filter((row) => row.kind === "intradayExtraLargeBuy").length,
      directionQualifiedSell: rawExtraLargeSellSignals.length,
      directionQualifiedBuy: rawExtraLargeBuySignals.length,
      groupQualifiedSell: captured(rawExtraLargeSellSignals).length,
      groupQualifiedBuy: captured(rawExtraLargeBuySignals).length,
    },
    extraLargeSellSignals: oneExtraLargeSignalPerTicker(captured(rawExtraLargeSellSignals)),
    extraLargeBuySignals: oneExtraLargeSignalPerTicker(captured(rawExtraLargeBuySignals)),
    largeForceSignals: rawLargeForceSignals,
    instantLargeSignals: captured(rawInstantLargeSignals),
    dates,
    summary,
  };
  storedSnapshotCache.set(cacheKey, { expiresAt: Date.now() + 5_000, payload });
  if (storedSnapshotCache.size > 20) storedSnapshotCache = new Map([...storedSnapshotCache].filter(([, item]) => item.expiresAt > Date.now()));
  return payload;
}

async function displayFastStoredPayload(query: string, limit: number) {
  const [dates, closedDates] = await Promise.all([readEarlySellDates(), loadTwseClosedTradingDates()]);
  const tradeDate = resolveIntradaySignalDisplayDate(dates, new Date(), closedDates);
  return { tradeDate, stored: await fastStoredPayload(tradeDate, query, limit, dates) };
}

type HubSignalSource = {
  base: string;
  payload: {
    tradeDate?: string;
    signals?: unknown;
    excludedTickers?: unknown[];
    [key: string]: unknown;
  };
  instantLargePayload: {
    signals?: unknown;
    collector?: unknown;
    [key: string]: unknown;
  };
};

async function loadInstantLargeCollectorStatus(timeout = 4_000) {
  const now = Date.now();
  if (instantLargeCollectorCache && instantLargeCollectorCache.expiresAt > now) {
    return instantLargeCollectorCache.payload;
  }
  const results = await Promise.allSettled(HUB_BASES.map(async (base) => {
    const url = new URL("/api/hub/intraday-large-orders", base);
    url.searchParams.set("limit", "1");
    const response = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Instant-Large-Status/1.0" },
      signal: AbortSignal.timeout(timeout),
    });
    if (!response.ok) throw new Error(`instant-large-status-${response.status}`);
    const payload = await response.json() as HubSignalSource["instantLargePayload"];
    if (!payload.collector || typeof payload.collector !== "object") throw new Error("instant-large-status-empty");
    return payload.collector;
  }));
  const collectors = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const collector = collectors.sort((left, right) => {
    const leftCount = Number((left as Record<string, unknown>).candidateCount) || 0;
    const rightCount = Number((right as Record<string, unknown>).candidateCount) || 0;
    return rightCount - leftCount;
  })[0];
  if (collector) {
    instantLargeCollectorCache = { expiresAt: now + 10_000, payload: collector };
    return collector;
  }
  return instantLargeCollectorCache?.payload ?? null;
}

async function loadHubSignalSource(base: string, timeout: number, groupRankings?: MainForceGroupRankings, signal?: AbortSignal): Promise<HubSignalSource> {
  const sourceUrl = new URL("/api/hub/daytrade-early-sell-signals", base);
  sourceUrl.searchParams.set("limit", "500");
  sourceUrl.searchParams.set("start", SIGNAL_WINDOW_START);
  sourceUrl.searchParams.set("end", SIGNAL_WINDOW_END);
  sourceUrl.searchParams.set("windowStart", SIGNAL_WINDOW_START);
  sourceUrl.searchParams.set("windowEnd", SIGNAL_WINDOW_END);
  sourceUrl.searchParams.set("interval", SIGNAL_GRANULARITY);
  sourceUrl.searchParams.set("granularity", SIGNAL_GRANULARITY);
  sourceUrl.searchParams.set("realtime", "1");
  const instantLargeUrl = new URL("/api/hub/intraday-large-orders", base);
  // 上游永久庫可能已有整日數千筆。500 筆只會涵蓋最後一小段，
  // 造成 09:00 起的訊號看似沒有保存。
  instantLargeUrl.searchParams.set("limit", "5000");
  const upstreamGroupHeaders = groupRankings
    ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(groupRankings)) }
    : {};
  const [sourceResult, instantLargeResult] = await Promise.allSettled([
    fetch(sourceUrl, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.0", ...upstreamGroupHeaders },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
    }),
    fetch(instantLargeUrl, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Instant-Large/1.0", ...upstreamGroupHeaders },
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
    }),
  ]);
  const response = sourceResult.status === "fulfilled" ? sourceResult.value : null;
  const instantLargeResponse = instantLargeResult.status === "fulfilled" ? instantLargeResult.value : null;
  if (!response?.ok && !instantLargeResponse?.ok) throw new Error("hub-signal-source-unavailable");
  return {
    base,
    payload: response?.ok
      ? await response.json() as HubSignalSource["payload"]
      : { tradeDate: taipeiTradeDate(), signals: [] },
    instantLargePayload: instantLargeResponse?.ok
      ? await instantLargeResponse.json() as HubSignalSource["instantLargePayload"]
      : { signals: [], collector: null },
  };
}

async function* loadFullHubSignalSources(groupRankings?: MainForceGroupRankings) {
  const emptySources: HubSignalSource[] = [];
  const sources = successfulSourcesInCompletionOrder(
    HUB_BASES.map(base => (signal: AbortSignal) => loadHubSignalSource(base, 6_000, groupRankings, signal)),
  );
  for await (const source of sources) {
    // A fast but empty backup must not hide another source's actual records.
    if (normalizeSignals(source.payload?.signals).length || normalizeSignals(source.instantLargePayload?.signals).length) yield source;
    else emptySources.push(source);
  }
  for (const source of emptySources) yield source;
}

let fullCollectionStartedAt = 0;
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const full = !['snapshot', 'fast', 'collector', 'date', 'collect'].some(key => params.has(key));
  if (!full) return handleRequest(request);
  if (fullCollectionStartedAt && Date.now() - fullCollectionStartedAt < 50_000) {
    const rawLimit = Number(params.get('limit') ?? 100);
    const limit = Math.max(1, Math.min(5000, Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 100));
    const {tradeDate, stored} = await displayFastStoredPayload(params.get('q')?.trim().slice(0, 50) ?? '', limit);
    return NextResponse.json({ok:true, tradeDate, ...stored, collectionBusy:true}, {headers:{'Cache-Control':'private, no-store'}});
  }
  const startedAt = Date.now();
  fullCollectionStartedAt = startedAt;
  try { return await handleRequest(request); }
  finally { if (fullCollectionStartedAt === startedAt) fullCollectionStartedAt = 0; }
}

async function handleRequest(request: NextRequest) {
  const rawLimit = Number(request.nextUrl.searchParams.get("limit") ?? 100);
  const limit = Math.max(1, Math.min(5000, Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 100));
  const requestedDate = request.nextUrl.searchParams.get("date")?.trim() ?? "";
  const query = request.nextUrl.searchParams.get("q")?.trim().slice(0, 50) ?? "";
  const fast = request.nextUrl.searchParams.get("fast") === "1";
  const snapshotOnly = request.nextUrl.searchParams.get("snapshot") === "1";
  const collectorOnly = request.nextUrl.searchParams.get("collector") === "1";
  const immediateCollect = request.nextUrl.searchParams.get("collect") === "immediate";
  const largeForceCollect = request.nextUrl.searchParams.get("collect") === "large-force";
  const extraLargeCollect = request.nextUrl.searchParams.get("collect") === "extra-large";
  const suppliedGroupRankings = clientGroupRankings(request);
  let fastStoredFallback: Awaited<ReturnType<typeof displayFastStoredPayload>> | null = null;

  if (collectorOnly) {
    const instantLargeCollector = await loadInstantLargeCollectorStatus().catch(() => instantLargeCollectorCache?.payload ?? null);
    return NextResponse.json({
      ok: Boolean(instantLargeCollector),
      tradeDate: taipeiTradeDate(),
      instantLargeCollector,
    }, { headers: { "Cache-Control": "private, no-store", "Server-Timing": "collector-status;desc=instant-large-only" } });
  }

  if (largeForceCollect) {
    const tradeDate = taipeiTradeDate();
    try {
      const result = await collectIntradayLargeForceBatch(tradeDate, suppliedGroupRankings);
      return NextResponse.json({
        ok: true,
        tradeDate,
        collected: true,
        saved: result.saved,
        patternSaved: result.patternSaved ?? 0,
        busy: result.busy,
        largeForceScan: result.progress,
      }, { headers: { "Cache-Control": "no-store", "Server-Timing": "collector;desc=full-market-large-force" } });
    } catch (error) {
      const progress = await readIntradayLargeForceScanProgress(tradeDate).catch(() => null);
      return NextResponse.json({
        ok: false,
        tradeDate,
        collected: false,
        largeForceScan: progress,
        message: error instanceof Error ? error.message : "盤中大戶力全市場回補失敗",
      }, { status: 503, headers: { "Cache-Control": "no-store" } });
    }
  }

  if (extraLargeCollect) {
    try {
      const dates = await readEarlySellDates();
      const closedDates = await loadTwseClosedTradingDates();
      const tradeDate = resolveIntradaySignalDisplayDate(dates, new Date(), closedDates);
      const groupRankings = suppliedGroupRankings ?? await loadSignalGroupRankings().catch(() => undefined);
      const completed = await backfillExtraLargeSellSignals(HUB_BASES[0], tradeDate, groupRankings);
      if (!completed) {
        return NextResponse.json({ ok: false, tradeDate, collected: false, message: "盤中特大買賣單回補尚未完成" }, { status: 503, headers: { "Cache-Control": "no-store" } });
      }
      const stored = await fastStoredPayload(tradeDate, query, limit, dates);
      return NextResponse.json({
        ok: true,
        tradeDate,
        collected: true,
        completed: completed.completed,
        diagnostics: completed,
        signalRevision: "category-detection-dated-history-v1",
        extraLargeSellSaved: stored.extraLargeSellSignals.length,
        extraLargeBuySaved: stored.extraLargeBuySignals.length,
      }, { headers: { "Cache-Control": "no-store", "Server-Timing": "collector;desc=extra-large-session-backfill" } });
    } catch {
      return NextResponse.json({ ok: false, collected: false, message: "盤中特大買賣單回補暫時無法完成" }, { status: 503, headers: { "Cache-Control": "no-store" } });
    }
  }

  if (requestedDate) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)) {
      return NextResponse.json({ ok: false, signals: [], message: "日期格式不正確" }, { status: 400 });
    }
    try {
      const stored = await storedPayload(requestedDate, query, limit);
      return NextResponse.json({ ok: true, tradeDate: requestedDate, history: true, ...stored }, { headers: { "Cache-Control": "no-store" } });
    } catch {
      return NextResponse.json({ ok: true, tradeDate: requestedDate, history: true, signals: [], dates: [], storageUnavailable: true }, { headers: { "Cache-Control": "no-store" } });
    }
  }

  if (snapshotOnly) {
    try {
      const { tradeDate, stored } = await displayFastStoredPayload(query, limit);
      return NextResponse.json({
        ok: true,
        tradeDate,
        carryingPreviousSession: tradeDate !== taipeiTradeDate(),
        window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
        snapshot: true,
        signalRevision: "category-detection-dated-history-v1",
        stale: storedSignalCount(stored) > 0,
        signalFeed: { polledAt: Date.now(), upstreamAt: 0, fallbackAt: 0, degraded: true, mode: "stored-snapshot" },
        ...stored,
      }, { headers: { "Cache-Control": "private, no-store", "Server-Timing": "snapshot;desc=stored-only" } });
    } catch {
      return NextResponse.json({ ok: true, tradeDate: taipeiTradeDate(), snapshot: true, signals: [], dates: [], storageUnavailable: true }, { headers: { "Cache-Control": "private, no-store" } });
    }
  }


  // 開盤大單使用獨立優先通道：不等待目前可能延遲 10 秒以上的逐筆 Hub，
  // 直接讀取會持續更新的當分鐘 1 分 K。達標後先寫入 D1，再由輕量快照
  // 回給所有電腦、手機與 iPad，畫面本身不必重算。
  if (immediateCollect) {
    const tradeDate = taipeiTradeDate();
    if (!isTaipeiIntradayCollectionWindow()) {
      return NextResponse.json({ ok: true, tradeDate, collected: false, reason: "outside-session" }, { headers: { "Cache-Control": "no-store" } });
    }
    const rankingPending = (suppliedGroupRankings
      ? Promise.resolve(suppliedGroupRankings)
      : loadSignalGroupRankings()).catch(() => undefined);
    let calculated: Awaited<ReturnType<typeof calculateLiveImmediateFallback>> | null = null;
    let source = "";
    for (const base of HUB_BASES) {
      try {
        calculated = await calculateLiveImmediateFallback(base, tradeDate);
        source = base;
        break;
      } catch {
        // 主站短暫失聯時立即改用正式備援站。
      }
    }
    if (!calculated) {
      return NextResponse.json({ ok: false, tradeDate, collected: false, latestBarAt: 0, reason: "minute-feed-unavailable", message: "行情來源尚未回傳今日分鐘資料，訊號採集持續重試" }, { status: 503, headers: { "Cache-Control": "no-store" } });
    }
    let signals = calculated.signals;
    let fourGateSignals = calculated.fourGateSignals;
    try {
      const rankings = await rankingPending;
      if (rankings) {
        signals = annotateMainForceGroupRanks(signals, signalGroupsByTicker, rankings)
          .filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, rankings));
        fourGateSignals = annotateMainForceGroupRanks(fourGateSignals, signalGroupsByTicker, rankings)
          .filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, rankings));
      } else {
        signals = [];
        fourGateSignals = [];
      }
    } catch {
      // 無法確認前十強弱族群時不寫入，避免未經族群濾網的股票混入盤中中心。
      signals = [];
      fourGateSignals = [];
    }
    if (signals.length || fourGateSignals.length) {
      [signals, fourGateSignals] = await Promise.all([
        annotatePreviousChipRanks(signals, tradeDate),
        annotatePreviousChipRanks(fourGateSignals, tradeDate),
      ]);
      await saveEarlySellSignals([...signals, ...fourGateSignals]);
      storedSnapshotCache.clear();
    }
    return NextResponse.json({
      ok: true,
      tradeDate,
      collected: true,
      source,
      latestBarAt: calculated.latestBarAt,
      scanned: calculated.scanned,
      saved: signals.length,
      fourGateSaved: fourGateSignals.length,
    }, { headers: { "Cache-Control": "no-store", "Server-Timing": "collector;desc=partial-minute-bars" } });
  }

  // 午夜後到下一個交易日 08:45，以及整個週末，都直接沿用最近
  // 一個有永久紀錄的交易日；不再讓空的即時來源覆蓋既有訊號。
  try {
    // 手機每 5 秒輪詢的 fast 路徑不能在正式快速查詢前再執行一次
    // 完整籌碼標註；否則冷啟動或上游稍慢時會先耗盡整個請求時間。
    const initialStored = fast
      ? (fastStoredFallback = await displayFastStoredPayload(query, limit))
      : await displayStoredPayload(query, limit);
    const { tradeDate } = initialStored;
    if (tradeDate !== taipeiTradeDate()) {
      if (fast) {
        return NextResponse.json({
          ok: true,
          tradeDate,
          carryingPreviousSession: true,
          window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
          fast: true,
          stale: storedSignalCount(initialStored.stored) > 0,
          ...initialStored.stored,
        }, { headers: { "Cache-Control": "no-store" } });
      }
      const groupRankingResult = suppliedGroupRankings
        ? [{ status: "fulfilled", value: suppliedGroupRankings } as const]
        : await Promise.allSettled([loadSignalGroupRankings()]);
      const groupRankings = groupRankingResult[0].status === "fulfilled" ? groupRankingResult[0].value : undefined;
      await backfillExtraLargeSellSignals(HUB_BASES[0], tradeDate, groupRankings);
      const carriedStored = await storedPayload(tradeDate, query, limit, groupRankings);
      return NextResponse.json({
        ok: true,
        tradeDate,
        carryingPreviousSession: true,
        window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
        stale: storedSignalCount(carriedStored) > 0,
        ...carriedStored,
      }, { headers: { "Cache-Control": "no-store" } });
    }
    if (fast) {
      return NextResponse.json({
        ok: true,
        tradeDate,
        carryingPreviousSession: false,
        window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
        fast: true,
        stale: false,
        signalFeed: {
          polledAt: Date.now(), upstreamAt: 0, fallbackAt: 0,
          degraded: false, mode: "stored-live-snapshot",
        },
        ...initialStored.stored,
      }, { headers: { "Cache-Control": "private, no-store", "Server-Timing": "snapshot;desc=d1-only" } });
    }
  } catch {
    // 永久儲存暫時不可用時才繼續嘗試正式即時來源。
  }

  // 手機快速輪詢同時詢問主站與備援，避免主站失聯時每次都先白等。
  // 三秒內都沒有回覆便直接顯示 D1 永久資料，不讓單次掛起鎖死後續輪詢。
  const fastSourceResults = fast
    ? await Promise.allSettled(HUB_BASES.map((base) => loadHubSignalSource(base, 3_000, suppliedGroupRankings)))
    : [];
  const fastSourceCandidates = fastSourceResults.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const sourceCandidates = fast ? fastSourceCandidates : loadFullHubSignalSources(suppliedGroupRankings);

  if (fast && fastSourceCandidates.length === 0 && fastStoredFallback) {
    return NextResponse.json({
      ok: true,
      tradeDate: fastStoredFallback.tradeDate,
      carryingPreviousSession: fastStoredFallback.tradeDate !== taipeiTradeDate(),
      window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
      fast: true,
      stale: storedSignalCount(fastStoredFallback.stored) > 0,
      upstreamUnavailable: true,
      ...fastStoredFallback.stored,
    }, { headers: { "Cache-Control": "no-store" } });
  }

  for await (const candidate of sourceCandidates) {
    try {
      const { base, payload, instantLargePayload } = candidate;
      const pressureAmountEligibleSignals = normalizeSignals(payload?.signals).filter(passesPreviousEstimatedSellPressureFilter);
      const normalizedSourceSignals = normalizeSignals(payload?.signals);
      const directInstantLargeSignals = normalizeSignals(instantLargePayload?.signals).filter((signal) => INSTANT_LARGE_SIGNAL_KINDS.includes(signal.kind));
      let instantLargeSignals = directInstantLargeSignals.length
        ? directInstantLargeSignals
        : normalizedSourceSignals.filter((signal) => INSTANT_LARGE_SIGNAL_KINDS.includes(signal.kind));
      instantLargeSignals = await qualifyIncomingInstantLargeSignals(instantLargeSignals);
      let liveSignals = pressureAmountEligibleSignals
        .filter(passesTodayImmediatePressureRatioFilter)
        .map(formatTodayImmediateSignal);
      let fourGateSignals: EarlySellSignalRecord[] = [];
      let mainForceSignals: EarlySellSignalRecord[] = [];
      let extraLargeSellSignals: EarlySellSignalRecord[] = [];
      let extraLargeBuySignals: EarlySellSignalRecord[] = [];
      let largeForceSignals: EarlySellSignalRecord[] = [];
      const liveTradeDate = String(liveSignals[0]?.tradeDate ?? payload?.tradeDate ?? taipeiTradeDate());
      const upstreamFeedAt = signalFeedTimestamp(payload, instantLargePayload);
      const feedDegraded = shouldUseMinuteBarFallback(liveTradeDate, upstreamFeedAt);

      if (fast) {
        const groupRankings = suppliedGroupRankings;
        if (groupRankings) {
          liveSignals = annotateMainForceGroupRanks(liveSignals, signalGroupsByTicker, groupRankings)
            .filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
          instantLargeSignals = annotateMainForceGroupRanks(instantLargeSignals, signalGroupsByTicker, groupRankings)
            .filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        } else {
          liveSignals = liveSignals.filter(hasCapturedMatchingTopGroup);
          instantLargeSignals = instantLargeSignals.filter(hasCapturedMatchingTopGroup);
        }
        const sourceTradeDate = String(instantLargeSignals[0]?.tradeDate ?? liveSignals[0]?.tradeDate ?? payload?.tradeDate ?? liveTradeDate);
        await saveEarlySellSignals([...liveSignals, ...instantLargeSignals]);
        storedSnapshotCache.clear();
        const { tradeDate, stored } = await displayFastStoredPayload(query, limit);
        const latestSourceSignalAt = Math.max(0, ...liveSignals.map((signal) => signal.barTs), ...instantLargeSignals.map((signal) => signal.barTs));
        return NextResponse.json({
          ok: true,
          tradeDate,
          sourceTradeDate,
          carryingPreviousSession: tradeDate !== taipeiTradeDate(),
          window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
          fast: true,
          latestSourceSignalAt,
          instantLargeCollector: instantLargePayload?.collector ?? null,
          signalFeed: {
            polledAt: Date.now(),
            upstreamAt: upstreamFeedAt,
            fallbackAt: 0,
            degraded: feedDegraded,
            mode: feedDegraded ? "minute-bars-fallback-pending" : "live",
          },
          ...stored,
        }, { headers: { "Cache-Control": "no-store" } });
      }

      // Publish already qualified direct events before slower derived strategies.
      // All original direction/group filters still apply before saving.
      const earlyRankings = suppliedGroupRankings ?? await loadSignalGroupRankings().catch(() => undefined);
      if (earlyRankings) {
        const earlyExcluded = new Set(Array.isArray(payload?.excludedTickers) ? payload.excludedTickers.map((ticker: unknown) => String(ticker).trim().toUpperCase()) : []);
        const directReady = annotateMainForceGroupRanks([...liveSignals, ...instantLargeSignals], signalGroupsByTicker, earlyRankings)
          .filter(signal => !earlyExcluded.has(signal.ticker.toUpperCase()) && hasMatchingTopGroup(signal, signalGroupsByTicker, earlyRankings));
        if (directReady.length) {
          await saveEarlySellSignals(directReady);
          storedSnapshotCache.clear();
        }
      }

      const immediateFallbackPromise = feedDegraded
        ? calculateLiveImmediateFallback(base, liveTradeDate)
        : Promise.resolve({ signals: [], fourGateSourceSignals: [], fourGateSignals: [], latestBarAt: 0, scanned: 0 });
      const derivedSignalsPromise = immediateFallbackPromise.then(
        (fallback) => calculateLiveDerivedSignals(base, [...pressureAmountEligibleSignals, ...fallback.fourGateSourceSignals], liveTradeDate),
        () => calculateLiveDerivedSignals(base, pressureAmountEligibleSignals, liveTradeDate),
      );
      const [derivedResult, mainForceResult, groupRankingResult, immediateFallbackResult] = await Promise.allSettled([
        derivedSignalsPromise,
        calculateLiveMainForceZeroSignals(base),
        earlyRankings ? Promise.resolve(earlyRankings) : loadSignalGroupRankings(),
        immediateFallbackPromise,
      ]);
      if (immediateFallbackResult.status === "fulfilled" && immediateFallbackResult.value.signals.length) {
        liveSignals = mergeImmediateFallback(liveSignals, immediateFallbackResult.value.signals);
      }
      if (derivedResult.status === "fulfilled") {
        fourGateSignals = derivedResult.value.fourGateSignals;
        extraLargeSellSignals = derivedResult.value.extraLargeSellSignals;
        extraLargeBuySignals = derivedResult.value.extraLargeBuySignals;
      }
      const groupRankings = groupRankingResult.status === "fulfilled" ? groupRankingResult.value : undefined;
      if (groupRankingResult.status === "rejected") {
        console.warn("[daytrade-early-sell] group rank annotation unavailable", groupRankingResult.reason);
      }
      if (mainForceResult.status === "fulfilled") {
        mainForceSignals = mainForceResult.value.mainForceSignals;
        largeForceSignals = mainForceResult.value.largeForceSignals;
      }
      if (groupRankings) {
        liveSignals = annotateMainForceGroupRanks(liveSignals, signalGroupsByTicker, groupRankings);
        fourGateSignals = annotateMainForceGroupRanks(fourGateSignals, signalGroupsByTicker, groupRankings);
        mainForceSignals = annotateMainForceGroupRanks(mainForceSignals, signalGroupsByTicker, groupRankings);
        extraLargeSellSignals = annotateMainForceGroupRanks(extraLargeSellSignals, signalGroupsByTicker, groupRankings);
        extraLargeBuySignals = annotateMainForceGroupRanks(extraLargeBuySignals, signalGroupsByTicker, groupRankings);
        instantLargeSignals = annotateMainForceGroupRanks(instantLargeSignals, signalGroupsByTicker, groupRankings);
        // 全部盤中訊號只留族群同方向前十，沒有標籤或標籤方向相反的一律不顯示。
        liveSignals = liveSignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        fourGateSignals = fourGateSignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        mainForceSignals = mainForceSignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        extraLargeSellSignals = extraLargeSellSignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        extraLargeBuySignals = extraLargeBuySignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
        instantLargeSignals = instantLargeSignals.filter((signal) => hasMatchingTopGroup(signal, signalGroupsByTicker, groupRankings));
      }
      const signalTradeDate = liveSignals[0]?.tradeDate ?? fourGateSignals[0]?.tradeDate ?? mainForceSignals[0]?.tradeDate ?? extraLargeSellSignals[0]?.tradeDate ?? extraLargeBuySignals[0]?.tradeDate ?? largeForceSignals[0]?.tradeDate ?? instantLargeSignals[0]?.tradeDate ?? liveTradeDate;
      if (signalTradeDate) {
        [liveSignals, fourGateSignals, mainForceSignals, extraLargeSellSignals, extraLargeBuySignals, instantLargeSignals] = await Promise.all([
          annotatePreviousChipRanks(liveSignals, signalTradeDate),
          annotatePreviousChipRanks(fourGateSignals, signalTradeDate),
          annotatePreviousChipRanks(mainForceSignals, signalTradeDate),
          annotatePreviousChipRanks(extraLargeSellSignals, signalTradeDate),
          annotatePreviousChipRanks(extraLargeBuySignals, signalTradeDate),
          annotatePreviousChipRanks(instantLargeSignals, signalTradeDate),
        ]);
      }
      const sourceTradeDate = String(mainForceSignals[0]?.tradeDate ?? extraLargeSellSignals[0]?.tradeDate ?? extraLargeBuySignals[0]?.tradeDate ?? largeForceSignals[0]?.tradeDate ?? instantLargeSignals[0]?.tradeDate ?? payload?.tradeDate ?? liveSignals[0]?.tradeDate ?? "");
      const excludedTickers = Array.isArray(payload?.excludedTickers)
        ? payload.excludedTickers.map((ticker: unknown) => String(ticker).trim().toUpperCase()).filter((ticker: string) => /^[0-9A-Z]{2,12}$/.test(ticker))
        : [];
      try {
        await saveEarlySellSignals([...liveSignals, ...fourGateSignals, ...mainForceSignals, ...extraLargeSellSignals, ...extraLargeBuySignals]);
        await saveEarlySellSignals(largeForceSignals);
        await saveEarlySellSignals(instantLargeSignals);
        storedSnapshotCache.clear();
        if (sourceTradeDate) await deleteEarlySellSignalsForTickers(sourceTradeDate, excludedTickers);
        const { tradeDate, stored } = await displayStoredPayload(query, limit, groupRankings);
        return NextResponse.json({
          ok: true,
          ...payload,
          tradeDate,
          sourceTradeDate,
          carryingPreviousSession: tradeDate !== taipeiTradeDate(),
          window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
          mainForceScan: mainForceScanStatus,
          signalFeed: {
            polledAt: Date.now(),
            upstreamAt: upstreamFeedAt,
            fallbackAt: immediateFallbackResult.status === "fulfilled" ? immediateFallbackResult.value.latestBarAt : 0,
            degraded: feedDegraded,
            mode: feedDegraded && immediateFallbackResult.status === "fulfilled" && immediateFallbackResult.value.latestBarAt > upstreamFeedAt
              ? "minute-bars-fallback"
              : feedDegraded ? "upstream-delayed" : "live",
            scanned: immediateFallbackResult.status === "fulfilled" ? immediateFallbackResult.value.scanned : 0,
          },
          groupRankings,
          ...stored,
        }, { headers: { "Cache-Control": "no-store" } });
      } catch {
        // 本機預覽沒有 D1 時仍保留即時通知；部署後會自動使用 D1 永久保存。
      }
      return NextResponse.json({
        ok: true,
        ...payload,
        window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
        signals: liveSignals.filter(isActiveIntradayCenterSignal).sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        fourGateSignals: fourGateSignals.slice(0, limit),
        mainForceSignals: mainForceSignals.sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        extraLargeSellSignals: extraLargeSellSignals.sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        extraLargeBuySignals: extraLargeBuySignals.sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        largeForceSignals: largeForceSignals.sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        instantLargeSignals: instantLargeSignals.sort((left, right) => right.barTs - left.barTs).slice(0, limit),
        mainForceScan: mainForceScanStatus,
        groupRankings,
        dates: sourceTradeDate ? [sourceTradeDate] : [],
        storageUnavailable: true,
      }, { headers: { "Cache-Control": "no-store" } });
    } catch {
      // 改試下一個正式來源。
    }
  }
  try {
    // 最後備援必須是純 D1 查詢；不可再從這裡呼叫已失聯的即時後台。
    const { tradeDate, stored } = fastStoredFallback ?? await displayFastStoredPayload(query, limit);
    return NextResponse.json({
      ok: true,
      tradeDate,
      carryingPreviousSession: tradeDate !== taipeiTradeDate(),
      window: `${SIGNAL_WINDOW_START}-${SIGNAL_WINDOW_END}`,
      stale: storedSignalCount(stored) > 0,
      ...stored,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    // 即時來源與 D1 都暫時無法使用時才回傳服務錯誤。
  }
  return NextResponse.json({ ok: false, signals: [], message: "盤中大單買進／賣出訊號暫時無法取得" }, { status: 503, headers: { "Cache-Control": "no-store" } });
}
