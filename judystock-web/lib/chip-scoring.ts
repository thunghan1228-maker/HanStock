import type { BattleRuntimeSettings } from "./battle-settings";

export type OfficialFlowScores = Record<"foreign" | "trust" | "dealer" | "hedge", number>;
export type MarketRankingSeriesPoint = OfficialFlowScores & { date: string };
export type ChipWeights = BattleRuntimeSettings["chipWeights"];

export function averageOfficialFlow(series: MarketRankingSeriesPoint[], days: number): OfficialFlowScores {
  const points = series.slice(0, days);
  const divisor = points.length || 1;
  return points.reduce<OfficialFlowScores>(
    (totals, point) => ({
      foreign: totals.foreign + point.foreign / divisor,
      trust: totals.trust + point.trust / divisor,
      dealer: totals.dealer + point.dealer / divisor,
      hedge: totals.hedge + point.hedge / divisor,
    }),
    { foreign: 0, trust: 0, dealer: 0, hedge: 0 },
  );
}

export function calculateOfficialChipScore(factors: OfficialFlowScores, weights: ChipWeights) {
  const keys: Array<keyof OfficialFlowScores> = ["foreign", "trust", "dealer", "hedge"];
  const total = keys.reduce((sum, key) => sum + weights[key], 0) || 1;
  const weighted = keys.reduce((sum, key) => sum + factors[key] * weights[key], 0);
  return Math.round((weighted / total) * 10) / 10;
}

export function calculateCombinedChipScore(todayScore: number, fiveDayAverage: number) {
  return Math.round((todayScore * 0.6 + fiveDayAverage * 0.4) * 10) / 10;
}

export type ChipRankMovementInput = { ticker: string; currentScore: number; previousScore: number };
export type ChipRankMovement = { currentRank: number; previousRank: number; change: number };

export function calculateChipRankMovements(rows: ChipRankMovementInput[]) {
  const rank = (scoreKey: "currentScore" | "previousScore") => new Map(
    [...rows]
      .sort((a, b) => b[scoreKey] - a[scoreKey] || a.ticker.localeCompare(b.ticker))
      .map((row, index) => [row.ticker, index + 1]),
  );
  const currentRanks = rank("currentScore");
  const previousRanks = rank("previousScore");
  return new Map(rows.map((row) => {
    const currentRank = currentRanks.get(row.ticker) ?? 0;
    const previousRank = previousRanks.get(row.ticker) ?? 0;
    return [row.ticker, { currentRank, previousRank, change: previousRank - currentRank } satisfies ChipRankMovement];
  }));
}
