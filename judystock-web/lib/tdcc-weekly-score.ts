export type TdccWeeklyRow = { code: string; largeHolderPct: number; previousPct: number | null; weeklyChangePp: number | null };

/** Converts TDCC percentage-point changes into the same -100..100 relative scale used by weekly components. */
export function rankTdccWeeklyChanges(rows: TdccWeeklyRow[]) {
  const ranked = rows.filter((row) => row.weeklyChangePp !== null).sort((a, b) => (a.weeklyChangePp ?? 0) - (b.weeklyChangePp ?? 0));
  const result = new Map<string, number>();
  ranked.forEach((row, index) => result.set(row.code, ranked.length < 2 ? 0 : Math.round(((index / (ranked.length - 1)) * 200 - 100) * 10) / 10));
  return result;
}
