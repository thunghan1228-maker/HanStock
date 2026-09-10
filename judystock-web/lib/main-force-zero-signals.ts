export type MainForceMinuteBar = {
  ts: number;
  open?: number | null;
  high: number;
  low: number;
  close: number;
  volume: number;
  main_net_volume?: number | null;
  main_net_amount?: number | null;
  main_force_available?: boolean | null;
};

export type MainForceHistoricalVolumes = Map<string, Map<number, number>>;

export type MainForceZeroSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "mainForceStrongBullish" | "mainForceStrongBearish";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

export const MAIN_FORCE_FILTER_MARKER = "A～D同步濾網 V1";

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const SESSION_END_MINUTE = 13 * 60 + 30;
const FORMAL_START_MINUTE = 9 * 60 + 5;
const RECENT_MEDIAN_START_MINUTE = 9 * 60 + 20;
const SYNC_WINDOW_MS = 3 * 60 * 1_000;
const MIN_DISTANCE_PCT = 0.2;
const MAX_DISTANCE_PCT = 1.2;
const MIN_AMOUNT_RATE_PCT = 1;
const VOLUME_MULTIPLE = 1.5;

type NormalizedBar = {
  ts: number;
  tradeDate: string;
  minute: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  netVolume: number;
  netAmount: number;
};

type FilterCandidate = {
  bar: NormalizedBar;
  bullish: boolean;
  cumulativeNetVolume: number;
  amountRatePct: number;
  vwap: number;
  distancePct: number;
  mainCrossTs: number;
  priceCrossTs: number;
  recent20Median: number | null;
};

function timestampMs(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return null;
  return number < 1_000_000_000_000 ? number * 1_000 : number;
}

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function minuteLabel(timestamp: number) {
  const { minute } = taipeiParts(timestamp);
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function median(values: number[]) {
  const sorted = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (sorted.length === 0) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function signedNumber(value: number, maximumFractionDigits = 2) {
  return `${value > 0 ? "+" : ""}${value.toLocaleString("zh-TW", { maximumFractionDigits })}`;
}

function normalizeBars(inputBars: MainForceMinuteBar[]) {
  return inputBars.flatMap((bar) => {
    const ts = timestampMs(bar.ts);
    const close = finiteNumber(bar.close);
    const volume = finiteNumber(bar.volume);
    const netVolume = finiteNumber(bar.main_net_volume);
    const netAmount = finiteNumber(bar.main_net_amount);
    if (ts === null || close === null || volume === null || netVolume === null || netAmount === null || close <= 0 || volume < 0 || bar.main_force_available === false) return [];
    const { tradeDate, minute } = taipeiParts(ts);
    if (minute < SESSION_START_MINUTE || minute > SESSION_END_MINUTE) return [];
    return [{
      ts,
      tradeDate,
      minute,
      high: finiteNumber(bar.high) ?? close,
      low: finiteNumber(bar.low) ?? close,
      close,
      volume,
      netVolume,
      netAmount,
    }];
  }).sort((left, right) => left.ts - right.ts);
}

function collectFilterCandidates(bars: NormalizedBar[]) {
  const candidates: FilterCandidate[] = [];
  let cumulativeNetVolume = 0;
  let cumulativeNetAmount = 0;
  let cumulativeTurnover = 0;
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;
  let lastNonzeroSign: -1 | 0 | 1 = 0;
  let regime: -1 | 1 | null = null;
  let mainCrossTs: number | null = null;
  let stableBars = 0;
  let previousVwapDistancePct = 0;
  let lastBullVwapCrossTs: number | null = null;
  let lastBearVwapCrossTs: number | null = null;
  const recentVolumes: number[] = [];

  for (const bar of bars) {
    const recent20Median = recentVolumes.length >= 20 ? median(recentVolumes.slice(-20)) : null;
    cumulativeNetVolume += bar.netVolume;
    cumulativeNetAmount += bar.netAmount;
    cumulativeTurnover += bar.close * bar.volume * 1_000;
    if (bar.volume > 0) {
      cumulativePriceVolume += ((bar.high + bar.low + bar.close) / 3) * bar.volume;
      cumulativeVolume += bar.volume;
    }
    if (cumulativeVolume <= 0 || cumulativeTurnover <= 0) {
      recentVolumes.push(bar.volume);
      continue;
    }

    const vwap = cumulativePriceVolume / cumulativeVolume;
    const distancePct = (bar.close / vwap - 1) * 100;
    if (previousVwapDistancePct <= 0 && distancePct > 0) lastBullVwapCrossTs = bar.ts;
    if (previousVwapDistancePct >= 0 && distancePct < 0) lastBearVwapCrossTs = bar.ts;
    previousVwapDistancePct = distancePct;

    const sign: -1 | 0 | 1 = cumulativeNetVolume > 0 ? 1 : cumulativeNetVolume < 0 ? -1 : 0;
    if (sign !== 0 && sign !== lastNonzeroSign && lastNonzeroSign !== 0) {
      regime = sign;
      mainCrossTs = bar.ts;
      stableBars = 1;
    } else if (regime !== null && sign === regime) {
      stableBars += 1;
    } else if (regime !== null && sign !== 0 && sign !== regime) {
      regime = null;
      mainCrossTs = null;
      stableBars = 0;
    }
    if (sign !== 0) lastNonzeroSign = sign;

    if (bar.minute >= FORMAL_START_MINUTE && regime !== null && mainCrossTs !== null && stableBars >= 2) {
      const bullish = regime === 1;
      const amountRatePct = cumulativeNetAmount / cumulativeTurnover * 100;
      const distancePassed = bullish
        ? distancePct >= MIN_DISTANCE_PCT && distancePct <= MAX_DISTANCE_PCT
        : distancePct <= -MIN_DISTANCE_PCT && distancePct >= -MAX_DISTANCE_PCT;
      const amountPassed = bullish ? amountRatePct >= MIN_AMOUNT_RATE_PCT : amountRatePct <= -MIN_AMOUNT_RATE_PCT;
      const priceCrossTs = bullish ? lastBullVwapCrossTs : lastBearVwapCrossTs;
      const syncPassed = priceCrossTs !== null && Math.abs(priceCrossTs - mainCrossTs) <= SYNC_WINDOW_MS;
      const latestIgnitionTs = priceCrossTs === null ? mainCrossTs : Math.max(mainCrossTs, priceCrossTs);
      const openingUpgrade = bar.minute === FORMAL_START_MINUTE && taipeiParts(mainCrossTs).minute < FORMAL_START_MINUTE;
      const triggerWithinIgnitionWindow = openingUpgrade || bar.ts - latestIgnitionTs <= SYNC_WINDOW_MS;

      if (distancePassed && amountPassed && syncPassed && triggerWithinIgnitionWindow && priceCrossTs !== null) {
        candidates.push({
          bar,
          bullish,
          cumulativeNetVolume,
          amountRatePct,
          vwap,
          distancePct,
          mainCrossTs,
          priceCrossTs,
          recent20Median,
        });
      }
    }

    recentVolumes.push(bar.volume);
  }

  return candidates;
}

function groupedBars(inputBars: MainForceMinuteBar[]) {
  const groups = new Map<string, NormalizedBar[]>();
  for (const bar of normalizeBars(inputBars)) {
    const group = groups.get(bar.tradeDate) ?? [];
    group.push(bar);
    groups.set(bar.tradeDate, group);
  }
  return groups;
}

export function hasPotentialMainForceABCDSignal(inputBars: MainForceMinuteBar[]) {
  for (const bars of groupedBars(inputBars).values()) {
    if (collectFilterCandidates(bars).length > 0) return true;
  }
  return false;
}

export function calculateMainForceZeroSignals(
  ticker: string,
  name: string,
  inputBars: MainForceMinuteBar[],
  historicalVolumes: MainForceHistoricalVolumes = new Map(),
) {
  const results: MainForceZeroSignal[] = [];

  for (const [tradeDate, bars] of groupedBars(inputBars)) {
    const priorDates = [...historicalVolumes.keys()].filter((date) => date < tradeDate).sort().slice(-5);
    if (priorDates.length < 5) continue;
    const historicalMedianByMinute = new Map<number, number>();
    for (let minute = SESSION_START_MINUTE; minute <= SESSION_END_MINUTE; minute += 1) {
      historicalMedianByMinute.set(minute, median(priorDates.map((date) => historicalVolumes.get(date)?.get(minute) ?? 0)) ?? 0);
    }

    const emittedDirections = new Set<-1 | 1>();
    for (const candidate of collectFilterCandidates(bars)) {
      const direction: -1 | 1 = candidate.bullish ? 1 : -1;
      if (emittedDirections.has(direction)) continue;
      const historicalMedianVolume = historicalMedianByMinute.get(candidate.bar.minute) ?? 0;
      const effectiveBaselineVolume = candidate.bar.minute >= RECENT_MEDIAN_START_MINUTE && candidate.recent20Median !== null
        ? Math.max(historicalMedianVolume, candidate.recent20Median)
        : historicalMedianVolume;
      if (effectiveBaselineVolume <= 0 || candidate.bar.volume < effectiveBaselineVolume * VOLUME_MULTIPLE) continue;

      const volumeRatio = candidate.bar.volume / effectiveBaselineVolume;
      results.push({
        tradeDate,
        ticker,
        name,
        kind: candidate.bullish ? "mainForceStrongBullish" : "mainForceStrongBearish",
        label: candidate.bullish ? "主力累計強勢翻多" : "主力累計強勢翻空",
        barTs: candidate.bar.ts,
        price: candidate.bar.close,
        note: [
          MAIN_FORCE_FILTER_MARKER,
          `主力零軸 ${minuteLabel(candidate.mainCrossTs)}`,
          `VWAP穿越 ${minuteLabel(candidate.priceCrossTs)}`,
          `主力淨額率 ${signedNumber(candidate.amountRatePct)}%`,
          `距VWAP ${signedNumber(candidate.distancePct)}%`,
          `量比 ${volumeRatio.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`,
          `累計 ${signedNumber(candidate.cumulativeNetVolume, 1)} 張`,
        ].join("｜"),
      });
      emittedDirections.add(direction);
    }
  }

  return results.sort((left, right) => left.barTs - right.barTs);
}
