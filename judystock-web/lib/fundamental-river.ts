export const FUNDAMENTAL_RIVER_BOUNDS = [0.618, 0.8, 1, 1.2, 1.382] as const;
export const FUNDAMENTAL_RIVER_MODEL_VERSION = "peer-fundamental-v1";

export type FundamentalPosition = "跌破特價" | "特價" | "便宜" | "貴" | "昂貴" | "突破昂貴";
export type FundamentalCandle = { date: string; close: number };
export type FinancialStatementPoint = { date: string; type: string; value: number };
export type QuarterFundamental = {
  date: string;
  effectiveDate: string;
  label: string;
  eps: number;
  ttmEps: number | null;
  revenue: number | null;
  operatingIncome: number | null;
  operatingMargin: number | null;
};
export type FundamentalRiverPoint = FundamentalCandle & { waterline: number };

export function fundamentalMedian(values: number[]) {
  const usable = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!usable.length) return null;
  const middle = Math.floor(usable.length / 2);
  return usable.length % 2 ? usable[middle] : (usable[middle - 1] + usable[middle]) / 2;
}

export function fundamentalRiverPosition(price: number, waterline: number): FundamentalPosition {
  const ratio = price / waterline;
  if (ratio < FUNDAMENTAL_RIVER_BOUNDS[0]) return "跌破特價";
  if (ratio < FUNDAMENTAL_RIVER_BOUNDS[1]) return "特價";
  if (ratio < FUNDAMENTAL_RIVER_BOUNDS[2]) return "便宜";
  if (ratio < FUNDAMENTAL_RIVER_BOUNDS[3]) return "貴";
  if (ratio < FUNDAMENTAL_RIVER_BOUNDS[4]) return "昂貴";
  return "突破昂貴";
}

export function quarterLabel(date: string) {
  const parsed = new Date(`${date.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  const quarter = Math.floor(parsed.getUTCMonth() / 3) + 1;
  return `${String(parsed.getUTCFullYear()).slice(2)}Q${quarter}`;
}

export function quarterEffectiveDate(date: string) {
  const parsed = new Date(`${date.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  const quarter = Math.floor(parsed.getUTCMonth() / 3) + 1;
  parsed.setUTCDate(parsed.getUTCDate() + (quarter === 4 ? 90 : quarter === 2 ? 60 : 45));
  return parsed.toISOString().slice(0, 10);
}

export function buildQuarterFundamentals(rows: FinancialStatementPoint[]): QuarterFundamental[] {
  const grouped = new Map<string, Map<string, number>>();
  for (const row of rows) {
    if (!row.date || !Number.isFinite(row.value)) continue;
    const metrics = grouped.get(row.date) ?? new Map<string, number>();
    metrics.set(row.type, row.value);
    grouped.set(row.date, metrics);
  }
  const raw = [...grouped.entries()].flatMap(([date, metrics]) => {
    const eps = metrics.get("EPS");
    if (eps === undefined) return [];
    const revenue = metrics.get("Revenue") ?? null;
    const operatingIncome = metrics.get("OperatingIncome") ?? null;
    return [{ date, effectiveDate: quarterEffectiveDate(date), label: quarterLabel(date), eps, ttmEps: null, revenue, operatingIncome, operatingMargin: revenue && operatingIncome !== null ? operatingIncome / revenue * 100 : null }];
  }).sort((a, b) => a.date.localeCompare(b.date));
  return raw.map((quarter, index) => ({
    ...quarter,
    ttmEps: index >= 3 ? raw.slice(index - 3, index + 1).reduce((sum, item) => sum + item.eps, 0) : null,
  }));
}

export function estimateFundamentalWaterline(input: {
  close: number | null;
  pe: number | null;
  pb: number | null;
  ttmEps: number | null;
  groupMedianPe: number | null;
  groupMedianPb: number | null;
}) {
  if (input.ttmEps !== null && input.ttmEps > 0 && input.groupMedianPe !== null && input.groupMedianPe > 0) {
    return { waterline: input.ttmEps * input.groupMedianPe, basis: "pe" as const };
  }
  const bvps = input.close !== null && input.pb !== null && input.pb > 0 ? input.close / input.pb : null;
  if (bvps !== null && input.groupMedianPb !== null && input.groupMedianPb > 0) {
    return { waterline: bvps * input.groupMedianPb, basis: "pb" as const };
  }
  if (input.close !== null && input.pe !== null && input.pe > 0 && input.groupMedianPe !== null && input.groupMedianPe > 0) {
    return { waterline: input.close / input.pe * input.groupMedianPe, basis: "pe" as const };
  }
  return { waterline: null, basis: null };
}

export function buildFundamentalRiverPoints(
  candles: FundamentalCandle[],
  quarters: QuarterFundamental[],
  groupMedianPe: number | null,
  fallbackWaterline: number | null,
) {
  const anchors = quarters.flatMap((quarter) => quarter.ttmEps !== null && quarter.ttmEps > 0 && groupMedianPe !== null && groupMedianPe > 0
    ? [{ effectiveDate: quarter.effectiveDate, waterline: quarter.ttmEps * groupMedianPe }]
    : []);
  if (!anchors.length) return fallbackWaterline === null ? [] : candles.map((candle) => ({ ...candle, waterline: fallbackWaterline }));
  return candles.flatMap((candle) => {
    let waterline: number | null = null;
    for (const anchor of anchors) {
      if (anchor.effectiveDate > candle.date.replaceAll("/", "-")) break;
      waterline = anchor.waterline;
    }
    return waterline === null ? [] : [{ ...candle, waterline }];
  });
}
