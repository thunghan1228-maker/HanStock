import { calculateMaArrangement } from "./ma-arrangement.ts";
import { MA_SCORE_PERIODS } from "./ma-score-ranking.ts";
import { blackDragonNewHighPeriods, blackDragonExceedsHigh } from "./black-dragon-highs.ts";
import { aggregateRiverFiveMinuteBars, type RiverMinuteBar } from "./river-intraday.ts";
import { isBlackDragonSignalWindow } from "./black-dragon-session.ts";

export const BLACK_DRAGON_INTRADAY_MODEL_VERSION = "black-dragon-intraday-volume-v2";

export type BlackDragonIntradayBase = {
  modelVersion: string;
  code: string;
  name: string;
  market: "twse" | "tpex";
  targetDate: string;
  completedThrough: string;
  previousClose: number;
  sessionOpen?: number | null;
  maLiveBaseSums: Record<number, number | null>;
  previousMaValues: Record<number, number | null>;
  referenceHighs: Record<number, number | null>;
  averageVolume20d: number;
};

export type BlackDragonIntradaySignal = {
  code: string;
  name: string;
  market: "twse" | "tpex";
  tradeDate: string;
  barTs: number;
  price: number;
  changePct: number;
  maScore: number;
  maLabel: string;
  newHighPeriods: number[];
  cumulativeVolume: number;
  averageVolume20d: number;
  projectedVolumeRatio: number;
  cumulativeTurnover: number;
  blackBodyPct: number;
  tier: "all" | "selected" | "surge";
  sessionOpen: number;
  openingBarTs: number;
  signalHigh: number;
  referenceHigh5: number;
  referenceThrough: string;
};

const SESSION_START_MINUTE = 9 * 60;
const SESSION_MINUTES = 270;

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + 8 * 60 * 60_000);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function liveMas(baseSums: Record<number, number | null>, price: number) {
  return Object.fromEntries(MA_SCORE_PERIODS.map((period) => {
    const sum = baseSums[period];
    return [period, typeof sum === "number" && Number.isFinite(sum) ? (sum + price) / period : null];
  })) as Record<number, number | null>;
}

function tierFor(averageVolume20d: number, volumeRatio: number, turnover: number, bodyPct: number) {
  if (averageVolume20d >= 1_000_000 && volumeRatio >= 2 && turnover >= 100_000_000 && bodyPct >= 1.5) return "surge" as const;
  if (averageVolume20d >= 1_000_000 && volumeRatio >= 1.5 && turnover >= 50_000_000 && bodyPct >= 1.5) return "selected" as const;
  return "all" as const;
}

/**
 * The triggering five-minute candle itself must exceed the previous five-day
 * high. A morning spike cannot qualify an unrelated afternoon candle.
 */
export function findFirstIntradayBlackDragonSignal(
  base: BlackDragonIntradayBase,
  minuteBars: RiverMinuteBar[],
  tradeDate: string,
) {
  if (base.modelVersion !== BLACK_DRAGON_INTRADAY_MODEL_VERSION || base.targetDate !== tradeDate || base.completedThrough >= tradeDate
    || !Number.isFinite(base.previousClose) || base.previousClose <= 0
    || !Number.isFinite(base.averageVolume20d) || base.averageVolume20d <= 0) return null;

  const valid = minuteBars.filter((bar) => [bar.open, bar.high, bar.low, bar.close].every((value) => typeof value === "number" && Number.isFinite(value) && value > 0)
    && Number(bar.high) >= Math.max(Number(bar.open), bar.close) && Number(bar.low) <= Math.min(Number(bar.open), bar.close));
  const openingTs = Date.parse(`${tradeDate}T09:00:00+08:00`);
  if (!valid.some((bar) => bar.ts === openingTs)) return null;
  const bars = aggregateRiverFiveMinuteBars(valid)
    .filter((bar) => taipeiParts(bar.ts).tradeDate === tradeDate)
    .sort((left, right) => left.ts - right.ts);
  const sessionOpen = bars[0]?.open;
  if (bars[0]?.ts !== openingTs || !Number.isFinite(sessionOpen) || Number(sessionOpen) <= 0) return null;
  if (typeof base.sessionOpen === "number" && Number.isFinite(base.sessionOpen)
    && Math.abs(base.sessionOpen - Number(sessionOpen)) > Math.max(0.00001, base.sessionOpen * 0.000001)) return null;

  let cumulativeVolume = 0;
  let cumulativeTurnover = 0;
  for (const bar of bars) {
    cumulativeVolume += Math.max(0, bar.volume);
    cumulativeTurnover += Math.max(0, bar.close * bar.volume);
    if (!isBlackDragonSignalWindow(bar.ts)) continue;
    const arrangement = calculateMaArrangement({
      close: bar.close,
      maValues: liveMas(base.maLiveBaseSums, bar.close),
      previousMaValues: base.previousMaValues,
    });
    if (!arrangement || arrangement.score < 10 || bar.close >= Number(sessionOpen)) continue;
    const newHighPeriods = blackDragonNewHighPeriods(bar.high, base.referenceHighs);
    if (!newHighPeriods.length) continue;
    const minute = taipeiParts(bar.ts).minute;
    const sessionProgress = Math.min(1, Math.max(0.08, (minute - SESSION_START_MINUTE + 5) / SESSION_MINUTES));
    const projectedVolumeRatio = cumulativeVolume / (base.averageVolume20d * sessionProgress);
    const blackBodyPct = (Number(sessionOpen) - bar.close) / Number(sessionOpen) * 100;
    return {
      code: base.code,
      name: base.name,
      market: base.market,
      tradeDate,
      barTs: bar.ts,
      price: bar.close,
      changePct: (bar.close / base.previousClose - 1) * 100,
      maScore: arrangement.score,
      maLabel: arrangement.label,
      newHighPeriods,
      cumulativeVolume,
      averageVolume20d: base.averageVolume20d,
      projectedVolumeRatio,
      cumulativeTurnover,
      blackBodyPct,
      tier: tierFor(base.averageVolume20d, projectedVolumeRatio, cumulativeTurnover, blackBodyPct),
      sessionOpen: Number(sessionOpen),
      openingBarTs: openingTs,
      signalHigh: bar.high,
      referenceHigh5: Number(base.referenceHighs[5]),
      referenceThrough: base.completedThrough,
    } satisfies BlackDragonIntradaySignal;
  }
  return null;
}

/** Persisted candidates must carry the numeric evidence for the current rule. */
export function hasVerifiedIntradayBlackDragonEvidence(signal: Partial<BlackDragonIntradaySignal>) {
  return typeof signal.tradeDate === "string"
    && signal.openingBarTs === Date.parse(`${signal.tradeDate}T09:00:00+08:00`)
    && [signal.price, signal.sessionOpen, signal.signalHigh, signal.referenceHigh5].every((value) => typeof value === "number" && Number.isFinite(value) && value > 0)
    && blackDragonExceedsHigh(Number(signal.signalHigh), Number(signal.referenceHigh5))
    && Number(signal.signalHigh) >= Number(signal.price)
    && Number(signal.price) < Number(signal.sessionOpen)
    && Number(signal.maScore) >= 10;
}
