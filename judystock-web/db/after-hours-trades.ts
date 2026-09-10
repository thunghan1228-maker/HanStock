import { readEarlySellSignals, saveEarlySellSignals } from "./early-sell-history";
import { normalizeAfterHoursTrades, parseAfterHoursForceQuotes, type AfterHoursForceQuote } from "../lib/after-hours-trades";
import { timedKeyedSingleFlight } from "../lib/timed-single-flight";

const loadOfficialForceQuotes = timedKeyedSingleFlight(60_000, async (tradeDate: string) => {
  const sources = [
    ["twse", `https://www.twse.com.tw/exchangeReport/BFT41U?response=json&date=${tradeDate.replaceAll("-", "")}&selectType=ALLBUT0999`],
    ["tpex", "https://www.tpex.org.tw/openapi/v1/tpex_off_market"],
  ] as const;
  const result = new Map<string, AfterHoursForceQuote>();
  await Promise.all(sources.map(async ([market, url]) => {
    try {
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(5_000), headers: { Accept: "application/json" } });
      if (!response.ok) return;
      for (const [ticker, quote] of parseAfterHoursForceQuotes(await response.json(), market, tradeDate)) result.set(ticker, quote);
    } catch { /* Official enrichment is optional; a missing source is not zero force. */ }
  }));
  return result;
}, 2);

// This collector is independent of the 09:00–13:35 intraday scan. Never require
// a nonexistent 14:30 one-minute force bar to retain a real after-hours trade.
export const refreshAfterHoursTrades = timedKeyedSingleFlight(15_000, async (tradeDate: string) => {
  const cutoff = Date.parse(`${tradeDate}T14:30:00+08:00`);
  if (!Number.isFinite(cutoff) || Date.now() < cutoff) return { status: "pending" as const, imported: 0 };
  const today = new Date(Date.now() + 8 * 60 * 60_000).toISOString().slice(0, 10);
  const existing = await readEarlySellSignals({ tradeDate, kinds: ["instantLargeBuy", "instantLargeSell"], minuteOfDay: 870, limit: 10_000 });
  const forceQuotes = tradeDate === today ? await loadOfficialForceQuotes(tradeDate) : undefined;
  // Enrich saved executions as well as new imports, even if the hub is unavailable.
  const enriched = normalizeAfterHoursTrades(existing, tradeDate, forceQuotes);
  const existingByKey = new Map(existing.map(row => [`${row.ticker}:${row.kind}:${row.barTs}`, row]));
  const updates = enriched.filter(row => existingByKey.get(`${row.ticker}:${row.kind}:${row.barTs}`)?.note !== row.note);
  await saveEarlySellSignals(updates);
  let sourceError: unknown;
  for (const base of ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"]) {
    try {
      const path = tradeDate === today ? "/api/hub/intraday-large-orders?limit=5000"
        : `/api/hub/intraday-signals/latest?trade_date=${encodeURIComponent(tradeDate)}&limit=200&market_only=false`;
      const response = await fetch(new URL(path, base), { cache: "no-store", signal: AbortSignal.timeout(5_000),
        headers: { Accept: "application/json", "User-Agent": "HanStock-After-Hours/1.0" } });
      if (!response.ok) throw new Error(`after-hours-source-${response.status}`);
      const payload = await response.json() as { tradeDate?: string; signals?: unknown };
      if (payload.tradeDate !== tradeDate || !Array.isArray(payload.signals)) throw new Error("after-hours-source-date-mismatch");
      const incoming = normalizeAfterHoursTrades(payload.signals, tradeDate, forceQuotes);
      // An empty or still-delayed source cannot delete the previous saved list.
      if (!incoming.length) continue;
      const additions = incoming.filter(row => !existingByKey.has(`${row.ticker}:${row.kind}:${row.barTs}`));
      await saveEarlySellSignals(additions);
      return { status: "ready" as const, imported: additions.length };
    } catch (error) { sourceError = error; }
  }
  if (sourceError) throw sourceError;
  return { status: "pending" as const, imported: 0 };
}, 8);
