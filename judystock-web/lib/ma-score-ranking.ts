import { calculateMaArrangement } from "./ma-arrangement.ts";

export const MA_SCORE_PERIODS = [5, 10, 20, 60, 120, 240] as const;
export const NEW_HIGH_PERIODS = [5, 10, 20, 60, 120, 360] as const;

export type MaScoreSourceBar = {
  code: string;
  name?: string;
  market: "twse" | "tpex";
  close: number;
};

export type MaScoreSnapshot = { tradeDate: string; rows: MaScoreSourceBar[] };

export type MaScoreRankingRow = {
  code: string;
  name: string;
  market: "twse" | "tpex";
  date: string;
  close: number;
  score: number;
  baseScore: number;
  arrangementScore: number;
  arrangementBonus: number;
  label: string;
  bullConfirmed: boolean;
  bearConfirmed: boolean;
  maValues: Record<number, number>;
  aboveMaPeriods: number[];
  newHighPeriods: number[];
};

export type MaScoreRegular = {
  code: string;
  name: string;
  market: "twse" | "tpex";
  appearances: number;
  sampleDays: number;
  recentFive: boolean[];
  score: number | null;
  rank: number | null;
};

function average(values: number[]) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function maArrangementBonus(score: number) {
  if (score === 15) return 3;
  if (score >= 12) return 2;
  if (score >= 9) return 1;
  return 0;
}

function scoreHistoryAtDate(bars: Array<MaScoreSourceBar & { date: string }>, date: string) {
  const firstUsable = bars.findIndex((bar) => bar.date <= date);
  if (firstUsable < 0) return null;
  // No score component needs more than 360 sessions. Capping each slice keeps
  // historical regular rankings from repeatedly copying years of unused bars.
  const usable = bars.slice(firstUsable, firstUsable + 360);
  if (usable.length < 240) return null;
  const closes = usable.map((bar) => bar.close);
  const maValues = Object.fromEntries(MA_SCORE_PERIODS.map((period) => [period, average(closes.slice(0, period))])) as Record<number, number>;
  const previousMaValues = Object.fromEntries(MA_SCORE_PERIODS.map((period) => [period, closes.length >= period + 1 ? average(closes.slice(1, period + 1)) : maValues[period]])) as Record<number, number>;
  const arrangement = calculateMaArrangement({ close: closes[0], maValues, previousMaValues });
  if (!arrangement) return null;
  const aboveMaPeriods = MA_SCORE_PERIODS.filter((period) => closes[0] >= maValues[period]);
  const newHighPeriods = NEW_HIGH_PERIODS.filter((period) => closes.length >= period && closes[0] >= Math.max(...closes.slice(0, period)));
  const baseScore = aboveMaPeriods.length + newHighPeriods.length;
  const arrangementBonus = maArrangementBonus(arrangement.score);
  return {
    close: closes[0],
    score: baseScore + arrangementBonus,
    baseScore,
    arrangementScore: arrangement.score,
    arrangementBonus,
    label: arrangement.label,
    bullConfirmed: arrangement.bullConfirmed,
    bearConfirmed: arrangement.bearConfirmed,
    maValues: Object.fromEntries(MA_SCORE_PERIODS.map((period) => [period, Math.round(maValues[period] * 100) / 100])) as Record<number, number>,
    aboveMaPeriods,
    newHighPeriods,
  };
}

function prepareHistory(snapshots: MaScoreSnapshot[]) {
  const orderedSnapshots = [...snapshots].sort((a, b) => b.tradeDate.localeCompare(a.tradeDate));
  const byCode = new Map<string, Array<MaScoreSourceBar & { date: string }>>();
  orderedSnapshots.forEach((snapshot) => snapshot.rows.forEach((row) => {
    if (!Number.isFinite(row.close)) return;
    const current = byCode.get(row.code) ?? [];
    current.push({ ...row, date: snapshot.tradeDate });
    byCode.set(row.code, current);
  }));
  return { orderedSnapshots, byCode };
}

function rankingFromHistory(byCode: Map<string, Array<MaScoreSourceBar & { date: string }>>, date: string) {
  return [...byCode.entries()].flatMap(([code, bars]) => {
    const source = bars.find((bar) => bar.date <= date);
    const scored = scoreHistoryAtDate(bars, date);
    return source && scored ? [{ code, name: source.name ?? code, market: source.market, date, ...scored }] : [];
  }).sort((a, b) => b.score - a.score || b.close - a.close || a.code.localeCompare(b.code));
}

export function buildMaScoreRanking(snapshots: MaScoreSnapshot[], requestedDate?: string) {
  const { orderedSnapshots, byCode } = prepareHistory(snapshots);
  const availableDates = orderedSnapshots.map((snapshot) => snapshot.tradeDate);
  const date = requestedDate && availableDates.includes(requestedDate) ? requestedDate : availableDates[0] ?? "—";
  const rows = rankingFromHistory(byCode, date);
  return { date, availableDates, rows };
}

export function buildMaScoreRegulars(snapshots: MaScoreSnapshot[], endingDate: string, lookback = 20, dailyTop = 10) {
  const { orderedSnapshots, byCode } = prepareHistory(snapshots);
  const dates = orderedSnapshots.map((snapshot) => snapshot.tradeDate).filter((date) => date <= endingDate).slice(0, Math.max(1, lookback));
  // Reuse the prepared per-stock history for every comparison day. The old
  // path rebuilt the full 2,000-stock map up to twenty times per request.
  const dailyRankings = dates.map((date) => rankingFromHistory(byCode, date).slice(0, dailyTop));
  const latestRank = new Map(dailyRankings[0]?.map((row, index) => [row.code, index + 1]) ?? []);
  const latestScore = new Map(dailyRankings[0]?.map((row) => [row.code, row.score]) ?? []);
  const identity = new Map<string, Pick<MaScoreRegular, "code" | "name" | "market">>();
  dailyRankings.flat().forEach((row) => identity.set(row.code, { code: row.code, name: row.name, market: row.market }));
  const regulars = [...identity.values()].map((stock): MaScoreRegular => ({
    ...stock,
    appearances: dailyRankings.filter((ranking) => ranking.some((row) => row.code === stock.code)).length,
    sampleDays: dates.length,
    recentFive: dailyRankings.slice(0, 5).map((ranking) => ranking.some((row) => row.code === stock.code)),
    score: latestScore.get(stock.code) ?? null,
    rank: latestRank.get(stock.code) ?? null,
  })).sort((a, b) => b.appearances - a.appearances || (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER) || a.code.localeCompare(b.code));
  return { dates, regulars };
}
