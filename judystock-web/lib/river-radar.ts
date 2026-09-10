export const RIVER_MA_PERIODS = [5, 10, 20, 60, 120, 240] as const;
export const RIVER_ENGINE_VERSION = "river-v1.0.0";

export type RiverMaPeriod = (typeof RIVER_MA_PERIODS)[number];
export type RiverStatus = "強多" | "偏多" | "中性" | "偏空" | "強空";
export type RiverSide = "bull" | "neutral" | "bear";

export type RiverScoreInput = {
  close: number;
  maValues: Record<number, number | null>;
  previousMaValues?: Record<number, number | null> | null;
  volumeRatio?: number | null;
  vwap?: number | null;
};

export type RiverScoreResult = {
  score: number;
  status: RiverStatus;
  side: RiverSide;
  maOrderScore: number;
  openingPct: number | null;
  components: {
    pricePosition: number;
    shortOrder: number;
    mediumLongOrder: number;
    slope: number;
    opening: number;
    volumeVwap: number;
  };
  confirmationState: "完整確認" | "量能待補";
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function round(value: number, digits = 1) {
  const base = 10 ** digits;
  return Math.round(value * base) / base;
}

function pairwiseBullCount(values: number[]) {
  let count = 0;
  let pairs = 0;
  for (let fast = 0; fast < values.length; fast += 1) {
    for (let slow = fast + 1; slow < values.length; slow += 1) {
      pairs += 1;
      if (values[fast] > values[slow]) count += 1;
      else if (values[fast] === values[slow]) count += .5;
    }
  }
  return { count, pairs };
}

function scaledPairs(values: number[], weight: number) {
  const { count, pairs } = pairwiseBullCount(values);
  return pairs ? count / pairs * weight : weight / 2;
}

export function riverStatus(score: number): { status: RiverStatus; side: RiverSide } {
  if (score >= 75) return { status: "強多", side: "bull" };
  if (score >= 60) return { status: "偏多", side: "bull" };
  if (score >= 40) return { status: "中性", side: "neutral" };
  if (score >= 25) return { status: "偏空", side: "bear" };
  return { status: "強空", side: "bear" };
}

export function calculateRiverScore(input: RiverScoreInput): RiverScoreResult | null {
  const mas = RIVER_MA_PERIODS.map((period) => input.maValues[period]);
  if (!Number.isFinite(input.close) || mas.some((value) => value === null || !Number.isFinite(value))) return null;
  const values = mas as number[];

  const pricePosition = values.reduce((score, ma) => score + (input.close > ma ? 1 : input.close === ma ? .5 : 0), 0) / values.length * 15;
  const shortOrder = scaledPairs(values.slice(0, 3), 20);
  const mediumLongOrder = scaledPairs(values.slice(2), 25);
  const maOrderScore = pairwiseBullCount(values).count;

  const previous = input.previousMaValues
    ? RIVER_MA_PERIODS.map((period) => input.previousMaValues?.[period] ?? null)
    : [];
  const slope = previous.length === values.length && previous.every((value) => value !== null && Number.isFinite(value))
    ? values.reduce((score, ma, index) => score + (ma > Number(previous[index]) ? 1 : ma === Number(previous[index]) ? .5 : 0), 0) / values.length * 15
    : 7.5;

  const openingPct = values[5] ? (values[0] - values[5]) / values[5] * 100 : null;
  const opening = openingPct === null ? 7.5 : clamp((openingPct + 5) / 10 * 15, 0, 15);

  const hasVolume = input.volumeRatio !== null && input.volumeRatio !== undefined && Number.isFinite(input.volumeRatio);
  const hasVwap = input.vwap !== null && input.vwap !== undefined && Number.isFinite(input.vwap);
  let volumeVwap = 5;
  if (hasVolume || hasVwap) {
    volumeVwap = 0;
    if (hasVwap) volumeVwap += input.close >= Number(input.vwap) ? 5 : 0;
    else volumeVwap += 2.5;
    if (hasVolume) volumeVwap += clamp(Number(input.volumeRatio) / 1.2 * 5, 0, 5);
    else volumeVwap += 2.5;
  }

  const components = {
    pricePosition: round(pricePosition),
    shortOrder: round(shortOrder),
    mediumLongOrder: round(mediumLongOrder),
    slope: round(slope),
    opening: round(opening),
    volumeVwap: round(volumeVwap),
  };
  const score = round(Object.values(components).reduce((sum, value) => sum + value, 0));
  const classification = riverStatus(score);
  return {
    score,
    ...classification,
    maOrderScore: round(maOrderScore),
    openingPct: openingPct === null ? null : round(openingPct, 2),
    components,
    confirmationState: hasVolume && hasVwap ? "完整確認" : "量能待補",
  };
}

