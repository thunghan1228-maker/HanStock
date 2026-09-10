import type { WatchlistStock } from "./watchlists";
import { extractMainForceGroupRank } from "./main-force-group-ranks";

type TradeSignal = { ticker: string; name: string; kind: string; tradeDate: string; barTs: number; price: number; note: string };

export function afterHoursWatchlistStocks(signals: TradeSignal[], tradeDate: string): WatchlistStock[] {
  const byTicker = new Map<string, WatchlistStock>();
  for (const row of [...signals].sort((a, b) => b.barTs - a.barTs || a.ticker.localeCompare(b.ticker))) {
    if (row.tradeDate !== tradeDate || !["instantLargeBuy", "instantLargeSell"].includes(row.kind) ||
        !Number.isFinite(row.barTs) || !/^[0-9A-Z]{2,12}$/.test(row.ticker)) continue;
    const clock = new Date(row.barTs + 8 * 60 * 60_000);
    if (clock.toISOString().slice(0, 10) !== tradeDate || clock.getUTCHours() !== 14 || clock.getUTCMinutes() !== 30) continue;
    if (byTicker.has(row.ticker)) continue;
    const forceMatch = row.note.match(/觸發當時盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u);
    const groupRank = extractMainForceGroupRank(row.note);
    byTicker.set(row.ticker, {ticker: row.ticker, name: row.name || row.ticker,
      group: groupRank?.group ?? "—", price: Number.isFinite(row.price) ? String(row.price) : "—", change: "—",
      forcePct: forceMatch ? Number(forceMatch[1]) : null, signalTradeDate: tradeDate, groupRank});
  }
  return [...byTicker.values()];
}
