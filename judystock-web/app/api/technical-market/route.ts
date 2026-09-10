import { type NextRequest } from "next/server";
import { readLatestTechnicalMarketSnapshot, readTechnicalMarketSnapshots, saveTechnicalMarketSnapshots, type TechnicalMarketSnapshotRow } from "../../../db/technical-market-history";
import { readTechnicalMarketIndicators, saveTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import { calculateMaArrangement, labelMaArrangement, MA_ARRANGEMENT_MODEL_VERSION } from "../../../lib/ma-arrangement";
import { calculateRiverScore, RIVER_ENGINE_VERSION, RIVER_MA_PERIODS, type RiverScoreResult } from "../../../lib/river-radar";
import { completedDailyStrategyDateAnchor, technicalMarketDateAnchor } from "../../../lib/technical-market-date";
import { VALUATION_RIVER_MODEL_VERSION, VALUATION_RIVER_WINDOW, valuationMedian, valuationRiverPosition } from "../../../lib/valuation-river";
import { DAILY_STRATEGY_MODEL_VERSION, detectCompletedDailyStrategies, isListedOrOtcStockCode, type DailyStrategyCandle } from "../../../lib/daily-strategy-signals";
import { maArrangementBonus, NEW_HIGH_PERIODS } from "../../../lib/ma-score-ranking";
import { BLACK_DRAGON_MODEL_VERSION, findRecentBlackDragonSignal } from "../../../lib/black-dragon";
import { blackDragonReferenceHighs } from "../../../lib/black-dragon-highs";
import { BLACK_DRAGON_INTRADAY_MODEL_VERSION, type BlackDragonIntradayBase } from "../../../lib/black-dragon-intraday";

type JsonRecord = Record<string, unknown>;
type DailyBar = TechnicalMarketSnapshotRow & { date: string; high?: number; low?: number; volume?: number };

const MA_PERIODS = RIVER_MA_PERIODS;
const FETCH_WINDOW_SIZE = 23;
const FETCH_DATE_CONCURRENCY = 6;
const BACKFILL_WINDOW_COUNT = 12;
const MIN_TWSE_ROWS = 700;
const MIN_TPEX_ROWS = 500;
const STOCK_INDICATOR_BATCH_SIZE = 160;
const HANSTOCK_TRPC_BATCH_SIZE = 40;
const FAST_INDICATOR_CACHE_MS = 30 * 60 * 1_000;
const FAST_INDICATOR_CACHE_CONTROL = "public, max-age=300, s-maxage=1800, stale-while-revalidate=86400";
const browserHeaders = {
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
  "User-Agent": "Mozilla/5.0 HanStock-Technical-Screener/2.0",
};

let cache: { expiresAt: number; payload: unknown } | null = null;
let fastIndicatorCache: { expiresAt: number; payload: unknown } | null = null;

function clean(value: unknown) {
  return String(value ?? "").replace(/<[^>]*>/g, "").replace(/,/g, "").trim();
}

function number(value: unknown) {
  const parsed = Number(clean(value).replace(/[−－]/g, "-"));
  return Number.isFinite(parsed) ? parsed : null;
}

function compactDate(date: Date) {
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

function slashDate(date: Date) {
  return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function candidateDates(anchor: Date, count: number, skipWeekdays = 0) {
  const dates: Date[] = [];
  let weekdays = 0;
  for (let offset = 0; offset < (count + skipWeekdays) * 2 && dates.length < count; offset += 1) {
    const date = new Date(anchor);
    date.setUTCDate(anchor.getUTCDate() - offset);
    if (date.getUTCDay() === 0 || date.getUTCDay() === 6) continue;
    if (weekdays < skipWeekdays) weekdays += 1;
    else dates.push(date);
  }
  return dates;
}

function stockLike(code: string) {
  return /^\d{4}[A-Z]?$/.test(code) || /^00\d{2,4}[A-Z]?$/.test(code) || /^91\d{4}$/.test(code);
}

function parseCsvLine(line: string) {
  const values: string[] = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') { value += '"'; index += 1; }
      else quoted = !quoted;
    } else if (character === "," && !quoted) { values.push(value); value = ""; }
    else value += character;
  }
  values.push(value);
  return values;
}

function parseTpexCsv(text: string, date: Date): DailyBar[] {
  return text.split(/\r?\n/).flatMap((line) => {
    const row = parseCsvLine(line);
    const code = clean(row[0]).toUpperCase();
    const parsedName = clean(row[1]);
    const name = /[\u3400-\u9fff]/.test(parsedName) ? parsedName : undefined;
    const close = number(row[2]);
    const open = number(row[4]);
    return stockLike(code) && open !== null && close !== null ? [{ code, name, market: "tpex" as const, date: slashDate(date), open, close }] : [];
  });
}

async function fetchTwse(date: Date): Promise<DailyBar[]> {
  const url = new URL("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX");
  url.searchParams.set("date", compactDate(date));
  url.searchParams.set("type", "ALLBUT0999");
  url.searchParams.set("response", "json");
  const response = await fetch(url, { headers: browserHeaders, cache: "no-store", signal: AbortSignal.timeout(12_000) });
  if (!response.ok) return [];
  const payload = await response.json() as JsonRecord;
  if (payload.stat !== "OK" || !Array.isArray(payload.tables)) return [];
  const table = (payload.tables as JsonRecord[]).find((item) => Array.isArray(item.fields) && (item.fields as unknown[]).some((field) => clean(field) === "證券代號"));
  if (!table || !Array.isArray(table.fields) || !Array.isArray(table.data)) return [];
  const fields = (table.fields as unknown[]).map(clean);
  const codeIndex = fields.indexOf("證券代號");
  const nameIndex = fields.indexOf("證券名稱");
  const openIndex = fields.indexOf("開盤價");
  const closeIndex = fields.indexOf("收盤價");
  return (table.data as unknown[][]).flatMap((row) => {
    const code = clean(row[codeIndex]).toUpperCase();
    const name = nameIndex >= 0 ? clean(row[nameIndex]) : undefined;
    const open = number(row[openIndex]);
    const close = number(row[closeIndex]);
    return stockLike(code) && open !== null && close !== null ? [{ code, name, market: "twse" as const, date: slashDate(date), open, close }] : [];
  });
}

async function fetchTpex(date: Date): Promise<DailyBar[]> {
  const url = new URL("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes");
  url.searchParams.set("date", slashDate(date));
  url.searchParams.set("id", "");
  url.searchParams.set("response", "json");
  // Cloud deployments are sometimes redirected by TPEx to /errors. Do not
  // follow that redirect loop; use the same read-only proxy fallback already
  // used by the institutional-history pipeline in this project.
  const tpexHeaders = {
    ...browserHeaders,
    Origin: "https://www.tpex.org.tw",
    Referer: "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html",
  };
  const direct = await fetch(url, { headers: tpexHeaders, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(15_000) }).catch(() => null);
  let payload: JsonRecord;
  if (direct?.ok) payload = await direct.json() as JsonRecord;
  else {
    const csvUrl = new URL(url);
    csvUrl.searchParams.set("response", "csv");
    const csv = await fetch(csvUrl, { headers: tpexHeaders, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(20_000) }).catch(() => null);
    if (csv?.ok) {
      // TPEx CSV is Big5. Latin-1 preserves ASCII digits, quotes and commas,
      // which are the only bytes needed for code/open/close parsing.
      const rows = parseTpexCsv(new TextDecoder("latin1").decode(await csv.arrayBuffer()), date);
      if (rows.length >= MIN_TPEX_ROWS) return rows;
    }
    const proxyUrl = `https://r.jina.ai/${url.toString().replace(/&/g, "%26")}`;
    const proxied = await fetch(proxyUrl, { headers: browserHeaders, cache: "no-store", signal: AbortSignal.timeout(45_000) });
    if (!proxied.ok) throw new Error(`tpex_proxy_http_${proxied.status}`);
    const text = await proxied.text();
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start < 0 || end <= start) throw new Error("tpex_proxy_payload_missing");
    payload = JSON.parse(text.slice(start, end + 1)) as JsonRecord;
  }
  if (payload.stat !== "ok" || !Array.isArray(payload.tables)) return [];
  const table = (payload.tables as JsonRecord[])[0];
  if (!table || !Array.isArray(table.fields) || !Array.isArray(table.data)) return [];
  const fields = (table.fields as unknown[]).map(clean);
  const codeIndex = fields.indexOf("代號");
  const nameIndex = fields.indexOf("名稱");
  const openIndex = fields.indexOf("開盤");
  const closeIndex = fields.indexOf("收盤");
  return (table.data as unknown[][]).flatMap((row) => {
    const code = clean(row[codeIndex]).toUpperCase();
    const name = nameIndex >= 0 ? clean(row[nameIndex]) : undefined;
    const open = number(row[openIndex]);
    const close = number(row[closeIndex]);
    return stockLike(code) && open !== null && close !== null ? [{ code, name, market: "tpex" as const, date: slashDate(date), open, close }] : [];
  });
}

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function riverScoreAt(closes: number[], offset: number): RiverScoreResult | null {
  if (closes.length < offset + 240) return null;
  const maValues = Object.fromEntries(MA_PERIODS.map((period) => [period, average(closes.slice(offset, offset + period))])) as Record<number, number | null>;
  const previousMaValues = closes.length >= offset + 241
    ? Object.fromEntries(MA_PERIODS.map((period) => [period, average(closes.slice(offset + 1, offset + 1 + period))])) as Record<number, number | null>
    : null;
  return calculateRiverScore({ close: closes[offset], maValues, previousMaValues });
}

function riverTrendDays(closes: number[], current: RiverScoreResult | null) {
  if (!current) return 0;
  let days = 0;
  for (let offset = 0; offset < Math.min(30, closes.length - 239); offset += 1) {
    const score = riverScoreAt(closes, offset);
    if (!score || score.side !== current.side) break;
    days += 1;
  }
  return days;
}

async function mapWithConcurrency<T, R>(items: T[], concurrency: number, task: (item: T) => Promise<R>) {
  const results = new Array<R>(items.length);
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await task(items[index]);
    }
  }));
  return results;
}

function mergeSnapshotRows(previous: TechnicalMarketSnapshotRow[], incoming: TechnicalMarketSnapshotRow[]) {
  const merged = new Map(previous.map((row) => [`${row.market}:${row.code}`, row]));
  incoming.forEach((row) => {
    const key = `${row.market}:${row.code}`;
    const existing = merged.get(key);
    merged.set(key, { ...existing, ...row, name: row.name ?? existing?.name });
  });
  return [...merged.values()];
}

function exchangeCounts(rows: TechnicalMarketSnapshotRow[]) {
  return {
    twse: rows.filter((row) => row.market === "twse").length,
    tpex: rows.filter((row) => row.market === "tpex").length,
  };
}

function completeSnapshot(rows: TechnicalMarketSnapshotRow[]) {
  const counts = exchangeCounts(rows);
  return counts.twse >= MIN_TWSE_ROWS;
}

function pickBackfillStep(existing: Array<{ tradeDate: string; rows: TechnicalMarketSnapshotRow[] }>) {
  const completeDates = new Set(existing.filter((day) => completeSnapshot(day.rows)).map((day) => day.tradeDate));
  const anchor = technicalMarketDateAnchor();
  for (let step = 0; step < BACKFILL_WINDOW_COUNT; step += 1) {
    const dates = candidateDates(anchor, FETCH_WINDOW_SIZE, FETCH_WINDOW_SIZE * (step + 1));
    const covered = dates.filter((date) => completeDates.has(slashDate(date))).length;
    if (covered < Math.floor(FETCH_WINDOW_SIZE * .75)) return step;
  }
  return BACKFILL_WINDOW_COUNT - 1;
}

async function fetchWindow(dates: Date[], existing: Array<{ tradeDate: string; rows: TechnicalMarketSnapshotRow[] }>) {
  const previousByDate = new Map(existing.map((day) => [day.tradeDate, day.rows]));
  const fetched = await mapWithConcurrency(dates, FETCH_DATE_CONCURRENCY, async (date) => {
    const [twseResult, tpexResult] = await Promise.allSettled([fetchTwse(date), fetchTpex(date)]);
    const twse = twseResult.status === "fulfilled" && twseResult.value.length >= MIN_TWSE_ROWS ? twseResult.value : [];
    const tpex = tpexResult.status === "fulfilled" ? tpexResult.value : [];
    const tpexFailure = tpexResult.status === "rejected" ? String((tpexResult.reason as Error)?.message ?? tpexResult.reason) : null;
    return {
      tradeDate: slashDate(date),
      twse,
      tpex,
      tpexFailure,
    };
  });
  const days = fetched.flatMap(({ tradeDate, twse, tpex }) => {
    const incoming = [...twse, ...tpex].map(({ code, name, market, open, close }) => ({ code, name, market, open, close }));
    if (!incoming.length) return [];
    return [{ tradeDate, rows: mergeSnapshotRows(previousByDate.get(tradeDate) ?? [], incoming) }];
  });
  let saved = false;
  try {
    saved = await saveTechnicalMarketSnapshots(days);
  } catch (error) {
    console.error("technical_market_snapshot_save_failed", error);
  }
  return {
    days,
    stats: {
      requestedDays: dates.length,
      twseDays: fetched.filter((day) => day.twse.length >= MIN_TWSE_ROWS).length,
      tpexDays: fetched.filter((day) => day.tpex.length >= MIN_TPEX_ROWS).length,
      tpexFailures: fetched.flatMap((day) => day.tpexFailure ? [`${day.tradeDate}:${day.tpexFailure}`] : []).slice(0, 3),
      completeDays: days.filter((day) => completeSnapshot(day.rows)).length,
      saved,
    },
  };
}

type HanstockCandle = { date?: unknown; open?: unknown; high?: unknown; low?: unknown; close?: unknown; volume?: unknown };
type HanstockBatchItem = { result?: { data?: { json?: { candles?: HanstockCandle[] } } } };

async function fetchHanstockHistoryBatch(codes: string[], market: "twse" | "tpex") {
  if (!codes.length) return [] as DailyBar[][];
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "1d" } }]));
  const response = await fetch(`https://hanstock-battle-minimal.thunghan8.chatgpt.site/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, {
    headers: { Accept: "application/json", "User-Agent": "HanStock-Technical-Screener/3.0" },
    cache: "no-store",
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`hanstock_${market}_batch_${response.status}`);
  const payload = await response.json() as HanstockBatchItem[];
  return codes.map((code, index) => (payload[index]?.result?.data?.json?.candles ?? []).flatMap((candle): DailyBar[] => {
    const date = clean(candle.date).replaceAll("-", "/");
    const open = number(candle.open);
    const high = number(candle.high);
    const low = number(candle.low);
    const close = number(candle.close);
    const volume = number(candle.volume);
    return /^\d{4}\/\d{2}\/\d{2}$/.test(date) && open !== null && high !== null && low !== null && close !== null && volume !== null
      ? [{ code, market, date, open, high, low, close, volume }]
      : [];
  }));
}

async function backfillStockIndicators(codes: string[], market: "twse" | "tpex") {
  const batches = Array.from({ length: Math.ceil(codes.length / HANSTOCK_TRPC_BATCH_SIZE) }, (_, index) => codes.slice(index * HANSTOCK_TRPC_BATCH_SIZE, index * HANSTOCK_TRPC_BATCH_SIZE + HANSTOCK_TRPC_BATCH_SIZE));
  const settled = await Promise.allSettled(batches.map((batch) => fetchHanstockHistoryBatch(batch, market)));
  const histories = settled.flatMap((result) => result.status === "fulfilled" ? result.value : []);
  const byDate = new Map<string, TechnicalMarketSnapshotRow[]>();
  // Keep the complete OHLCV candle.  The daily-strategy detector needs high,
  // low and volume; dropping them here made every completed history look
  // incomplete and left the screener permanently at 0 / total.
  histories.flat().forEach(({ date, code, market, open, high, low, close, volume }) => {
    const rows = byDate.get(date) ?? [];
    rows.push({ code, market, open, high, low, close, volume });
    byDate.set(date, rows);
  });
  const computed = buildRows([...byDate.entries()].map(([tradeDate, rows]) => ({ tradeDate, rows })));
  await saveTechnicalMarketIndicators(computed.map((row) => ({
    code: row.code,
    market: row.market,
    dataDate: row.date,
    payload: row as unknown as Record<string, unknown>,
  })));
  return { requested: codes.length, completed: computed.filter((row) => row.technicalReady).length, failedBatches: settled.filter((result) => result.status === "rejected").length };
}

function buildRows(snapshots: Array<{ tradeDate: string; rows: TechnicalMarketSnapshotRow[] }>) {
  const dailyStrategyCutoff = slashDate(completedDailyStrategyDateAnchor());
  const byCode = new Map<string, DailyBar[]>();
  snapshots.forEach((snapshot) => snapshot.rows.forEach((row) => {
    const current = byCode.get(row.code) ?? [];
    current.push({ ...row, date: snapshot.tradeDate });
    byCode.set(row.code, current);
  }));
  return [...byCode.entries()].flatMap(([code, values]) => {
    const ordered = values.sort((a, b) => b.date.localeCompare(a.date));
    const latest = ordered[0];
    if (!latest) return [];
    const closes = ordered.map((bar) => bar.close);
    const previousClose = closes[1] ?? latest.close;
    const changePct = previousClose ? (latest.close - previousClose) / previousClose * 100 : 0;
    const candle = latest.close > latest.open ? "red" : latest.close < latest.open ? "black" : "flat";
    const riverBase = closes.length >= VALUATION_RIVER_WINDOW ? valuationMedian(closes.slice(0, VALUATION_RIVER_WINDOW)) : null;
    const averages = Object.fromEntries(MA_PERIODS.map((period) => [period, closes.length >= period ? average(closes.slice(0, period)) : null])) as Record<string, number | null>;
    const previousAverages = Object.fromEntries(MA_PERIODS.map((period) => [period, closes.length >= period + 1 ? average(closes.slice(1, period + 1)) : null])) as Record<string, number | null>;
    const ready = MA_PERIODS.every((period) => averages[String(period)] !== null);
    const roundedMas = Object.fromEntries(MA_PERIODS.map((period) => [period, averages[String(period)] === null ? null : Math.round(averages[String(period)]! * 100) / 100]));
    const arrangement = ready ? calculateMaArrangement({
      close: latest.close,
      maValues: Object.fromEntries(MA_PERIODS.map((period) => [period, averages[String(period)]])),
      previousMaValues: Object.fromEntries(MA_PERIODS.map((period) => [period, previousAverages[String(period)]])),
    }) : null;
    const aboveMaPeriods = ready ? MA_PERIODS.filter((period) => latest.close >= averages[String(period)]!) : [];
    const newHighPeriods = NEW_HIGH_PERIODS.filter((period) => closes.length >= period && latest.close >= Math.max(...closes.slice(0, period)));
    const maBaseScore = aboveMaPeriods.length + newHighPeriods.length;
    const arrangementBonus = arrangement ? maArrangementBonus(arrangement.score) : 0;
    const liveBaseSums = Object.fromEntries(MA_PERIODS.map((period) => [period, closes.length >= period - 1 ? closes.slice(0, period - 1).reduce((sum, close) => sum + close, 0) : null]));
    const river = riverScoreAt(closes, 0);
    const previousRiver = riverScoreAt(closes, 1);
    const dailyCandles = [...ordered].reverse().flatMap((bar): DailyStrategyCandle[] =>
      bar.date <= dailyStrategyCutoff && typeof bar.high === "number" && typeof bar.low === "number" && typeof bar.volume === "number"
        ? [{ date: bar.date, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }]
        : []);
    const dailyStrategyReady = dailyCandles.length >= 21;
    const dailyStrategies = dailyStrategyReady ? detectCompletedDailyStrategies({ candles: dailyCandles }) : [];
    const completedBlackDragonBars = ordered.filter((bar) => bar.date <= dailyStrategyCutoff);
    const blackDragonTargetDates = [...new Set([
      slashDate(technicalMarketDateAnchor()),
      completedBlackDragonBars[0]?.date,
    ].filter((date): date is string => Boolean(date)))];
    const blackDragonIntradayBases = blackDragonTargetDates.flatMap((targetDate): BlackDragonIntradayBase[] => {
      const priorBars = ordered.filter((bar) => bar.date < targetDate);
      const priorCloses = priorBars.map((bar) => bar.close);
      const priorVolumes = priorBars.slice(0, 20)
        .map((bar) => Number(bar.volume)).filter((volume) => Number.isFinite(volume) && volume > 0);
      if (priorBars.length < Math.max(...MA_PERIODS) || priorVolumes.length < 20) return [];
      return [{
        modelVersion: BLACK_DRAGON_INTRADAY_MODEL_VERSION,
        code,
        name: latest.name?.trim() || code,
        market: latest.market,
        targetDate: targetDate.replaceAll("/", "-"),
        completedThrough: priorBars[0].date.replaceAll("/", "-"),
        previousClose: priorBars[0].close,
        sessionOpen: ordered.find((bar) => bar.date === targetDate)?.open ?? null,
        maLiveBaseSums: Object.fromEntries(MA_PERIODS.map((period) => [
          period,
          priorCloses.length >= period - 1
            ? priorCloses.slice(0, period - 1).reduce((sum, close) => sum + close, 0)
            : null,
        ])),
        previousMaValues: Object.fromEntries(MA_PERIODS.map((period) => [
          period,
          priorCloses.length >= period
            ? average(priorCloses.slice(0, period))
            : null,
        ])),
        referenceHighs: blackDragonReferenceHighs([...priorBars].reverse()),
        averageVolume20d: average(priorVolumes),
      } satisfies BlackDragonIntradayBase];
    });
    const blackDragonIntradayBase = blackDragonIntradayBases[0] ?? null;
    const blackDragonSignal = findRecentBlackDragonSignal({
      code,
      name: latest.name,
      market: latest.market,
      completedThrough: dailyStrategyCutoff,
      bars: ordered.map((bar) => ({
        date: bar.date,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
      })),
    });
    return [{
      code,
      name: latest.name,
      market: latest.market,
      date: latest.date,
      open: latest.open,
      close: latest.close,
      ma5: roundedMas[5], ma10: roundedMas[10], ma20: roundedMas[20], ma60: roundedMas[60], ma120: roundedMas[120], ma240: roundedMas[240],
      maValues: roundedMas,
      maLiveBaseSums: liveBaseSums,
      maModelVersion: MA_ARRANGEMENT_MODEL_VERSION,
      maBullConfirmed: arrangement?.bullConfirmed ?? false,
      maBearConfirmed: arrangement?.bearConfirmed ?? false,
      valuationModelVersion: VALUATION_RIVER_MODEL_VERSION,
      valuationWindow: VALUATION_RIVER_WINDOW,
      riverBase: riverBase === null ? null : Math.round(riverBase * 100) / 100,
      riverDistancePct: riverBase === null ? null : Math.round((latest.close / riverBase - 1) * 10_000) / 100,
      riverPosition: riverBase === null ? null : valuationRiverPosition(latest.close, riverBase),
      aboveMa5: averages[5] === null ? null : latest.close >= averages[5]!,
      aboveMa10: averages[10] === null ? null : latest.close >= averages[10]!,
      aboveMa20: averages[20] === null ? null : latest.close >= averages[20]!,
      maScore: arrangement?.score ?? null,
      maCompositeScore: arrangement ? maBaseScore + arrangementBonus : null,
      maBaseScore: arrangement ? maBaseScore : null,
      maArrangementBonus: arrangement ? arrangementBonus : null,
      aboveMaPeriods,
      newHighPeriods,
      maLabel: arrangement?.label ?? "資料不足",
      riverScore: river?.score ?? null,
      riverStatus: river?.status ?? "資料不足",
      riverSide: river?.side ?? null,
      riverOpeningPct: river?.openingPct ?? null,
      riverComponents: river?.components ?? null,
      riverConfirmationState: river?.confirmationState ?? "資料不足",
      riverTrendDays: riverTrendDays(closes, river),
      previousRiverScore: previousRiver?.score ?? null,
      previousRiverStatus: previousRiver?.status ?? null,
      isNewRiverBull: river?.side === "bull" && previousRiver?.side !== "bull",
      isNewRiverBear: river?.side === "bear" && previousRiver?.side !== "bear",
      isRiverExit: Boolean(previousRiver && river && previousRiver.side !== "neutral" && river.side !== previousRiver.side),
      dailyStrategyVersion: dailyStrategyReady ? DAILY_STRATEGY_MODEL_VERSION : null,
      dailyStrategyDataDate: dailyStrategyReady ? dailyCandles.at(-1)?.date ?? null : null,
      dailyStrategies,
      blackDragonModelVersion: BLACK_DRAGON_MODEL_VERSION,
      blackDragonDataDate: dailyStrategyCutoff,
      blackDragonSignal,
      blackDragonIntradayBase,
      blackDragonIntradayBases,
      technicalReady: ready,
      availableTradingDays: closes.length,
      candle,
      changePct: Math.round(changePct * 100) / 100,
    }];
  });
}

export async function GET(request: NextRequest) {
  const backfill = request.nextUrl.searchParams.get("backfill") === "1";
  const tpexStockBackfill = request.nextUrl.searchParams.get("tpexStockBackfill") === "1";
  const stockIndicatorBackfill = tpexStockBackfill || request.nextUrl.searchParams.get("stockIndicatorBackfill") === "1";
  const dailyStrategyBackfill = request.nextUrl.searchParams.get("dailyStrategies") === "1";
  const requestedMarket = request.nextUrl.searchParams.get("indicatorMarket");
  const indicatorMarket: "twse" | "tpex" = tpexStockBackfill || requestedMarket !== "twse" ? "tpex" : "twse";
  const readOnly = request.nextUrl.searchParams.get("readOnly") === "1";
  const compact = request.nextUrl.searchParams.get("compact") === "1";
  const fast = request.nextUrl.searchParams.get("fast") === "1";
  if (fast && fastIndicatorCache && fastIndicatorCache.expiresAt > Date.now()) {
    return Response.json(fastIndicatorCache.payload, { headers: { "Cache-Control": FAST_INDICATOR_CACHE_CONTROL } });
  }
  if (fast) {
    const [indicators, latestSnapshot] = await Promise.all([
      readTechnicalMarketIndicators().catch(() => []),
      readLatestTechnicalMarketSnapshot().catch(() => null),
    ]);
    const latestQuoteByStock = new Map((latestSnapshot?.rows ?? []).map((row) => [`${row.market}:${row.code}`, row]));
    const rows = indicators.flatMap((indicator) => {
      const row = indicator.payload as ReturnType<typeof buildRows>[number];
      if (row?.code !== indicator.code || row.market !== indicator.market) return [];
      const latestQuote = latestQuoteByStock.get(`${row.market}:${row.code}`);
      const open = Number.isFinite(row.open) ? row.open : latestQuote?.open;
      const close = latestQuote?.close ?? row.close;
      return [{
        code: row.code,
        name: row.name ?? latestQuote?.name,
        market: row.market,
        date: latestSnapshot?.tradeDate ?? row.date,
        open,
        close,
        changePct: row.changePct,
        riverBase: row.riverBase,
        riverDistancePct: row.riverDistancePct,
        riverPosition: row.riverPosition,
        riverScore: row.riverScore,
        riverSide: row.riverSide,
        ma5: row.ma5,
        ma10: row.ma10,
        ma20: row.ma20,
        ma60: row.ma60,
        ma120: row.ma120,
        ma240: row.ma240,
        maScore: row.maScore,
        maCompositeScore: row.maCompositeScore,
        maBaseScore: row.maBaseScore,
        maArrangementBonus: row.maArrangementBonus,
        aboveMaPeriods: row.aboveMaPeriods,
        newHighPeriods: row.newHighPeriods,
        maLabel: row.maLabel,
        maBullConfirmed: row.maBullConfirmed,
        maBearConfirmed: row.maBearConfirmed,
        maLiveBaseSums: row.maLiveBaseSums,
        aboveMa5: row.aboveMa5,
        aboveMa10: row.aboveMa10,
        aboveMa20: row.aboveMa20,
        technicalReady: row.technicalReady,
        availableTradingDays: row.availableTradingDays,
        candle: row.candle,
      }];
    });
    const dataDate = latestSnapshot?.tradeDate ?? indicators.map((row) => row.dataDate).sort().at(-1) ?? rows.map((row) => row.date).sort().at(-1) ?? "—";
    const historyTradingDays = Math.min(240, rows.reduce((maximum, row) => Math.max(maximum, Number(row.availableTradingDays) || 0), 0));
    const riverReadyCount = rows.filter((row) => row.riverBase !== null).length;
    const technicalReadyCount = rows.filter((row) => row.technicalReady).length;
    const readyTarget = Math.max(1_000, Math.floor(rows.length * .7));
    const payload = {
      ok: rows.length > 0,
      dataDate,
      updatedAt: indicators.map((row) => row.updatedAt).filter((value): value is string => Boolean(value)).sort().at(-1) ?? new Date().toISOString(),
      historyTradingDays,
      historyReady: technicalReadyCount >= readyTarget && riverReadyCount >= readyTarget,
      riverReadyCount,
      technicalReadyCount,
      readyTarget,
      maPeriods: MA_PERIODS,
      riverEngineVersion: RIVER_ENGINE_VERSION,
      maModelVersion: MA_ARRANGEMENT_MODEL_VERSION,
      valuationModelVersion: VALUATION_RIVER_MODEL_VERSION,
      snapshotCacheHit: true,
      rows,
    };
    fastIndicatorCache = { expiresAt: Date.now() + FAST_INDICATOR_CACHE_MS, payload };
    return Response.json(payload, { headers: { "Cache-Control": FAST_INDICATOR_CACHE_CONTROL } });
  }
  if (!backfill && cache && cache.expiresAt > Date.now()) {
    return Response.json(cache.payload, { headers: { "Cache-Control": "public, max-age=60, s-maxage=300" } });
  }

  const existing = await readTechnicalMarketSnapshots().catch(() => []);
  const existingIndicators = await readTechnicalMarketIndicators().catch(() => []);
  const requestedStep = Number(request.nextUrl.searchParams.get("step"));
  const requestedDays = Number(request.nextUrl.searchParams.get("days"));
  const windowSize = Number.isInteger(requestedDays) && requestedDays >= 1 ? Math.min(requestedDays, FETCH_WINDOW_SIZE) : FETCH_WINDOW_SIZE;
  const step = backfill
    ? Number.isInteger(requestedStep) && requestedStep >= 0 ? Math.min(requestedStep, BACKFILL_WINDOW_COUNT - 1) : pickBackfillStep(existing)
    : -1;
  const dates = readOnly || stockIndicatorBackfill ? [] : candidateDates(technicalMarketDateAnchor(), windowSize, backfill ? FETCH_WINDOW_SIZE * (step + 1) : 0);
  const fetched = dates.length ? await fetchWindow(dates, existing) : { days: [], stats: { requestedDays: 0, twseDays: 0, tpexDays: 0, tpexFailures: [] as string[], completeDays: 0, saved: false } };
  const stored = await readTechnicalMarketSnapshots().catch(() => []);
  const fallback = stored.length ? stored : fetched.days.sort((a, b) => b.tradeDate.localeCompare(a.tradeDate));
  const snapshotRows = buildRows(fallback);
  const marketCodes = [...new Set([
    ...snapshotRows.filter((row) => row.market === indicatorMarket).map((row) => row.code),
    ...existingIndicators.filter((row) => row.market === indicatorMarket).map((row) => row.code),
  ])].filter((code) => !dailyStrategyBackfill || isListedOrOtcStockCode(code)).sort();
  const legacyTpexStep = Number(request.nextUrl.searchParams.get("tpexStep"));
  const requestedIndicatorStep = Number(request.nextUrl.searchParams.get("indicatorStep"));
  const indicatorStep = tpexStockBackfill ? legacyTpexStep : requestedIndicatorStep;
  let indicatorBackfillStats = { requested: 0, completed: 0, failedBatches: 0 };
  if (stockIndicatorBackfill) {
    const readyCodes = new Set(existingIndicators.filter((row) => row.market === indicatorMarket && row.payload.maModelVersion === MA_ARRANGEMENT_MODEL_VERSION && row.payload.valuationModelVersion === VALUATION_RIVER_MODEL_VERSION && row.payload.valuationWindow === VALUATION_RIVER_WINDOW && row.payload.blackDragonModelVersion === BLACK_DRAGON_MODEL_VERSION && Boolean(row.payload.technicalReady) && (!dailyStrategyBackfill || row.payload.dailyStrategyVersion === DAILY_STRATEGY_MODEL_VERSION)).map((row) => row.code));
    const selected = Number.isInteger(indicatorStep) && indicatorStep >= 0
      ? marketCodes.slice(indicatorStep * STOCK_INDICATOR_BATCH_SIZE, (indicatorStep + 1) * STOCK_INDICATOR_BATCH_SIZE)
      : marketCodes.filter((code) => !readyCodes.has(code)).slice(0, STOCK_INDICATOR_BATCH_SIZE);
    indicatorBackfillStats = await backfillStockIndicators(selected, indicatorMarket).catch(() => ({ requested: selected.length, completed: 0, failedBatches: Math.ceil(selected.length / HANSTOCK_TRPC_BATCH_SIZE) }));
    fastIndicatorCache = null;
    cache = null;
    // Backfill callers only need completion metadata. Re-reading and rebuilding
    // the entire universe here keeps two copies of the long histories in memory
    // and can trigger Cloudflare 1101.
    if (compact) {
      return Response.json({
        ok: indicatorBackfillStats.completed > 0 || indicatorBackfillStats.requested === 0,
        dataDate: fallback[0]?.tradeDate ?? existingIndicators.map((row) => row.dataDate).sort().at(-1) ?? "—",
        updatedAt: new Date().toISOString(),
        indicatorBackfillMarket: indicatorMarket,
        indicatorBackfillStats,
      }, { headers: { "Cache-Control": "no-store" } });
    }
  }
  const indicators = stockIndicatorBackfill ? await readTechnicalMarketIndicators().catch(() => existingIndicators) : existingIndicators;
  const rowMap = new Map(snapshotRows.map((row) => [`${row.market}:${row.code}`, row]));
  indicators.forEach((indicator) => {
    const cachedRow = indicator.payload as ReturnType<typeof buildRows>[number];
    if (cachedRow?.code === indicator.code && cachedRow.market === indicator.market && cachedRow.date === indicator.dataDate) {
      // Keep the existing long-MA row visible while the v2 slope confirmation
      // is rebuilt. Legacy rows receive a conservative price-above/below-MA5
      // gate immediately, so a 15/15 pullback such as 8112 cannot remain in
      // the confirmed strong-bull list during migration.
      const legacyScore = cachedRow.maScore;
      const legacyBullConfirmed = legacyScore !== null && legacyScore >= 12 && cachedRow.ma5 !== null && cachedRow.close >= cachedRow.ma5;
      const legacyBearConfirmed = legacyScore !== null && legacyScore <= 5 && cachedRow.ma5 !== null && cachedRow.close <= cachedRow.ma5;
      const row = cachedRow.maModelVersion === MA_ARRANGEMENT_MODEL_VERSION ? cachedRow : {
        ...cachedRow,
        maBullConfirmed: legacyBullConfirmed,
        maBearConfirmed: legacyBearConfirmed,
        maLabel: legacyScore === null ? "資料不足" : labelMaArrangement(legacyScore, legacyBullConfirmed, legacyBearConfirmed),
      };
      const key = `${row.market}:${row.code}`;
      const snapshot = rowMap.get(key);
      // Long-MA values may come from the per-stock cache, but valuation must
      // always use the same latest 160-session window as the detail chart.
      // Never expose a legacy 80-session position under the new filter labels;
      // the background per-stock batch upgrades it in place.
      const cachedValuationIsCurrent = row.valuationModelVersion === VALUATION_RIVER_MODEL_VERSION && row.valuationWindow === VALUATION_RIVER_WINDOW;
      const snapshotValuationIsCurrent = snapshot?.valuationModelVersion === VALUATION_RIVER_MODEL_VERSION && snapshot.valuationWindow === VALUATION_RIVER_WINDOW && snapshot.riverBase !== null;
      const valuationSource = cachedValuationIsCurrent ? row : snapshotValuationIsCurrent ? snapshot : null;
      rowMap.set(key, {
        ...snapshot,
        ...row,
        name: snapshot?.name ?? row.name,
        riverBase: valuationSource?.riverBase ?? null,
        riverDistancePct: valuationSource?.riverDistancePct ?? null,
        riverPosition: valuationSource?.riverPosition ?? null,
        valuationModelVersion: valuationSource?.valuationModelVersion ?? VALUATION_RIVER_MODEL_VERSION,
        valuationWindow: valuationSource?.valuationWindow ?? VALUATION_RIVER_WINDOW,
      });
    }
  });
  const rows = [...rowMap.values()];
  const dataDate = fallback[0]?.tradeDate ?? rows.map((row) => row.date).sort().at(-1) ?? "—";
  const historyTradingDays = Math.min(240, rows.reduce((maximum, row) => Math.max(maximum, row.availableTradingDays), 0));
  const riverReadyCount = rows.filter((row) => row.riverPosition !== null).length;
  const technicalReadyCount = rows.filter((row) => row.technicalReady).length;
  const readyTarget = Math.max(1_000, Math.floor(rows.length * .7));
  const historyReady = technicalReadyCount >= readyTarget && riverReadyCount >= readyTarget;
  const payload = {
    ok: riverReadyCount > 0,
    dataDate,
    updatedAt: new Date().toISOString(),
    historyTradingDays,
    historyReady,
    riverReadyCount,
    technicalReadyCount,
    readyTarget,
    earliestDate: fallback.at(-1)?.tradeDate ?? null,
    backfillStep: step,
    fetchStats: fetched.stats,
    twseIndicatorCount: indicators.filter((row) => row.market === "twse").length,
    tpexIndicatorCount: indicators.filter((row) => row.market === "tpex").length,
    indicatorBackfillMarket: stockIndicatorBackfill ? indicatorMarket : null,
    indicatorBackfillStats,
    tpexBackfillStats: indicatorMarket === "tpex" ? indicatorBackfillStats : { requested: 0, completed: 0, failedBatches: 0 },
    maPeriods: MA_PERIODS,
    riverEngineVersion: RIVER_ENGINE_VERSION,
    maModelVersion: MA_ARRANGEMENT_MODEL_VERSION,
    valuationModelVersion: VALUATION_RIVER_MODEL_VERSION,
    rows,
  };
  if (!backfill && historyReady) cache = { expiresAt: Date.now() + 5 * 60 * 1_000, payload };
  const responsePayload = backfill && compact ? { ...payload, rows: undefined } : payload;
  return Response.json(responsePayload, { headers: { "Cache-Control": backfill || readOnly ? "no-store" : "public, max-age=60, s-maxage=300" } });
}
