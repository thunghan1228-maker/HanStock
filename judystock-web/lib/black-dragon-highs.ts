export const BLACK_DRAGON_MIN_HIGH_SESSIONS = 5;
export const BLACK_DRAGON_HIGH_PERIODS = [5, 10, 20, 50, 60, 120, 360] as const;

export type BlackDragonReferenceHighs = Record<number, number | null>;

// Ordinary stock quotes use at least cent precision; float32 provider noise
// must not turn an equal high (15.10000038 versus 15.1) into a breakout.
export function blackDragonExceedsHigh(high: number, reference: number) {
  return Math.round(high * 100) > Math.round(reference * 100);
}

/** Prior completed sessions only, in ascending date order. Never include the signal day. */
export function blackDragonReferenceHighs(priorBars: Array<{ high?: number | null }>): BlackDragonReferenceHighs {
  return Object.fromEntries(BLACK_DRAGON_HIGH_PERIODS.map((period) => {
    const highs = priorBars.slice(-period).map((bar) => bar.high);
    const complete = highs.length === period && highs.every((high) => typeof high === "number" && Number.isFinite(high) && high > 0);
    return [period, complete ? Math.max(...highs as number[]) : null];
  }));
}

/** A longer-period label cannot bypass the mandatory five-session price comparison. */
export function blackDragonNewHighPeriods(high: number, references?: BlackDragonReferenceHighs) {
  const fiveDayHigh = references?.[BLACK_DRAGON_MIN_HIGH_SESSIONS];
  if (!Number.isFinite(high) || typeof fiveDayHigh !== "number" || !Number.isFinite(fiveDayHigh)
    || fiveDayHigh <= 0 || !blackDragonExceedsHigh(high, fiveDayHigh)) return [];
  return BLACK_DRAGON_HIGH_PERIODS.filter((period) => {
    const reference = references?.[period];
    return typeof reference === "number" && Number.isFinite(reference) && reference > 0 && blackDragonExceedsHigh(high, reference);
  });
}
