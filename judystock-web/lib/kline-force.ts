export type ForceHistoryBar = {
  ts?: number | null;
  date: string;
  net: number;
  buyAmount?: number;
  sellAmount?: number;
  netAmount?: number;
  dayNet?: number;
  ultra?: boolean;
  mainForceAvailable?: boolean;
  amountsAvailable?: boolean;
};

/** Keep known history during sparse refreshes. Missing observations are not zero trades.
 * Self-contained so the embedded chart can use this exact merge, too.
 */
export function mergeForceHistory(saved: ForceHistoryBar[], incoming: ForceHistoryBar[]): ForceHistoryBar[] {
  const byTime = new Map<string, ForceHistoryBar>();
  for (const row of [...saved, ...incoming]) {
    if (!row || !Number.isFinite(row.net) || row.mainForceAvailable === false) continue;
    // Older responses had no availability flag and padded unknown bars with zero.
    if (row.mainForceAvailable !== true && row.net === 0 && !(row.buyAmount || row.sellAmount)) continue;
    const ts = row.ts && row.ts > 0 ? row.ts < 1e12 ? row.ts * 1000 : row.ts : null;
    const key = ts ? new Date(ts + 28800000).toISOString().slice(5, 16).replace('-', '/').replace('T', ' ') : String(row.date).replace(/^\d{4}[-/]/, '').replace('-', '/');
    const previous = byTime.get(key);
    const amountsAvailable = row.amountsAvailable !== false && Number.isFinite(row.buyAmount) && Number.isFinite(row.sellAmount);
    byTime.set(key, {
      ...previous, ...row, ts: ts ?? previous?.ts, date: key, mainForceAvailable: true,
      buyAmount: amountsAvailable ? row.buyAmount : previous?.buyAmount,
      sellAmount: amountsAvailable ? row.sellAmount : previous?.sellAmount,
      netAmount: amountsAvailable ? row.netAmount ?? (row.buyAmount! - row.sellAmount!) : previous?.netAmount,
      amountsAvailable: amountsAvailable || previous?.amountsAvailable === true,
    });
  }
  const rows = [...byTime.values()].sort((a, b) => a.ts && b.ts ? a.ts - b.ts : a.date.localeCompare(b.date));
  let day = '', total = 0;
  return rows.map(row => {
    if (day !== row.date.slice(0, 5)) { day = row.date.slice(0, 5); total = 0; }
    total += row.net;
    return { ...row, dayNet: total };
  });
}
