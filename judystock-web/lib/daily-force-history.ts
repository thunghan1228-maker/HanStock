type DailyRecord = { tradeDate: string; netVolume: number; barCount: number; sourceInterval: "1m" | "5m"; lastBarAt: string };
type MinuteRecord = { tradeDate: string; interval: "1m" | "5m"; barTime: string; netVolume: number;
  observed?: number; buyAmount?: number; sellAmount?: number; mainTickCount?: number };

/** Derive missing daily summaries from durable minute bars, never from price/volume estimates. */
export function dailyForceHistory(daily: DailyRecord[], minuteRows: MinuteRecord[]) {
  const groups = new Map<string, Map<string, MinuteRecord>>();
  for (const row of minuteRows) {
    if (!Number.isFinite(row.netVolume) || !(row.observed || row.netVolume || row.buyAmount || row.sellAmount || row.mainTickCount)) continue;
    const key = `${row.tradeDate}:${row.interval}`;
    const rows = groups.get(key) ?? new Map<string, MinuteRecord>();
    rows.set(row.barTime, row); groups.set(key, rows);
  }
  const recovered: DailyRecord[] = [...groups.values()].map(group => {
    const rows = [...group.values()].sort((a, b) => a.barTime.localeCompare(b.barTime));
    return { tradeDate: rows[0].tradeDate, sourceInterval: rows[0].interval,
      netVolume: rows.reduce((sum, row) => sum + row.netVolume, 0),
      barCount: rows.length, lastBarAt: rows.at(-1)!.barTime };
  });
  const byDate = new Map<string, DailyRecord>();
  const coverage = (row: DailyRecord) => Math.min(271, row.barCount * (row.sourceInterval === "5m" ? 5 : 1));
  for (const row of [...daily, ...recovered]) {
    const old = byDate.get(row.tradeDate);
    if (!old || coverage(row) > coverage(old)
      || coverage(row) === coverage(old) && (row.sourceInterval === "1m" || old.sourceInterval !== "1m")) {
      byDate.set(row.tradeDate, row);
    }
  }
  return [...byDate.values()].sort((a, b) => a.tradeDate.localeCompare(b.tradeDate)).map(row => ({
    date: row.tradeDate, net: row.netVolume / 1_000_000, barCount: row.barCount,
    sourceInterval: row.sourceInterval, lastBarAt: row.lastBarAt,
  }));
}
