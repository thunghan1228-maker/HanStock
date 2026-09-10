import { calculateIntradayLargeForceValue, type IntradayLargeForceValue, type IntradayLargeForceMinuteBar } from "./intraday-large-force.ts";
import { readIntradayLargeForceMonitorRows } from "../db/intraday-large-force-scan.ts";
import { readRankingForceClose } from "../db/ranking-force-close.ts";
import { backfillRankingForceClose } from "./ranking-force-backfill.ts";

export type RankingForce = {
  forcePct: number | null;
  tradeDate: string;
  sampledAt: number;
  barTs: number | null;
  availableCount: number;
  totalCount: number;
};
type Value = Pick<IntradayLargeForceValue, "tradeDate" | "barTs" | "forcePct">;
export type RankingForceClock = { tradeDate: string; sampledAt: number; cutoff: number; preopen: boolean };

export function rankingForceClock(sourceDate: string, sampledAt: number, preopen = false): RankingForceClock {
  const match = sourceDate.match(/^(\d{4})[-/]?(\d{2})[-/]?(\d{2})/);
  const tradeDate = match ? `${match[1]}-${match[2]}-${match[3]}` : new Date(sampledAt + 28800000).toISOString().slice(0, 10);
  const close = Date.parse(`${tradeDate}T13:30:00+08:00`);
  return { tradeDate, sampledAt, cutoff: Math.min(sampledAt, close), preopen };
}

export function isRankingForceCurrent(value: Value | undefined, clock: RankingForceClock) {
  return !clock.preopen && !!value && value.tradeDate === clock.tradeDate
    && Number.isFinite(value.forcePct) && Number.isFinite(value.barTs)
    && value.barTs <= clock.cutoff && value.barTs >= clock.cutoff - 120_000;
}

/** Equal-weight mean of every configured member; never replace missing members by zero. */
export function summarizeRankingForce(tickers: string[], values: Map<string, Value>, clock: RankingForceClock): RankingForce {
  const members = [...new Set(tickers)];
  const available = members.flatMap(ticker => {
    const value = values.get(ticker);
    return isRankingForceCurrent(value, clock) ? [value!] : [];
  });
  return {
    forcePct: members.length > 0 && available.length === members.length ? available.reduce((sum, row) => sum + row.forcePct, 0) / members.length : null,
    tradeDate: clock.tradeDate, sampledAt: clock.sampledAt,
    barTs: available.length ? Math.min(...available.map(row => row.barTs)) : null,
    availableCount: available.length, totalCount: members.length,
  };
}

export async function loadRankingForceValues(tickers: string[], clock: RankingForceClock) {
  const values = new Map<string, Value>();
  if (clock.preopen) return values;
  const unique = [...new Set(tickers)];
  const stored = await readIntradayLargeForceMonitorRows(clock.tradeDate).catch(() => ({ rows: [] }));
  for (const row of stored.rows) if (unique.includes(row.ticker) && isRankingForceCurrent(row, clock)) values.set(row.ticker, row);
  // Once the closing auction has settled, recover the complete trade-day totals.
  // Live Hub batches contain only in-memory subscriptions, not historical ticks.
  const close = Date.parse(`${clock.tradeDate}T13:30:00+08:00`);
  if (clock.sampledAt >= close + 300_000) {
    const closed = await readRankingForceClose(clock.tradeDate).catch(() => []);
    const completed = new Set<string>();
    for (const row of closed) if (unique.includes(row.ticker) && isRankingForceCurrent(row, clock)) {
      values.set(row.ticker, row);
      completed.add(row.ticker);
    }
    // Every refresh advances a bounded backfill, persisted across deployments.
    // Never accept an opening-only snapshot as the completed trade-day value.
    const missingClose = unique.filter(ticker => !completed.has(ticker)).slice(0, 25);
    if (missingClose.length) {
      const recovered = await backfillRankingForceClose(missingClose, clock.tradeDate).catch(() => []);
      for (const row of recovered) if (isRankingForceCurrent(row, clock)) values.set(row.ticker, row);
    }
    return values;
  }
  // Fresh monitor rows already contain the cumulative minute calculation. Fill
  // remaining members in bounded batches instead of starting hundreds of subscriptions.
  const missing = unique.filter(ticker => !values.has(ticker));
  const batches = Array.from({ length: Math.ceil(missing.length / 200) }, (_, i) => missing.slice(i * 200, i * 200 + 200));
  await Promise.all(batches.map(async batch => {
    for (const base of ["https://hanstock-production.up.railway.app"]) {
      const remaining = batch.filter(ticker => !values.has(ticker));
      if (!remaining.length) break;
      try {
        const response = await fetch(`${base}/api/hub/bars1m/batch`, {
          method: "POST", cache: "no-store", headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ codes: remaining }), signal: AbortSignal.timeout(4_000),
        });
        if (!response.ok) continue;
        const payload = await response.json() as { data?: Record<string, IntradayLargeForceMinuteBar[]> };
        for (const ticker of remaining) {
          const bars = payload.data?.[ticker];
          if (!Array.isArray(bars)) continue;
          const value = calculateIntradayLargeForceValue(bars, clock.tradeDate, clock.cutoff);
          if (value && isRankingForceCurrent(value, clock)) values.set(ticker, value);
        }
      } catch { /* The ranking remains usable and reports the exact missing coverage. */ }
    }
  }));
  return values;
}
