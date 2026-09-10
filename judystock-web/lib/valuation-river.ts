// A 160-session median is slow enough not to reclassify a high-priced stock as
// "cheap" after only a short pullback, while still adapting within the current
// trading year.
export const VALUATION_RIVER_WINDOW = 160;
export const VALUATION_RIVER_MODEL_VERSION = "valuation-160-v2";

export type ValuationRiverCandle = { date: string; close: number };
export type ValuationRiverPoint = ValuationRiverCandle & { base: number };
export type ValuationDistanceFilter = "all" | "near" | "clear" | "strong" | "extreme";

export function valuationMedian(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function valuationRiverPosition(price: number, base: number) {
  const ratio = price / base;
  if (ratio < .8) return "特價" as const;
  // "便宜" requires a meaningful discount. A price only fractionally below
  // the median stays on the neutral-to-rich side instead of flooding the
  // cheap list with roughly half the market.
  if (ratio < .9) return "便宜" as const;
  if (ratio < 1.2) return "偏貴" as const;
  return "昂貴" as const;
}

export function valuationDistanceFilterMatches(distancePct: number | null | undefined, filter: ValuationDistanceFilter) {
  if (filter === "all") return distancePct !== null && distancePct !== undefined && Number.isFinite(distancePct);
  if (distancePct === null || distancePct === undefined || !Number.isFinite(distancePct)) return false;
  const distance = Math.abs(distancePct);
  if (filter === "near") return distance <= 5;
  if (filter === "clear") return distance > 5 && distance <= 15;
  if (filter === "strong") return distance > 15 && distance <= 30;
  return distance > 30;
}

export function buildValuationRiverPoints(candles: ValuationRiverCandle[], maximumPoints = 220): ValuationRiverPoint[] {
  // Calculate every rolling 160-session median before reducing SVG points.
  // Sampling first silently stretches the window (260 sessions become roughly
  // 130 points), causing the plotted bands to disagree with the detail label.
  const complete = candles.map((point, index) => {
    const start = Math.max(0, index - (VALUATION_RIVER_WINDOW - 1));
    return { ...point, base: valuationMedian(candles.slice(start, index + 1).map((item) => item.close)) ?? point.close };
  });
  if (complete.length <= maximumPoints) return complete;
  const step = Math.ceil(complete.length / maximumPoints);
  return complete.filter((_, index) => index % step === 0 || index === complete.length - 1);
}
