import {
  averageOfficialFlow,
  calculateOfficialChipScore,
  type ChipWeights,
  type MarketRankingSeriesPoint,
} from "./chip-scoring.ts";

export type WeeklyChipMarket = "上市" | "上櫃" | "ETF";
export type WeeklyChipPeriod = "current" | "previous";
export type WeeklyChipDirection = "increase" | "decrease";

export type WeeklyChipStockInput = {
  code: string;
  name: string;
  market: WeeklyChipMarket;
  groupName: string;
  series: MarketRankingSeriesPoint[];
};

export type WeeklyChipComparisonRow = {
  key: string;
  code: string | null;
  name: string;
  groupName: string;
  market: WeeklyChipMarket | "族群";
  coveredCount: number;
  currentEndDate: string;
  previousEndDate: string;
  currentScore: number;
  previousScore: number;
  twoWeeksAgoScore: number | null;
  scoreChange: number;
  previousScoreChange: number | null;
  currentIncreaseRank: number | null;
  previousIncreaseRank: number | null;
  currentDecreaseRank: number | null;
  previousDecreaseRank: number | null;
};

type WeeklyScoreRow = Omit<WeeklyChipComparisonRow,
  "currentIncreaseRank" | "previousIncreaseRank" | "currentDecreaseRank" | "previousDecreaseRank"
>;

function round1(value: number) {
  return Math.round(value * 10) / 10;
}

export function weeklyScoreChangePercent(currentScore: number, previousScore: number) {
  if (previousScore === 0) return null;
  return Math.round(((currentScore - previousScore) / Math.abs(previousScore)) * 10_000) / 100;
}

export type WeeklyPerformanceItem = {
  ticker: string;
  returnPct: number | null;
};

export function summarizeWeeklyPerformance(items: WeeklyPerformanceItem[], direction: WeeklyChipDirection) {
  const available = items.filter((item): item is WeeklyPerformanceItem & { returnPct: number } => typeof item.returnPct === "number" && Number.isFinite(item.returnPct));
  if (!available.length) return { count: 0, averageReturnPct: null, hitRate: null, best: null, worst: null };
  const averageReturnPct = Math.round((available.reduce((sum, item) => sum + item.returnPct, 0) / available.length) * 100) / 100;
  const hitCount = available.filter((item) => direction === "increase" ? item.returnPct > 0 : item.returnPct < 0).length;
  return {
    count: available.length,
    averageReturnPct,
    hitRate: Math.round((hitCount / available.length) * 10_000) / 100,
    best: [...available].sort((a, b) => b.returnPct - a.returnPct)[0],
    worst: [...available].sort((a, b) => a.returnPct - b.returnPct)[0],
  };
}

function weekday(date: string) {
  const normalized = date.replaceAll("/", "-");
  const parsed = new Date(`${normalized}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? -1 : parsed.getUTCDay();
}

export function calculateCompletedWeeklyChipScores(series: MarketRankingSeriesPoint[], weights: ChipWeights) {
  const latestFridayIndex = series.findIndex((point) => weekday(point.date) === 5);
  if (latestFridayIndex < 0 || series.length < latestFridayIndex + 10) return null;
  const currentWeek = series.slice(latestFridayIndex, latestFridayIndex + 5);
  const previousWeek = series.slice(latestFridayIndex + 5, latestFridayIndex + 10);
  const twoWeeksAgoWeek = series.slice(latestFridayIndex + 10, latestFridayIndex + 15);
  if (currentWeek.length !== 5 || previousWeek.length !== 5) return null;
  const currentScore = calculateOfficialChipScore(averageOfficialFlow(currentWeek, 5), weights);
  const previousScore = calculateOfficialChipScore(averageOfficialFlow(previousWeek, 5), weights);
  const twoWeeksAgoScore = twoWeeksAgoWeek.length === 5
    ? calculateOfficialChipScore(averageOfficialFlow(twoWeeksAgoWeek, 5), weights)
    : null;
  return {
    currentEndDate: currentWeek[0].date,
    previousEndDate: previousWeek[0].date,
    currentScore,
    previousScore,
    twoWeeksAgoScore,
    scoreChange: round1(currentScore - previousScore),
    previousScoreChange: twoWeeksAgoScore === null ? null : round1(previousScore - twoWeeksAgoScore),
  };
}

function rankMap(rows: WeeklyScoreRow[], scoreKey: "currentScore" | "previousScore", direction: WeeklyChipDirection) {
  const accepted = rows.filter((row) => direction === "increase" ? row[scoreKey] > 0 : row[scoreKey] < 0);
  accepted.sort((a, b) => direction === "increase"
    ? b[scoreKey] - a[scoreKey] || a.key.localeCompare(b.key)
    : a[scoreKey] - b[scoreKey] || a.key.localeCompare(b.key));
  return new Map(accepted.map((row, index) => [row.key, index + 1]));
}

function attachRanks(rows: WeeklyScoreRow[]): WeeklyChipComparisonRow[] {
  const currentIncrease = rankMap(rows, "currentScore", "increase");
  const previousIncrease = rankMap(rows, "previousScore", "increase");
  const currentDecrease = rankMap(rows, "currentScore", "decrease");
  const previousDecrease = rankMap(rows, "previousScore", "decrease");
  return rows.map((row) => ({
    ...row,
    currentIncreaseRank: currentIncrease.get(row.key) ?? null,
    previousIncreaseRank: previousIncrease.get(row.key) ?? null,
    currentDecreaseRank: currentDecrease.get(row.key) ?? null,
    previousDecreaseRank: previousDecrease.get(row.key) ?? null,
  }));
}

export function buildWeeklyStockComparisons(rows: WeeklyChipStockInput[], weights: ChipWeights) {
  const scored = rows.flatMap((row): WeeklyScoreRow[] => {
    const weekly = calculateCompletedWeeklyChipScores(row.series, weights);
    if (!weekly) return [];
    return [{
      key: row.code,
      code: row.code,
      name: row.name,
      groupName: row.groupName,
      market: row.market,
      coveredCount: 1,
      ...weekly,
    }];
  });
  return attachRanks(scored);
}

export function buildWeeklyGroupComparisons(
  stocks: WeeklyChipComparisonRow[],
  groups: Array<{ name: string; codes: string[] }>,
) {
  const stocksByCode = new Map(stocks.flatMap((row) => row.code ? [[row.code, row] as const] : []));
  const scored = groups.flatMap((group): WeeklyScoreRow[] => {
    const members = group.codes.map((code) => stocksByCode.get(code)).filter((row): row is WeeklyChipComparisonRow => Boolean(row));
    if (members.length === 0) return [];
    const currentScore = round1(members.reduce((sum, row) => sum + row.currentScore, 0) / members.length);
    const previousScore = round1(members.reduce((sum, row) => sum + row.previousScore, 0) / members.length);
    const olderMembers = members.filter((row) => row.twoWeeksAgoScore !== null);
    const twoWeeksAgoScore = olderMembers.length === members.length
      ? round1(olderMembers.reduce((sum, row) => sum + (row.twoWeeksAgoScore ?? 0), 0) / olderMembers.length)
      : null;
    return [{
      key: group.name,
      code: null,
      name: group.name,
      groupName: `${members.length}／${group.codes.length} 檔`,
      market: "族群",
      coveredCount: members.length,
      currentEndDate: members[0].currentEndDate,
      previousEndDate: members[0].previousEndDate,
      currentScore,
      previousScore,
      twoWeeksAgoScore,
      scoreChange: round1(currentScore - previousScore),
      previousScoreChange: twoWeeksAgoScore === null ? null : round1(previousScore - twoWeeksAgoScore),
    }];
  });
  return attachRanks(scored);
}

export function selectWeeklyTop(
  rows: WeeklyChipComparisonRow[],
  period: WeeklyChipPeriod,
  direction: WeeklyChipDirection,
  limit = 20,
) {
  const scoreKey = period === "current" ? "currentScore" : "previousScore";
  return rows
    .filter((row) => direction === "increase" ? row[scoreKey] > 0 : row[scoreKey] < 0)
    .sort((a, b) => direction === "increase"
      ? b[scoreKey] - a[scoreKey] || a.key.localeCompare(b.key)
      : a[scoreKey] - b[scoreKey] || a.key.localeCompare(b.key))
    .slice(0, limit);
}
