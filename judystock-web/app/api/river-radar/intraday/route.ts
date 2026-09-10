import { NextRequest } from "next/server";
import { fetchBoundedMarketJson, mapMarketBatches } from "../../../../lib/bounded-market-data";
import stockGroupsSource from "../../../../data/stock_groups.py?raw";
import { GET as getTechnicalMarket } from "../../technical-market/route";
import { GET as getDispositionRisk } from "../../disposition-risk/route";
import { GET as getFocusRanking } from "../../focus-ranking/route";
import { DAILY_STRATEGY_CATALOG, detectIntradayDailyStrategies, detectIntradayMa20Cross, isDailyStrategyHistoryCoverageWindow, isDailyStrategySignalWindow, isListedOrOtcStockCode, type DailyStrategyCandle, type DailyStrategyMatch } from "../../../../lib/daily-strategy-signals";
import { aggregateRiverFiveMinuteBars, latestRiverSelectionWithMa20Filters, scoreRiverFiveMinuteBars, type RiverMa20SelectionMatch, type RiverMinuteBar } from "../../../../lib/river-intraday";
import { selectRankedRiverGroupCandidates } from "../../../../lib/river-group-selection";
import { riverScanBatch } from "../../../../lib/river-scan-batch";
import { passesDailyStrategyDirectionChange } from "../../../../lib/daily-strategy-universe";
import { matchesPersistedOfficialPrimaryGroup } from "../../../../lib/persisted-signal-validation";
import { loadTwseClosedTradingDates, resolveIntradaySignalCutoverDate } from "../../../../lib/intraday-signal-session";
import { parseStockPrimaryGroupMap, type StockPrimaryGroupMap } from "../../../../lib/stock-primary-group";
import { acquireRiverIntradayScanLease, readRiverIntradayScanState, readRiverSignals, readRiverStrategySignals, saveRiverIntraday, saveRiverIntradayScanState, saveRiverStrategySignals, type RiverIntradayScanState, type RiverIntradaySignal, type RiverIntradaySnapshot, type RiverStrategySignal } from "../../../../db/river-radar-intraday";

type TechnicalRow = {
  code: string;
  name?: string;
  market: "twse" | "tpex";
  date?: string;
  close?: number;
  changePct?: number;
  technicalReady: boolean;
  riverScore: number | null;
  riverSide: "bull" | "neutral" | "bear" | null;
  maLiveBaseSums: Record<number, number | null>;
};
type TechnicalPayload = { rows?: TechnicalRow[]; dataDate?: string };
type BatchPayload = { data?: Record<string, RiverMinuteBar[]> };
type CandleEnvelope = { result?: { data?: { json?: { candles?: Array<Record<string, unknown>> } } } };
type FiveMinuteHistoryCandle = { date: string; close: number };
type DispositionPayload = { dispositions?: Array<{ code?: string; status?: string }> };
type ConfiguredGroup = { name?: string; members?: Array<{ code?: string; name?: string }> };
type FocusRankingPayload = { rankings?: { strong?: { signalGroups?: Array<{ name?: string }> }; weak?: { signalGroups?: Array<{ name?: string }> } } };

const HUB_BASES = ["https://hanstock-production.up.railway.app"];
const CANDLE_BATCH_SIZE = 8;
const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF"]);
const MINIMUM_CONFIGURED_GROUP_COVERAGE = 60;
const RIVER_GROUP_SELECTION_VERSION = "focus-ranking-67-primary-group-change-top6-daily-and-5m-ma20-live-v10";
const DAILY_STRATEGY_GROUP_SELECTION_VERSION = "focus-ranking-67-primary-group-daily-strategy-top10-change7-after1100-v4";
const COMPATIBLE_RIVER_GROUP_SELECTION_VERSIONS = new Set([
  RIVER_GROUP_SELECTION_VERSION,
  "focus-ranking-67-primary-group-change-top6-daily-and-5m-ma20-v9",
  "focus-ranking-67-primary-group-ma-top3-daily-and-5m-ma20-v8",
  "focus-ranking-67-ma-top3-daily-and-5m-ma20-v7",
]);
const COMPATIBLE_DAILY_STRATEGY_GROUP_SELECTION_VERSIONS = new Set([
  DAILY_STRATEGY_GROUP_SELECTION_VERSION,
  "focus-ranking-67-primary-group-daily-strategy-top10-change7-v3",
  "focus-ranking-67-daily-strategy-top10-change7-v2",
  "focus-ranking-67-daily-strategy-top10-v1",
]);
const OFFICIAL_PRIMARY_GROUP_BY_CODE = parseStockPrimaryGroupMap(stockGroupsSource, EXCLUDED_GROUPS);
let cache: { expiresAt: number; payload: unknown } | null = null;
let dailyHistoryCache = new Map<string, { expiresAt: number; rows: DailyStrategyCandle[] }>();
let fiveMinuteHistoryCache = new Map<string, { expiresAt: number; rows: FiveMinuteHistoryCandle[] }>();
let dispositionCache: { expiresAt: number; codes: Set<string> } | null = null;

function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function taipeiDate(timestamp: number) { const date = new Date(timestamp + 8 * 60 * 60_000); return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`; }
async function selectedSignalDate() {
  const closedDates = await loadTwseClosedTradingDates();
  return resolveIntradaySignalCutoverDate(new Date(), closedDates).replaceAll("-", "/");
}
function sessionProgress(timestamp: number) { const taipei = new Date(timestamp + 8 * 60 * 60_000); const elapsed = taipei.getUTCHours() * 60 + taipei.getUTCMinutes() - 9 * 60 + 1; return Math.min(1, Math.max(.08, elapsed / 270)); }
function isDailyStrategyScanWindow(timestamp = Date.now()) {
  const taipei = new Date(timestamp + 8 * 60 * 60_000);
  const weekday = taipei.getUTCDay();
  const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
  return weekday >= 1 && weekday <= 5 && minutes >= 11 * 60 && minutes <= 14 * 60 + 30;
}

async function loadRiverGroups() {
  const response = await fetch("https://www.hanstock.xyz/api/trpc/stocks.groups", { cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-River-Groups/1.0" }, signal: AbortSignal.timeout(18_000) });
  if (!response.ok) throw new Error(`river-groups-${response.status}`);
  const payload = await response.json() as { result?: { data?: { json?: ConfiguredGroup[] } } };
  const groups = new Map<string, Array<{ code: string; name: string }>>();
  for (const row of payload.result?.data?.json ?? []) {
    const name = String(row.name ?? "").trim();
    if (!name || EXCLUDED_GROUPS.has(name)) continue;
    const members = (row.members ?? []).flatMap((member) => {
      const code = String(member.code ?? "").trim(); const stockName = String(member.name ?? "").trim().replace(/\*$/, "");
      return /^\d{4}$/.test(code) && !code.startsWith("00") && stockName ? [{ code, name: stockName }] : [];
    });
    if (members.length) groups.set(name, members);
  }
  // 與 67 族群排行共用同一份正式設定；盤後少一群時仍須繼續選股，
  // 不能讓均線多、空整批歸零。只有來源嚴重缺漏時才停止。
  if (groups.size < MINIMUM_CONFIGURED_GROUP_COVERAGE) throw new Error(`river-groups-incomplete-${groups.size}`);
  return groups;
}

async function loadFocusGroupOrder(origin: string) {
  const response = await getFocusRanking(new Request(new URL("/api/focus-ranking?direction=both", origin)));
  const payload = await response.json() as FocusRankingPayload;
  const bull = (payload.rankings?.strong?.signalGroups ?? []).map((group) => String(group.name ?? "").trim()).filter(Boolean).slice(0, 10);
  const bear = (payload.rankings?.weak?.signalGroups ?? []).map((group) => String(group.name ?? "").trim()).filter(Boolean).slice(0, 10);
  if (bull.length !== 10 || bear.length !== 10) throw new Error(`focus-group-order-incomplete-${bull.length}-${bear.length}`);
  return { bull, bear };
}

function isOfficialRankedGroupSignal(signal: RiverIntradaySignal, primaryGroups: StockPrimaryGroupMap) {
  const direction = signal.direction;
  const officialPrimaryGroup = primaryGroups.get(signal.code);
  return signal.signalType === "river"
    && signal.selectionSource === "focus-ranking-67"
    && COMPATIBLE_RIVER_GROUP_SELECTION_VERSIONS.has(String(signal.selectionRuleVersion))
    && passesDailyStrategyDirectionChange(direction, signal.changePct)
    // 族群排名要採用訊號成立當下已保存的名次。中午排名變動只能
    // 影響之後的新訊號，不能把早上已成立的訊號從當日紀錄刪除。
    && Number(signal.groupRank) >= 1 && Number(signal.groupRank) <= 10
    // The signal was already checked against the official primary group before
    // it was written. A production isolate can briefly start without the raw
    // group map populated; that must not erase every permanent signal on read.
    && matchesPersistedOfficialPrimaryGroup(officialPrimaryGroup, signal.groupName)
    && Number(signal.stockRank) >= 1 && Number(signal.stockRank) <= 6;
}

function isOfficialRankedStrategySignal(signal: RiverStrategySignal, primaryGroups: StockPrimaryGroupMap) {
  const direction = signal.direction;
  const officialPrimaryGroup = primaryGroups.get(signal.code);
  return signal.signalType === "daily-strategy"
    && isDailyStrategySignalWindow(Number(signal.barTs))
    && signal.selectionSource === "focus-ranking-67"
    && COMPATIBLE_DAILY_STRATEGY_GROUP_SELECTION_VERSIONS.has(String(signal.selectionRuleVersion))
    && passesDailyStrategyDirectionChange(direction, signal.changePct)
    && Number(signal.groupRank) >= 1 && Number(signal.groupRank) <= 10
    && matchesPersistedOfficialPrimaryGroup(officialPrimaryGroup, signal.groupName);
}

function rankedGroupMembers(groups: Map<string, Array<{ code: string; name: string }>>, groupOrder: string[], primaryGroups: StockPrimaryGroupMap) {
  const matches = new Map<string, { groupName: string; groupRank: number }>();
  groupOrder.slice(0, 10).forEach((groupName, index) => {
    for (const member of groups.get(groupName) ?? []) {
      if (primaryGroups.get(member.code) !== groupName) continue;
      if (!matches.has(member.code)) matches.set(member.code, { groupName, groupRank: index + 1 });
    }
  });
  return matches;
}

function accumulatedOfficialRiverSignals(signals: RiverIntradaySignal[], primaryGroups: StockPrimaryGroupMap) {
  const accumulated = new Map<string, RiverIntradaySignal>();
  signals
    .filter((signal) => isOfficialRankedGroupSignal(signal, primaryGroups))
    .sort((left, right) => left.barTs - right.barTs)
    .forEach((signal) => {
      const key = `${signal.tradeDate}:${signal.code}:${signal.direction}`;
      if (!accumulated.has(key)) accumulated.set(key, signal);
    });
  return [...accumulated.values()].sort((left, right) => right.barTs - left.barTs);
}

function accumulatedOfficialStrategySignals(
  signals: RiverStrategySignal[],
  primaryGroups: StockPrimaryGroupMap,
  riverSignals: RiverIntradaySignal[],
) {
  // 第一代日線策略紀錄尚未寫入主族群、族群名次與漲跌幅。
  // 同一檔、同方向的均線訊號已有當時保存的正式族群快照，僅用它補齊
  // 缺欄位；原本已寫入的族群不覆蓋，避免把舊的重疊族群誤改成合格。
  const riverContext = new Map(accumulatedOfficialRiverSignals(riverSignals, primaryGroups).map((signal) => [
    `${signal.tradeDate}:${signal.code}:${signal.direction}`,
    signal,
  ]));
  const accumulated = new Map<string, RiverStrategySignal>();
  signals
    .filter((signal) => String(signal.strategyKind) !== "motherChild")
    .map((signal) => {
      const companion = riverContext.get(`${signal.tradeDate}:${signal.code}:${signal.direction}`);
      if (!companion) return signal;
      return {
        ...signal,
        selectionSource: signal.selectionSource ?? companion.selectionSource,
        selectionRuleVersion: signal.selectionRuleVersion ?? "focus-ranking-67-daily-strategy-top10-v1",
        groupName: signal.groupName ?? companion.groupName,
        groupRank: signal.groupRank ?? companion.groupRank,
        changePct: Number.isFinite(Number(signal.changePct)) ? signal.changePct : companion.changePct,
      };
    })
    .filter((signal) => isListedOrOtcStockCode(signal.code) && isOfficialRankedStrategySignal(signal, primaryGroups))
    .sort((left, right) => left.barTs - right.barTs)
    .forEach((signal) => {
      const key = `${signal.tradeDate}:${signal.code}:${signal.strategyKind}`;
      if (!accumulated.has(key)) accumulated.set(key, signal);
    });
  return [...accumulated.values()].sort((left, right) => right.barTs - left.barTs);
}

function normalizeDailyCandle(raw: Record<string, unknown>) {
  const date = String(raw.date ?? raw.time ?? "").slice(0, 10).replaceAll("-", "/");
  const open = number(raw.open); const high = number(raw.high); const low = number(raw.low); const close = number(raw.close); const volume = number(raw.volume);
  if (!/^\d{4}\/\d{2}\/\d{2}$/.test(date) || open === null || high === null || low === null || close === null || volume === null) return null;
  return { date, open, high, low, close, volume, ma5: number(raw.ma5), ma20: number(raw.ma20) } satisfies DailyStrategyCandle;
}

function normalizeFiveMinuteHistoryCandle(raw: Record<string, unknown>) {
  const date = String(raw.date ?? raw.time ?? "").trim();
  const close = number(raw.close);
  if (!/^\d{2}\/\d{2} \d{2}:\d{2}$/.test(date) || close === null) return null;
  return { date, close } satisfies FiveMinuteHistoryCandle;
}

function normalizeIntradayCandle(raw: Record<string, unknown>) {
  const close = number(raw.close); const volume = number(raw.volume);
  const open = number(raw.open) ?? close; const high = number(raw.high) ?? close; const low = number(raw.low) ?? close;
  const rawTimestamp = number(raw.ts ?? raw.timestamp);
  let ts = rawTimestamp === null ? null : rawTimestamp < 10_000_000_000 ? rawTimestamp * 1_000 : rawTimestamp;
  if (ts === null) {
    const date = String(raw.date ?? raw.time ?? "").trim();
    const short = date.match(/^(\d{2})\/(\d{2}) (\d{2}):(\d{2})$/);
    const full = date.match(/^(\d{4})[-\/](\d{2})[-\/](\d{2})[ T](\d{2}):(\d{2})/);
    if (short) {
      const taipei = new Date(Date.now() + 8 * 60 * 60_000);
      ts = Date.UTC(taipei.getUTCFullYear(), Number(short[1]) - 1, Number(short[2]), Number(short[3]) - 8, Number(short[4]));
    } else if (full) {
      ts = Date.UTC(Number(full[1]), Number(full[2]) - 1, Number(full[3]), Number(full[4]) - 8, Number(full[5]));
    }
  }
  if (ts === null || close === null || volume === null || open === null || high === null || low === null) return null;
  return { ts, open, high, low, close, volume } satisfies RiverMinuteBar;
}

async function fetchIntradayCandleBatch(codes: string[]) {
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "5m" } }]));
  const payload = await fetchBoundedMarketJson<CandleEnvelope[]>(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, () => ({ cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-River-Radar-Fallback/1.0" }, signal: AbortSignal.timeout(18_000) }));
  return new Map(codes.flatMap((code, index) => {
    const rows = (payload[index]?.result?.data?.json?.candles ?? []).flatMap((raw) => {
      const row = normalizeIntradayCandle(raw); return row ? [row] : [];
    });
    return rows.length ? [[code, rows] as const] : [];
  }));
}

async function loadBatchBars(codes: string[]) {
  const batches = Array.from({ length: Math.ceil(codes.length / 40) }, (_, index) => codes.slice(index * 40, index * 40 + 40));
  const merged = new Map<string, RiverMinuteBar[]>();
  for (const base of HUB_BASES) {
    try {
      const settled = await mapMarketBatches(batches, (items) => fetchBoundedMarketJson<BatchPayload>(new URL("/api/hub/bars1m/batch", base), () => ({ method: "POST", cache: "no-store", headers: { Accept: "application/json", "Content-Type": "application/json", "User-Agent": "HanStock-River-Radar/1.1" }, body: JSON.stringify({ codes: items }), signal: AbortSignal.timeout(6_000) })));
      settled.forEach((item) => { if (item.status === "fulfilled") Object.entries(item.value.data ?? {}).forEach(([code, rows]) => {
        if (!merged.has(code) && Array.isArray(rows) && rows.length > 0) merged.set(code, rows);
      }); });
      if (merged.size === codes.length) return merged;
    } catch { /* use backup hub */ }
  }
  const missing = codes.filter((code) => !merged.has(code));
  const fallbackBatches = Array.from({ length: Math.ceil(missing.length / CANDLE_BATCH_SIZE) }, (_, index) => missing.slice(index * CANDLE_BATCH_SIZE, index * CANDLE_BATCH_SIZE + CANDLE_BATCH_SIZE));
  const fallback = await mapMarketBatches(fallbackBatches, fetchIntradayCandleBatch);
  fallback.forEach((item) => {
    if (item.status === "fulfilled") item.value.forEach((rows, code) => { if (rows.length > 0) merged.set(code, rows); });
  });
  return merged;
}

async function fetchDailyHistoryBatch(codes: string[]) {
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "1d" } }]));
  const payload = await fetchBoundedMarketJson<CandleEnvelope[]>(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, () => ({ cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-Intraday-Daily-Strategies/1.0" }, signal: AbortSignal.timeout(12_000) }));
  return new Map(codes.flatMap((code, index) => {
    const rows = (payload[index]?.result?.data?.json?.candles ?? []).flatMap((raw) => { const row = normalizeDailyCandle(raw); return row ? [row] : []; }).sort((left, right) => String(left.date).localeCompare(String(right.date))).slice(-260);
    return rows.length ? [[code, rows] as const] : [];
  }));
}

async function loadDailyHistories(codes: string[]) {
  const now = Date.now(); const result = new Map<string, DailyStrategyCandle[]>(); const missing: string[] = [];
  codes.forEach((code) => { const cached = dailyHistoryCache.get(code); if (cached && cached.expiresAt > now) result.set(code, cached.rows); else missing.push(code); });
  const batches = Array.from({ length: Math.ceil(missing.length / CANDLE_BATCH_SIZE) }, (_, index) => missing.slice(index * CANDLE_BATCH_SIZE, index * CANDLE_BATCH_SIZE + CANDLE_BATCH_SIZE));
  const settled = await mapMarketBatches(batches, fetchDailyHistoryBatch);
  settled.forEach((item) => { if (item.status === "fulfilled") item.value.forEach((rows, code) => { result.set(code, rows); dailyHistoryCache.set(code, { expiresAt: now + 30 * 60_000, rows }); }); });
  if (dailyHistoryCache.size > 1_000) dailyHistoryCache = new Map([...dailyHistoryCache].filter(([, item]) => item.expiresAt > now));
  return result;
}

async function fetchFiveMinuteHistoryBatch(codes: string[]) {
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "5m" } }]));
  const payload = await fetchBoundedMarketJson<CandleEnvelope[]>(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, () => ({ cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-River-MA20-History/1.0" }, signal: AbortSignal.timeout(18_000) }));
  return new Map(codes.flatMap((code, index) => {
    const rows = (payload[index]?.result?.data?.json?.candles ?? []).flatMap((raw) => {
      const row = normalizeFiveMinuteHistoryCandle(raw); return row ? [row] : [];
    });
    return rows.length ? [[code, rows] as const] : [];
  }));
}

async function loadFiveMinuteHistories(codes: string[]) {
  const now = Date.now(); const result = new Map<string, FiveMinuteHistoryCandle[]>(); const missing: string[] = [];
  codes.forEach((code) => { const cached = fiveMinuteHistoryCache.get(code); if (cached && cached.expiresAt > now) result.set(code, cached.rows); else missing.push(code); });
  const batches = Array.from({ length: Math.ceil(missing.length / CANDLE_BATCH_SIZE) }, (_, index) => missing.slice(index * CANDLE_BATCH_SIZE, index * CANDLE_BATCH_SIZE + CANDLE_BATCH_SIZE));
  const settled = await mapMarketBatches(batches, fetchFiveMinuteHistoryBatch);
  settled.forEach((item) => { if (item.status === "fulfilled") item.value.forEach((rows, code) => { result.set(code, rows); fiveMinuteHistoryCache.set(code, { expiresAt: now + 30 * 60_000, rows }); }); });
  if (fiveMinuteHistoryCache.size > 1_000) fiveMinuteHistoryCache = new Map([...fiveMinuteHistoryCache].filter(([, item]) => item.expiresAt > now));
  return result;
}

function previousSessionFiveMinuteCloses(rows: FiveMinuteHistoryCandle[], tradeDate: string) {
  const currentSessionPrefix = `${tradeDate.slice(5)} `;
  const firstCurrentSessionIndex = rows.findIndex((row) => row.date.startsWith(currentSessionPrefix));
  const previousRows = firstCurrentSessionIndex >= 0 ? rows.slice(0, firstCurrentSessionIndex) : rows;
  return previousRows.slice(-19).map((row) => row.close);
}

async function loadActiveDispositionCodes() {
  if (dispositionCache && dispositionCache.expiresAt > Date.now()) return dispositionCache.codes;
  try {
    const response = await getDispositionRisk(); const payload = await response.json() as DispositionPayload;
    const codes = new Set((payload.dispositions ?? []).filter((row) => row.status === "處置中" || row.status === "即將處置").map((row) => String(row.code ?? "")));
    dispositionCache = { expiresAt: Date.now() + 5 * 60_000, codes }; return codes;
  } catch { return dispositionCache?.codes ?? new Set<string>(); }
}

function buildLiveDailyCandle(rows: ReturnType<typeof aggregateRiverFiveMinuteBars>, tradeDate: string) {
  const session = rows.filter((row) => taipeiDate(row.ts) === tradeDate); const first = session[0]; const latest = session.at(-1); if (!first || !latest) return null;
  return { date: tradeDate, open: first.open, high: Math.max(...session.map((row) => row.high)), low: Math.min(...session.map((row) => row.low)), close: latest.close, volume: session.reduce((sum, row) => sum + row.volume, 0) } satisfies DailyStrategyCandle;
}

function crossMatch(candidate: TechnicalRow, rows: ReturnType<typeof aggregateRiverFiveMinuteBars>) {
  const current = rows.at(-1); const previous = rows.at(-2); const base = Number(candidate.maLiveBaseSums[20]); if (!current || !Number.isFinite(base)) return null;
  const previousPrice = previous?.close ?? Number(candidate.close); if (!Number.isFinite(previousPrice)) return null;
  return detectIntradayMa20Cross({ previousPrice, currentPrice: current.close, previousMa20: (base + previousPrice) / 20, currentMa20: (base + current.close) / 20 });
}

function strategySignal(candidate: TechnicalRow, latest: ReturnType<typeof scoreRiverFiveMinuteBars>[number], tradeDate: string, matched: DailyStrategyMatch, group: { groupName: string; groupRank: number }): RiverStrategySignal {
  return { code: candidate.code, name: candidate.name ?? candidate.code, market: candidate.market, tradeDate, barTs: latest.ts, price: latest.close, changePct: Number(candidate.changePct), score: latest.score.score, status: latest.score.status, side: matched.direction, direction: matched.direction, volumeRatio: latest.volumeRatio, vwap: latest.vwap, volumeVwapScore: latest.score.components.volumeVwap, openingPct: latest.score.openingPct, confirmationState: latest.score.confirmationState, fiveMinuteBars: 1, signalType: "daily-strategy", strategyKind: matched.kind, strategyName: matched.name, groupName: group.groupName, groupRank: group.groupRank, selectionSource: "focus-ranking-67", selectionRuleVersion: DAILY_STRATEGY_GROUP_SELECTION_VERSION, label: `${matched.name}・${matched.direction === "bull" ? "前十強" : "後十弱"}族群第 ${group.groupRank} 名・${group.groupName}・個股漲跌 ${Number(candidate.changePct) > 0 ? "+" : ""}${Number(candidate.changePct).toFixed(2)}%・${matched.summary}` };
}

async function readStoredSignalPayload() {
  const signalDate = await selectedSignalDate();
  const [storedRiverSignals, storedStrategySignals, scanState] = await Promise.all([
    readRiverSignals(signalDate).catch(() => []),
    readRiverStrategySignals(signalDate).catch(() => []),
    readRiverIntradayScanState().catch(() => null),
  ]);
  const riverSignals = accumulatedOfficialRiverSignals(storedRiverSignals, OFFICIAL_PRIMARY_GROUP_BY_CODE);
  const strategySignals = accumulatedOfficialStrategySignals(storedStrategySignals, OFFICIAL_PRIMARY_GROUP_BY_CODE, storedRiverSignals);
  return {
    // 這是純讀取已保存快照的端點。上一輪背景重掃失敗不代表 D1 中的
    // 當日訊號失效；回傳可讀狀態並另外保留 scanState 供診斷。
    ok: true,
    updatedAt: scanState?.completedAt ? new Date(scanState.completedAt).toISOString() : null,
    tradeDate: scanState?.tradeDate ?? signalDate,
    signalDate,
    readOnly: true,
    scanState,
    primaryGroupCoverage: OFFICIAL_PRIMARY_GROUP_BY_CODE.size,
    storedSignalCoverage: {
      riverRaw: storedRiverSignals.length,
      riverVisible: riverSignals.length,
      strategyRaw: storedStrategySignals.length,
      strategyVisible: strategySignals.length,
    },
    signals: [...riverSignals, ...strategySignals].sort((a, b) => b.barTs - a.barTs),
  };
}

export async function GET(request: NextRequest) {
  if (request.nextUrl.searchParams.get("fast") === "1") {
    if (cache && cache.expiresAt > Date.now()) {
      return Response.json(cache.payload, { headers: { "Cache-Control": "private, no-store" } });
    }
    return Response.json(await readStoredSignalPayload(), { headers: { "Cache-Control": "private, no-store" } });
  }
  if (cache && cache.expiresAt > Date.now() && request.nextUrl.searchParams.get("refresh") !== "1") return Response.json(cache.payload, { headers: { "Cache-Control": "private, no-store" } });
  const scanStartedAt = Date.now();
  const force = request.nextUrl.searchParams.get("refresh") === "1";
  const acquired = await acquireRiverIntradayScanLease(scanStartedAt, force ? 0 : 2_000).catch(() => false);
  if (!acquired) {
    return Response.json({ ...(await readStoredSignalPayload()), refreshSkipped: "scan-in-progress-or-recent" }, { headers: { "Cache-Control": "private, no-store" } });
  }
  try {
    return await runRiverIntradayScan(request, scanStartedAt);
  } catch (error) {
    const completedAt = Date.now();
    const reason = error instanceof Error ? error.message : "river-intraday-scan-failed";
    const previous = await readRiverIntradayScanState().catch(() => null);
    const scanState = { ...previous, status: "error", startedAt: scanStartedAt, completedAt, reason } satisfies RiverIntradayScanState;
    await saveRiverIntradayScanState(scanState).catch(() => false);
    return Response.json({ ...(await readStoredSignalPayload()), ok: false, updatedAt: new Date(completedAt).toISOString(), scanState, error: reason }, { headers: { "Cache-Control": "private, no-store" }, status: 503 });
  }
}

async function runRiverIntradayScan(request: NextRequest, scanStartedAt: number) {
  const strategyWindowActive = isDailyStrategyScanWindow(scanStartedAt);
  const strategyHistoryCoverageRequired = isDailyStrategyHistoryCoverageWindow(scanStartedAt);
  // Intraday scans consume saved daily indicators; rebuilding full market history here
  // can exhaust the Worker while unrelated quote requests are still running.
  const technicalResponse = await getTechnicalMarket(new NextRequest(new URL("/api/technical-market?fast=1", request.nextUrl.origin)));
  const technical = await technicalResponse.json() as TechnicalPayload;
  const ready = (technical.rows ?? []).filter((row) => row.technicalReady && Number.isFinite(row.riverScore) && row.maLiveBaseSums);
  if (ready.length === 0) throw new Error("technical-market-indicators-not-ready");
  const bullCandidates = ready.filter((row) => Number(row.riverScore) >= 55).sort((a, b) => Number(b.riverScore) - Number(a.riverScore)).slice(0, 100);
  const bearCandidates = ready.filter((row) => Number(row.riverScore) <= 45).sort((a, b) => Number(a.riverScore) - Number(b.riverScore)).slice(0, 100);
  const [groups, focusGroupOrder] = await Promise.all([loadRiverGroups(), loadFocusGroupOrder(request.nextUrl.origin)]);
  const previousScan = await readRiverIntradayScanState().catch(() => null);
  const selectedTradeDate = await selectedSignalDate();
  const batch = riverScanBatch(focusGroupOrder, previousScan?.tradeDate === selectedTradeDate ? previousScan.nextGroupIndex : 0);
  const belongsToBatch = (code: string) => batch.groups.has(OFFICIAL_PRIMARY_GROUP_BY_CODE.get(code) ?? "");
  const readyByCode = new Map(ready.map((row) => [row.code, row]));
  const strongStrategyGroupsByCode = rankedGroupMembers(groups, focusGroupOrder.bull, OFFICIAL_PRIMARY_GROUP_BY_CODE);
  const weakStrategyGroupsByCode = rankedGroupMembers(groups, focusGroupOrder.bear, OFFICIAL_PRIMARY_GROUP_BY_CODE);
  const strategyCandidates = (strategyHistoryCoverageRequired ? [...new Set([...strongStrategyGroupsByCode.keys(), ...weakStrategyGroupsByCode.keys()])] : [])
    .flatMap((code) => { const row = readyByCode.get(code); return row && belongsToBatch(code) ? [row] : []; });
  const focusedCodes = new Set<string>();
  focusGroupOrder.bull.forEach((groupName) => (groups.get(groupName) ?? []).forEach((member) => {
    if (!batch.groups.has(groupName)) return;
    if (OFFICIAL_PRIMARY_GROUP_BY_CODE.get(member.code) !== groupName) return;
    const changePct = Number(readyByCode.get(member.code)?.changePct);
    if (Number.isFinite(changePct) && changePct >= 0 && changePct <= 7) focusedCodes.add(member.code);
  }));
  focusGroupOrder.bear.forEach((groupName) => (groups.get(groupName) ?? []).forEach((member) => {
    if (!batch.groups.has(groupName)) return;
    if (OFFICIAL_PRIMARY_GROUP_BY_CODE.get(member.code) !== groupName) return;
    const changePct = Number(readyByCode.get(member.code)?.changePct);
    if (Number.isFinite(changePct) && changePct <= 0 && changePct >= -7) focusedCodes.add(member.code);
  }));
  const scanPool = [...new Map([
    ...strategyCandidates.map((row) => [row.code, row] as const),
    ...[...focusedCodes].flatMap((code) => { const row = readyByCode.get(code); return row ? [[code, row] as const] : []; }),
    ...bullCandidates.slice(batch.index * 10, batch.index * 10 + 10).map((row) => [row.code, row] as const),
    ...bearCandidates.slice(batch.index * 10, batch.index * 10 + 10).map((row) => [row.code, row] as const),
  ]).values()];
  const [barsByCode, fiveMinuteHistories] = await Promise.all([
    loadBatchBars(scanPool.map((row) => row.code)),
    loadFiveMinuteHistories([...focusedCodes]),
  ]);
  const allFiveMinuteByCode = new Map(scanPool.map((candidate) => [candidate.code, aggregateRiverFiveMinuteBars(barsByCode.get(candidate.code) ?? [])] as const));
  const scoredBarsByCode = new Map(scanPool.map((candidate) => {
    const sessionBars = (allFiveMinuteByCode.get(candidate.code) ?? []).filter((bar) => taipeiDate(bar.ts) === selectedTradeDate);
    return [candidate.code, scoreRiverFiveMinuteBars(sessionBars, candidate.maLiveBaseSums)] as const;
  }));
  const selectionMatchByCode = new Map<string, RiverMa20SelectionMatch>();
  const intradayEligibility = new Map<string, { bull: boolean; bear: boolean }>();
  focusedCodes.forEach((code) => {
    const candidate = readyByCode.get(code);
    const scored = scoredBarsByCode.get(code) ?? [];
    const precedingCloses = previousSessionFiveMinuteCloses(fiveMinuteHistories.get(code) ?? [], selectedTradeDate);
    const dailyMa20BaseSum = candidate ? number(candidate.maLiveBaseSums[20]) : null;
    const bullMatch = candidate ? latestRiverSelectionWithMa20Filters(scored, "bull", Number(candidate.riverScore), precedingCloses, dailyMa20BaseSum) : null;
    const bearMatch = candidate ? latestRiverSelectionWithMa20Filters(scored, "bear", Number(candidate.riverScore), precedingCloses, dailyMa20BaseSum) : null;
    if (bullMatch) selectionMatchByCode.set(`${code}:bull`, bullMatch);
    if (bearMatch) selectionMatchByCode.set(`${code}:bear`, bearMatch);
    intradayEligibility.set(code, { bull: bullMatch !== null, bear: bearMatch !== null });
  });
  const selected = selectRankedRiverGroupCandidates(
    groups,
    new Map(ready.map((row) => [row.code, Number(row.riverScore)])),
    focusGroupOrder,
    new Map(ready.flatMap((row) => Number.isFinite(row.changePct) ? [[row.code, Number(row.changePct)] as const] : [])),
    10,
    6,
    intradayEligibility,
    OFFICIAL_PRIMARY_GROUP_BY_CODE,
  );
  const selectedRows = [...selected.bull, ...selected.bear].flatMap((item) => {
    const row = readyByCode.get(item.code); return row ? [{ row, selection: item }] : [];
  });
  const candidates = scanPool;
  // History is needed only for this batch's strategy groups, not the unrelated
  // trend-score snapshots. Save each completed batch before scanning the next.
  const coveredCodes = strategyCandidates.filter((row) => (barsByCode.get(row.code)?.length ?? 0) > 0).map((row) => row.code);
  const [dailyHistories, activeDispositionCodes] = await Promise.all([
    strategyHistoryCoverageRequired ? loadDailyHistories(coveredCodes) : Promise.resolve(new Map<string, DailyStrategyCandle[]>()),
    strategyWindowActive ? loadActiveDispositionCodes() : Promise.resolve(new Set<string>()),
  ]);
  const snapshots: RiverIntradaySnapshot[] = [];
  const generatedSignals: RiverIntradaySignal[] = selectedRows.flatMap(({ row, selection }) => {
    const match = selectionMatchByCode.get(`${row.code}:${selection.direction}`);
    if (!match) return [];
    return [{
      code: row.code, name: row.name ?? selection.name, market: row.market, tradeDate: selectedTradeDate,
      barTs: match.ts, price: match.close,
      score: Number(row.riverScore), status: row.riverSide, side: selection.direction,
      direction: selection.direction, signalType: "river", groupName: selection.groupName, groupRank: selection.groupRank,
      stockRank: selection.stockRank, groupScore: selection.groupScore, changePct: selection.changePct,
      selectionSource: "focus-ranking-67", selectionRuleVersion: RIVER_GROUP_SELECTION_VERSION,
      ma20PositionVerified: true, dailyMa20: match.dailyMa20, fiveMinuteMa20: match.fiveMinuteMa20,
      label: selection.direction === "bull"
        ? `均線多・最強前 10 主族群第 ${selection.groupRank} 名・個股 0～+7%・日 K 與訊號 5 分 K 均在各自 20MA 之上・群內依漲幅排序最多 6 檔`
        : `均線空・最弱後 10 主族群第 ${selection.groupRank} 名・個股 0～-7%・日 K 與訊號 5 分 K 均在各自 20MA 之下・群內依跌幅排序最多 6 檔`,
    }];
  });
  const generatedStrategySignals: RiverStrategySignal[] = [];

  for (const candidate of candidates) {
    const allFiveMinute = aggregateRiverFiveMinuteBars(barsByCode.get(candidate.code) ?? []); const latestDate = allFiveMinute.length ? taipeiDate(allFiveMinute.at(-1)!.ts) : null; if (!latestDate) continue;
    const sessionBars = allFiveMinute.filter((bar) => taipeiDate(bar.ts) === latestDate); const liveScored = latestDate === selectedTradeDate ? (scoredBarsByCode.get(candidate.code) ?? []) : scoreRiverFiveMinuteBars(sessionBars, candidate.maLiveBaseSums); const latestLive = liveScored.at(-1); if (!latestLive?.score) continue;
    const snapshot: RiverIntradaySnapshot = { code: candidate.code, name: candidate.name ?? candidate.code, market: candidate.market, tradeDate: latestDate, barTs: latestLive.ts, price: latestLive.close, score: latestLive.score.score, status: latestLive.score.status, side: latestLive.score.side, volumeRatio: latestLive.volumeRatio, vwap: latestLive.vwap, volumeVwapScore: latestLive.score.components.volumeVwap, openingPct: latestLive.score.openingPct, confirmationState: latestLive.score.confirmationState, fiveMinuteBars: liveScored.length };
    snapshots.push(snapshot);
    const matches: DailyStrategyMatch[] = [];
    const history = (dailyHistories.get(candidate.code) ?? []).filter((row) => String(row.date) < latestDate); const liveDaily = buildLiveDailyCandle(sessionBars, latestDate);
    // 日線 11 策略只在台北時間 11:00～14:30 建立。11:00 前即使
    // K 型或穿 2／破 2 已暫時成立，也不可寫入、顯示或跳出訊號。
    if (strategyWindowActive && isDailyStrategySignalWindow(latestLive.ts) && isListedOrOtcStockCode(candidate.code)) {
      if (liveDaily) matches.push(...detectIntradayDailyStrategies({ history, live: liveDaily, sessionProgress: sessionProgress(latestLive.ts), activeDisposition: activeDispositionCodes.has(candidate.code) }));
      const ma20Cross = crossMatch(candidate, sessionBars); if (ma20Cross) matches.push(ma20Cross);
    }
    [...new Map(matches.map((item) => [item.kind, item])).values()].forEach((matched) => {
      if (!passesDailyStrategyDirectionChange(matched.direction, candidate.changePct)) return;
      const group = matched.direction === "bull"
        ? strongStrategyGroupsByCode.get(candidate.code)
        : weakStrategyGroupsByCode.get(candidate.code);
      if (group) generatedStrategySignals.push(strategySignal(candidate, latestLive, latestDate, matched, group));
    });
  }

  await Promise.all([saveRiverIntraday(snapshots, generatedSignals), saveRiverStrategySignals(generatedStrategySignals)]).catch(() => false);
  const signalDate = await selectedSignalDate();
  const [storedRiverSignals, storedStrategySignals] = await Promise.all([readRiverSignals(signalDate).catch(() => []), readRiverStrategySignals(signalDate).catch(() => [])]);
  const liveRiverSignals = generatedSignals.filter((signal) => signal.tradeDate === signalDate); const liveStrategySignals = generatedStrategySignals.filter((signal) => signal.tradeDate === signalDate);
  const riverSignals = accumulatedOfficialRiverSignals([...liveRiverSignals, ...storedRiverSignals], OFFICIAL_PRIMARY_GROUP_BY_CODE);
  const strategySignals = accumulatedOfficialStrategySignals(
    [...liveStrategySignals, ...storedStrategySignals],
    OFFICIAL_PRIMARY_GROUP_BY_CODE,
    [...liveRiverSignals, ...storedRiverSignals],
  );
  const signals = [...riverSignals, ...strategySignals].sort((a, b) => b.barTs - a.barTs);
  const rows = snapshots.sort((a, b) => b.score - a.score); const latestTradeDate = rows.map((row) => row.tradeDate).sort().at(-1) ?? technical.dataDate ?? "—";
  const completedAt = Date.now();
  const scanState = {
    status: barsByCode.size > 0 && (!strategyHistoryCoverageRequired || coveredCodes.length === 0 || dailyHistories.size > 0) ? "ok" : "degraded",
    startedAt: scanStartedAt,
    completedAt,
    tradeDate: latestTradeDate,
    barCoverage: barsByCode.size,
    totalCandidates: scanPool.length,
    strategyHistoryCoverage: dailyHistories.size,
    strategyHistoryCoverageRequired,
    strategyWindowActive,
    nextGroupIndex: batch.nextIndex,
    scannedGroupCount: batch.index + 1,
    totalGroupCount: batch.total,
    cycleCompletedAt: batch.nextIndex === 0 ? completedAt : previousScan?.tradeDate === selectedTradeDate ? previousScan.cycleCompletedAt : undefined,
    reason: barsByCode.size === 0 ? "no-usable-intraday-bars" : strategyHistoryCoverageRequired && dailyHistories.size === 0 ? "no-usable-daily-history" : undefined,
  } satisfies RiverIntradayScanState;
  await saveRiverIntradayScanState(scanState).catch(() => false);
  const payload = { ok: scanState.status === "ok", updatedAt: new Date(completedAt).toISOString(), scanState, tradeDate: latestTradeDate, signalDate, candidateRule: `均線多只取正式主族群位於 67 族群最強前 10 群、個股漲幅 0～+7%；均線空只取正式主族群位於最弱後 10 群、個股跌幅 0～-7%。重疊成分表或查不到正式主族群的股票一律排除。均線多必須同時滿足當日日 K 在日線 20MA 之上，且訊號當下 5 分 K 收盤價在 5 分 20MA 之上；均線空兩項條件顛倒。每群依個股漲跌幅排序最多取 6 檔；不足時不跨群、也不以區間外股票補滿。日線 11 策略多方只留前十強主族群且守 0～+7%，空方只留後十弱主族群且守 0～-7%，並只在 11:00～14:30 顯示。`, confirmationRule: `訊號時間記錄每檔盤中第一次同時通過均線強弱分數、日線 20MA 與 5 分 20MA 的五分 K。若缺少前一交易日 19 根五分 K，該檔不產生訊號，也不回填成 09:00。日線 11 策略在 11:00 前不產生訊號。`, retentionRule: "均線多、空自 09:00 起累積保留；日線 11 策略只保留 11:00～14:30 內第一次成立的實際時間。盤中族群排名更新只影響新訊號，不刪除已成立的紀錄。前一交易日訊號保留到下一交易日 08:45。", configuredGroupCount: selected.coveredGroups, scannedCandidates: candidates.length, selectedBullCandidates: selected.bull.length, selectedBearCandidates: selected.bear.length, strategyGroupRule: "日線 11 策略多方＝正式主族群前十強且 0～+7%；空方＝正式主族群後十弱且 0～-7%；時間＝11:00～14:30", focusGroupOrder, barCoverage: barsByCode.size, fiveMinuteHistoryCoverage: fiveMinuteHistories.size, strategyHistoryCoverage: dailyHistories.size, strategyCatalog: DAILY_STRATEGY_CATALOG, rows, signals };
  if (scanState.status === "ok") cache = { expiresAt: Date.now() + 2_000, payload };
  return Response.json(payload, { headers: { "Cache-Control": "private, no-store" } });
}
