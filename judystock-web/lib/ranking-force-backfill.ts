import { parseRankingForceClose, type RankingForceClose } from "./ranking-force-close.ts";
import { readRankingForceClose, saveRankingForceClose } from "../db/ranking-force-close.ts";
import recoveredSeptember4 from "../data/ranking-force-close/2026-09-04.json" with { type: "json" };

const pending = new Map<string, Promise<RankingForceClose[]>>();
/** Read verified day totals without waiting for live subscriptions or a remote backfill. */
export async function readAvailableRankingForceClose(tickers: string[], tradeDate: string, persistArchive = false) {
  const codes = [...new Set(tickers)].sort();
  const stored = await readRankingForceClose(tradeDate).catch(() => []);
  const byTicker = new Map(stored.map(row => [row.ticker, row]));
  // Real recovered trade-day records, independently checked against the Hub.
  // Keep the archive as a recovery source; active sessions never read closing totals.
  if (recoveredSeptember4.tradeDate === tradeDate) {
    const archived = recoveredSeptember4.rows.flatMap(row => {
      const value = parseRankingForceClose(row, tradeDate);
      return value && codes.includes(value.ticker) && !byTicker.has(value.ticker) ? [value] : [];
    });
    if (archived.length) {
      if (persistArchive) await saveRankingForceClose(archived);
      for (const row of archived) byTicker.set(row.ticker, row);
    }
  }
  return codes.flatMap(ticker => byTicker.get(ticker) ?? []);
}

export async function backfillRankingForceClose(tickers: string[], tradeDate: string, options: { timeoutMs?: number } = {}) {
  const codes = [...new Set(tickers)].sort();
  const available = await readAvailableRankingForceClose(codes, tradeDate, true);
  const byTicker = new Map(available.map(row => [row.ticker, row]));
  const missing = codes.filter(ticker => !byTicker.has(ticker));
  if (!missing.length) return codes.flatMap(ticker => byTicker.get(ticker) ?? []);
  const timeoutMs = options.timeoutMs ?? 40_000;
  const key = `${tradeDate}:${missing.join(",")}:${timeoutMs}`;
  let task = pending.get(key);
  if (!task) {
    task = (async () => {
      const recovered = new Map<string, RankingForceClose>();
      for (const base of ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"]) {
        const remaining = missing.filter(ticker => !recovered.has(ticker));
        if (!remaining.length) break;
        try {
          const query = new URLSearchParams({ date: tradeDate, codes: remaining.join(","), scan_limit: String(remaining.length), limit: String(remaining.length), include_all: "true" });
          const response = await fetch(`${base}/api/hub/daytrade-flow-ranking?${query}`, { cache: "no-store", headers: { Accept: "application/json" }, signal: AbortSignal.timeout(timeoutMs) });
          if (!response.ok) continue;
          const payload = await response.json() as { rows?: Record<string, unknown>[] };
          const rows = (payload.rows ?? []).flatMap(row => { const value = parseRankingForceClose(row, tradeDate); return value && remaining.includes(value.ticker) ? [value] : []; });
          if (rows.length) {
            await saveRankingForceClose(rows);
            for (const row of rows) recovered.set(row.ticker, row);
          }
        } catch { /* Retry the existing historical source; never store missing rows as zero. */ }
      }
      return [...recovered.values()];
    })().finally(() => pending.delete(key));
    pending.set(key, task);
  }
  for (const row of await task) byTicker.set(row.ticker, row);
  return codes.flatMap(ticker => byTicker.get(ticker) ?? []);
}
