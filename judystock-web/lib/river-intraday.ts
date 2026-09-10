import { calculateRiverScore, RIVER_MA_PERIODS, type RiverScoreResult } from "./river-radar.ts";

export type RiverMinuteBar = { ts: number; open?: number; close: number; high?: number; low?: number; volume: number };
export type RiverFiveMinuteBar = { ts: number; open: number; close: number; high: number; low: number; volume: number };

export function aggregateRiverFiveMinuteBars(rows: RiverMinuteBar[]) {
  const buckets = new Map<number, RiverFiveMinuteBar>();
  for (const row of [...rows].sort((a, b) => a.ts - b.ts)) {
    if (!Number.isFinite(row.ts) || !Number.isFinite(row.close) || !Number.isFinite(row.volume)) continue;
    const taipei = new Date(row.ts + 8 * 60 * 60 * 1_000);
    const minute = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
    if (minute < 9 * 60 || minute > 13 * 60 + 30) continue;
    const bucketMinute = Math.floor(minute / 5) * 5;
    const bucketTs = row.ts - (minute - bucketMinute) * 60_000 - (row.ts % 60_000);
    const current = buckets.get(bucketTs);
    const high = Number.isFinite(row.high) ? Number(row.high) : row.close;
    const low = Number.isFinite(row.low) ? Number(row.low) : row.close;
    const open = Number.isFinite(row.open) ? Number(row.open) : row.close;
    buckets.set(bucketTs, current ? { ...current, close: row.close, high: Math.max(current.high, high), low: Math.min(current.low, low), volume: current.volume + Math.max(0, row.volume) } : { ts: bucketTs, open, close: row.close, high, low, volume: Math.max(0, row.volume) });
  }
  return [...buckets.values()].sort((a, b) => a.ts - b.ts);
}

function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function liveMas(baseSums: Record<number, number | null>, price: number) {
  return Object.fromEntries(RIVER_MA_PERIODS.map((period) => {
    const sum = baseSums[period];
    return [period, sum === null || !Number.isFinite(sum) ? null : (Number(sum) + price) / period];
  })) as Record<number, number | null>;
}

export function scoreRiverFiveMinuteBars(bars: RiverFiveMinuteBar[], baseSums: Record<number, number | null>) {
  let sessionAmount = 0;
  let sessionVolume = 0;
  return bars.map((bar, index) => {
    sessionAmount += bar.close * bar.volume;
    sessionVolume += bar.volume;
    const vwap = sessionVolume > 0 ? sessionAmount / sessionVolume : null;
    const baselineVolume = median(bars.slice(Math.max(0, index - 20), index).map((item) => item.volume).filter((value) => value > 0));
    const previousPrice = bars[index - 1]?.close ?? bar.close;
    const score = calculateRiverScore({ close: bar.close, maValues: liveMas(baseSums, bar.close), previousMaValues: liveMas(baseSums, previousPrice), volumeRatio: baselineVolume ? bar.volume / baselineVolume : null, vwap });
    return { ...bar, vwap, volumeRatio: baselineVolume ? bar.volume / baselineVolume : null, score };
  });
}

export function confirmedRiverDirection(scores: Array<RiverScoreResult | null>, bullThreshold = 75, bearThreshold = 25) {
  const latest = scores.at(-1);
  const previous = scores.at(-2);
  if (!latest || !previous) return null;
  if (latest.score >= bullThreshold && previous.score >= bullThreshold) return "bull" as const;
  if (latest.score <= bearThreshold && previous.score <= bearThreshold) return "bear" as const;
  return null;
}

export function firstRiverSelectionTimestamp(
  rows: Array<{ ts: number; score: RiverScoreResult }>,
  direction: "bull" | "bear",
  finalScore: number,
) {
  const valid = rows.filter((row) => Number.isFinite(row.ts) && Number.isFinite(row.score.score));
  if (!valid.length) return null;
  if (direction === "bull") {
    const target = Math.min(finalScore, Math.max(...valid.map((row) => row.score.score)));
    return valid.find((row) => row.score.score >= target)?.ts ?? valid.at(-1)!.ts;
  }
  const target = Math.max(finalScore, Math.min(...valid.map((row) => row.score.score)));
  return valid.find((row) => row.score.score <= target)?.ts ?? valid.at(-1)!.ts;
}

export type RiverMa20SelectionMatch = {
  ts: number;
  close: number;
  fiveMinuteMa20: number;
  dailyMa20: number;
};

export function firstRiverSelectionWithMa20Filters(
  rows: Array<{ ts: number; close: number; score: RiverScoreResult }>,
  direction: "bull" | "bear",
  finalScore: number,
  precedingFiveMinuteCloses: number[],
  dailyMa20BaseSum: number | null,
) {
  const ordered = [...rows]
    .filter((row) => Number.isFinite(row.ts) && Number.isFinite(row.close) && Number.isFinite(row.score.score))
    .sort((left, right) => left.ts - right.ts);
  if (!ordered.length || !Number.isFinite(dailyMa20BaseSum)) return null;
  const target = direction === "bull"
    ? Math.min(finalScore, Math.max(...ordered.map((row) => row.score.score)))
    : Math.max(finalScore, Math.min(...ordered.map((row) => row.score.score)));
  const closes = precedingFiveMinuteCloses.filter(Number.isFinite).slice(-19);
  if (closes.length < 19) return null;
  for (const row of ordered) {
    closes.push(row.close);
    if (closes.length > 20) closes.shift();
    const fiveMinuteMa20 = closes.reduce((sum, close) => sum + close, 0) / 20;
    const dailyMa20 = (Number(dailyMa20BaseSum) + row.close) / 20;
    const scoreMatched = direction === "bull" ? row.score.score >= target : row.score.score <= target;
    const positionMatched = direction === "bull"
      ? row.close > fiveMinuteMa20 && row.close > dailyMa20
      : row.close < fiveMinuteMa20 && row.close < dailyMa20;
    if (scoreMatched && positionMatched) return { ts: row.ts, close: row.close, fiveMinuteMa20, dailyMa20 } satisfies RiverMa20SelectionMatch;
  }
  return null;
}

/**
 * Only confirms the newest five-minute candle.  A live scan cannot know that a
 * stock belonged to the current ranked group earlier in the session, so using
 * the first historical candle would backdate a newly observed signal to 09:00.
 */
export function latestRiverSelectionWithMa20Filters(
  rows: Array<{ ts: number; close: number; score: RiverScoreResult }>,
  direction: "bull" | "bear",
  finalScore: number,
  precedingFiveMinuteCloses: number[],
  dailyMa20BaseSum: number | null,
) {
  const ordered = [...rows]
    .filter((row) => Number.isFinite(row.ts) && Number.isFinite(row.close) && Number.isFinite(row.score.score))
    .sort((left, right) => left.ts - right.ts);
  if (!ordered.length || !Number.isFinite(dailyMa20BaseSum)) return null;
  const target = direction === "bull"
    ? Math.min(finalScore, Math.max(...ordered.map((row) => row.score.score)))
    : Math.max(finalScore, Math.min(...ordered.map((row) => row.score.score)));
  const closes = precedingFiveMinuteCloses.filter(Number.isFinite).slice(-19);
  if (closes.length < 19) return null;
  let latest: RiverMa20SelectionMatch | null = null;
  ordered.forEach((row, index) => {
    closes.push(row.close);
    if (closes.length > 20) closes.shift();
    if (index !== ordered.length - 1) return;
    const fiveMinuteMa20 = closes.reduce((sum, close) => sum + close, 0) / 20;
    const dailyMa20 = (Number(dailyMa20BaseSum) + row.close) / 20;
    const scoreMatched = direction === "bull" ? row.score.score >= target : row.score.score <= target;
    const positionMatched = direction === "bull"
      ? row.close > fiveMinuteMa20 && row.close > dailyMa20
      : row.close < fiveMinuteMa20 && row.close < dailyMa20;
    if (scoreMatched && positionMatched) latest = { ts: row.ts, close: row.close, fiveMinuteMa20, dailyMa20 };
  });
  return latest;
}
