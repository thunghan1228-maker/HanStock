import { type NextRequest } from "next/server";
import { readTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import {
  claimBlackDragonIntradayBatch,
  finishBlackDragonIntradayBatch,
  readBlackDragonIntradayScanState,
} from "../../../db/black-dragon-intraday";
import { readRiverStrategySignals, saveRiverStrategySignals, type RiverStrategySignal } from "../../../db/river-radar-intraday";
import {
  BLACK_DRAGON_INTRADAY_MODEL_VERSION,
  findFirstIntradayBlackDragonSignal,
  hasVerifiedIntradayBlackDragonEvidence,
  type BlackDragonIntradayBase,
} from "../../../lib/black-dragon-intraday";
import { isListedOrOtcStockCode } from "../../../lib/daily-strategy-signals";
import { loadTwseClosedTradingDates, resolveIntradaySignalCutoverDate } from "../../../lib/intraday-signal-session";
import type { RiverMinuteBar } from "../../../lib/river-intraday";
import { readKlineSnapshot } from "../../../db/kline-snapshots";
import { mergeBlackDragonCandleWindows } from "../../../lib/black-dragon-candles";
import { BLACK_DRAGON_SCAN_START_MINUTE, BLACK_DRAGON_SELECTION_VERSION, isBlackDragonScanAllowed, isBlackDragonSignalWindow } from "../../../lib/black-dragon-session";
import { parseHanStockOfficialPrimaryGroupMap } from "../../../lib/stock-primary-group";
import stockGroupsSource from "../../../data/stock_groups.py?raw";

type CandleEnvelope = { result?: { data?: { json?: { candles?: Array<Record<string, unknown>> } } } };
const SCAN_BATCH_SIZE = 80;
const CANDLE_BATCH_SIZE = 40;
const officialPrimaryGroupByCode = parseHanStockOfficialPrimaryGroupMap(stockGroupsSource);

function collectionWindow() {
  const taipei = new Date(Date.now() + 8 * 60 * 60_000);
  const minute = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
  return taipei.getUTCDay() >= 1 && taipei.getUTCDay() <= 5 && minute >= BLACK_DRAGON_SCAN_START_MINUTE && minute <= 13 * 60 + 35;
}

async function selectedSignalDate() {
  const closedDates = await loadTwseClosedTradingDates();
  return resolveIntradaySignalCutoverDate(new Date(), closedDates);
}

async function fetchCandleBatch(codes: string[]) {
  const path = codes.map(() => "stocks.candles").join(",");
  const input = Object.fromEntries(codes.map((code, index) => [index, { json: { ticker: code, interval: "5m" } }]));
  const response = await fetch(`https://www.hanstock.xyz/api/trpc/${path}?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Black-Dragon-Intraday/1.0" },
    signal: AbortSignal.timeout(45_000),
  });
  if (!response.ok) throw new Error(`black-dragon-intraday-bars-${response.status}`);
  const payload = await response.json() as CandleEnvelope[];
  return new Map(codes.map((code, index) => [code, payload[index]?.result?.data?.json?.candles ?? []]));
}

async function loadCandleBars(codes: string[], tradeDate: string) {
  const batches = Array.from({ length: Math.ceil(codes.length / CANDLE_BATCH_SIZE) }, (_, index) =>
    codes.slice(index * CANDLE_BATCH_SIZE, index * CANDLE_BATCH_SIZE + CANDLE_BATCH_SIZE));
  const settled = await Promise.allSettled(batches.map((batch) => fetchCandleBatch(batch)));
  const incoming = new Map<string, Array<Record<string, unknown>>>();
  settled.forEach((result) => {
    if (result.status === "fulfilled") result.value.forEach((bars, code) => incoming.set(code, bars));
  });
  const merged = new Map<string, RiverMinuteBar[]>();
  // Read one snapshot at a time, retain only this session, and release the long
  // historical payload before reading the next stock.
  for (const code of codes) {
    const snapshot = await readKlineSnapshot(code, "5m").catch(() => null);
    const bars = mergeBlackDragonCandleWindows(snapshot?.value.candles ?? [], incoming.get(code) ?? [], tradeDate);
    if (bars.length) merged.set(code, bars);
  }
  return merged;
}

function isBase(value: unknown): value is BlackDragonIntradayBase {
  if (!value || typeof value !== "object") return false;
  const base = value as Partial<BlackDragonIntradayBase>;
  return base.modelVersion === BLACK_DRAGON_INTRADAY_MODEL_VERSION
    && typeof base.code === "string"
    && (base.market === "twse" || base.market === "tpex")
    && typeof base.targetDate === "string"
    && typeof base.completedThrough === "string";
}

function isStoredBlackDragon(signal: RiverStrategySignal) {
  return signal.signalType === "black-dragon"
    && signal.strategyKind === "blackDragon"
    && signal.selectionRuleVersion === BLACK_DRAGON_SELECTION_VERSION
    && hasVerifiedIntradayBlackDragonEvidence(signal)
    && isBlackDragonSignalWindow(Number(signal.barTs))
    && isListedOrOtcStockCode(signal.code)
    && officialPrimaryGroupByCode.has(signal.code);
}

async function storedPayload(tradeDate: string) {
  const [signals, scanState] = await Promise.all([
    readRiverStrategySignals(tradeDate).catch(() => []),
    readBlackDragonIntradayScanState().catch(() => null),
  ]);
  return {
    ok: true,
    signalDate: tradeDate,
    selectionRuleVersion: BLACK_DRAGON_SELECTION_VERSION,
    scanState,
    signals: signals.filter(isStoredBlackDragon).sort((left, right) => right.barTs - left.barTs),
  };
}

export async function GET(request: NextRequest) {
  const signalDate = await selectedSignalDate();
  if (!isBlackDragonScanAllowed()) {
    return Response.json({ ok: true, signalDate, signals: [], scanState: null,
      refreshSkipped: "before-1100", message: "11:00 前暫不顯示訊號" },
    { headers: { "Cache-Control": "private, no-store" } });
  }
  const liveCollection = collectionWindow();
  const afterHoursBackfill = request.nextUrl.searchParams.get("backfill") === "1";
  if (request.nextUrl.searchParams.get("fast") === "1" || (!liveCollection && !afterHoursBackfill)) {
    return Response.json(await storedPayload(signalDate), { headers: { "Cache-Control": "private, no-store" } });
  }

  const previousTradeDate = resolveIntradaySignalCutoverDate(new Date(`${signalDate}T08:00:00+08:00`), await loadTwseClosedTradingDates());
  const indicators = (await readTechnicalMarketIndicators().catch(() => []))
    .filter((indicator) => officialPrimaryGroupByCode.has(indicator.code));
  const bases = indicators.flatMap((indicator) => {
    const candidates = Array.isArray(indicator.payload.blackDragonIntradayBases)
      ? indicator.payload.blackDragonIntradayBases
      : [indicator.payload.blackDragonIntradayBase];
    return candidates.flatMap((base) => isBase(base)
      && isListedOrOtcStockCode(base.code)
      && base.targetDate === signalDate
      && base.completedThrough === previousTradeDate ? [base] : []);
  }).sort((left, right) => left.code.localeCompare(right.code));
  const previousScan = await readBlackDragonIntradayScanState().catch(() => null);
  if (!liveCollection
    && previousScan?.tradeDate === signalDate
    && previousScan.total === bases.length
    && previousScan.status === "ready"
    && previousScan.processed >= bases.length) {
    return Response.json({
      ...(await storedPayload(signalDate)),
      baseCoverage: { ready: bases.length, total: indicators.length, missing: Math.max(0, indicators.length - bases.length) },
      refreshSkipped: "after-hours-backfill-complete",
    }, { headers: { "Cache-Control": "private, no-store" } });
  }
  const claim = await claimBlackDragonIntradayBatch(signalDate, bases.length, SCAN_BATCH_SIZE).catch(() => null);
  if (!claim || !bases.length) {
    return Response.json({ ...(await storedPayload(signalDate)), baseCoverage: { ready: bases.length, total: indicators.length, missing: Math.max(0, indicators.length - bases.length) }, refreshSkipped: !claim ? "scan-in-progress-or-recent" : "no-ready-bases" }, { headers: { "Cache-Control": "private, no-store" } });
  }

  const batch = bases.slice(claim.startIndex, claim.startIndex + SCAN_BATCH_SIZE);
  if (batch.length < Math.min(SCAN_BATCH_SIZE, bases.length)) {
    batch.push(...bases.slice(0, Math.min(SCAN_BATCH_SIZE, bases.length) - batch.length));
  }
  try {
    const barsByCode = await loadCandleBars(batch.map((base) => base.code), signalDate);
    const generated = batch.flatMap((base) => {
      const signal = findFirstIntradayBlackDragonSignal(base, barsByCode.get(base.code) ?? [], signalDate);
      if (!signal) return [];
      const tierLabel = signal.tier === "surge" ? "強爆量" : signal.tier === "selected" ? "精選" : "全部";
      return [{
        ...signal,
        direction: "bear",
        side: "bear",
        score: signal.maScore,
        signalType: "black-dragon",
        strategyKind: "blackDragon",
        strategyName: "創高的黑龍",
        groupName: officialPrimaryGroupByCode.get(signal.code),
        selectionRuleVersion: BLACK_DRAGON_SELECTION_VERSION,
        label: `創高的黑龍・盤中${tierLabel}`,
      } satisfies RiverStrategySignal];
    });
    const persisted = generated.length ? await saveRiverStrategySignals(generated) : true;
    if (!persisted) throw new Error("black-dragon-signal-write-failed");
    const finished = {
      ...claim,
      status: "ready" as const,
      processed: Math.min(bases.length, claim.processed + batch.length),
      barCoverage: barsByCode.size,
      signalCount: generated.length,
      reason: barsByCode.size ? undefined : "no-usable-intraday-bars",
    };
    await finishBlackDragonIntradayBatch(finished).catch(() => false);
    const stored = await storedPayload(signalDate);
    const combined = new Map<string, RiverStrategySignal>();
    [...generated, ...stored.signals].forEach((signal) => combined.set(`${signal.tradeDate}:${signal.code}`, signal));
    return Response.json({
      ...stored,
      ok: barsByCode.size > 0,
      scanState: finished,
      baseCoverage: { ready: bases.length, total: indicators.length, missing: Math.max(0, indicators.length - bases.length) },
      signals: [...combined.values()].sort((left, right) => right.barTs - left.barTs),
    }, { headers: { "Cache-Control": "private, no-store" } });
  } catch (error) {
    const failed = { ...claim, status: "error" as const, reason: error instanceof Error ? error.message : "black-dragon-intraday-scan-failed" };
    await finishBlackDragonIntradayBatch(failed).catch(() => false);
    return Response.json({ ...(await storedPayload(signalDate)), ok: false, scanState: failed }, { status: 503, headers: { "Cache-Control": "private, no-store" } });
  }
}
