export type BrokerBranchWeeklyRow = {
  ticker: string;
  weekEndDate: string;
  netAmount: number;
  concentration: number;
  activeBranches: number;
};

function percentileScores(entries: Array<{ ticker: string; value: number }>) {
  const sorted = [...entries].sort((a, b) => a.value - b.value || a.ticker.localeCompare(b.ticker));
  const scores = new Map<string, number>();
  for (let start = 0; start < sorted.length;) {
    let end = start + 1;
    while (end < sorted.length && sorted[end].value === sorted[start].value) end += 1;
    const averageIndex = (start + end - 1) / 2;
    const score = sorted.length < 2 ? 0 : Math.round(((averageIndex / (sorted.length - 1)) * 200 - 100) * 10) / 10;
    for (let index = start; index < end; index += 1) scores.set(sorted[index].ticker, score);
    start = end;
  }
  return scores;
}

/**
 * 分點週分數：週淨買賣 65%＋方向化集中度 35%。
 * 集中度必須跟著週淨額方向，避免「集中賣超」被誤判成正分。
 */
export function rankBrokerBranchWeekly(rows: BrokerBranchWeeklyRow[]) {
  const valid = rows.filter((row) => Number.isFinite(row.netAmount) && Number.isFinite(row.concentration));
  const directional = valid.filter((row) => row.netAmount !== 0);
  const netScores = percentileScores(directional.map((row) => ({ ticker: row.ticker, value: row.netAmount })));
  const concentrationScores = percentileScores(directional.map((row) => ({
    ticker: row.ticker,
    value: Math.sign(row.netAmount) * Math.abs(row.concentration),
  })));
  return new Map(valid.map((row) => {
    if (row.netAmount === 0) return [row.ticker, 0] as const;
    const score = (netScores.get(row.ticker) ?? 0) * 0.65 + (concentrationScores.get(row.ticker) ?? 0) * 0.35;
    return [row.ticker, Math.round(score * 10) / 10] as const;
  }));
}
