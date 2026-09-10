export type DailyPriceVolume = {
  closePrice: number | null;
  volumeLots: number | null;
  priceDate: string;
};

type Market = "twse" | "tpex";
type Snapshot = Map<string, DailyPriceVolume>;
const sources: Record<Market, string> = {
  twse: "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
  tpex: "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
};
const cache = new Map<Market, { rows: Snapshot; retryAt: number }>();
const inFlight = new Map<Market, Promise<Snapshot>>();

function numberOrNull(value: unknown) {
  const text = String(value ?? "").replace(/,/g, "").trim();
  if (!text || !/^\d+(?:\.\d+)?$/.test(text)) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

export function parseDailyPriceVolume(payload: unknown, market: Market): Snapshot {
  const result: Snapshot = new Map();
  if (!Array.isArray(payload)) return result;
  for (const row of payload) {
    if (!row || typeof row !== "object") continue;
    const code = String(market === "twse" ? row.Code ?? "" : row.SecuritiesCompanyCode ?? "").trim();
    if (!/^\d{4,6}[A-Z]?$/.test(code)) continue;
    const rawDate = String(row.Date ?? "").replace(/[/\-]/g, "");
    if (!/^\d{7,8}$/.test(rawDate)) continue;
    const year = Number(rawDate.slice(0, -4)) + (rawDate.length === 7 ? 1911 : 0);
    const priceDate = `${year}-${rawDate.slice(-4, -2)}-${rawDate.slice(-2)}`;
    const date = new Date(priceDate);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== priceDate) continue;
    const price = numberOrNull(market === "twse" ? row.ClosingPrice : row.Close);
    const shares = numberOrNull(market === "twse" ? row.TradeVolume : row.TradingShares);
    const value = { closePrice: price !== null && price > 0 ? price : null, volumeLots: shares === null ? null : shares / 1_000, priceDate };
    if (!result.has(code) || result.get(code)!.priceDate < priceDate) result.set(code, value);
  }
  return result;
}

async function loadMarket(market: Market): Promise<Snapshot> {
  const previous = cache.get(market);
  if (previous && Date.now() < previous.retryAt) return previous.rows;
  const pending = inFlight.get(market);
  if (pending) return pending;
  const task = (async () => {
    let rows = previous?.rows ?? new Map<string, DailyPriceVolume>();
    let complete = false;
    try {
      const response = await fetch(sources[market], {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const incoming = parseDailyPriceVolume(await response.json(), market);
      complete = incoming.size >= 100;
      // A failed/partial market never erases its last successful snapshot.
      if (complete || !rows.size) rows = incoming;
    } catch (error) {
      console.warn(`[monthly-revenue] ${market} daily snapshot unavailable`, String(error));
    }
    cache.set(market, { rows, retryAt: Date.now() + (complete ? 15 * 60_000 : 60_000) });
    return rows;
  })().finally(() => inFlight.delete(market));
  inFlight.set(market, task);
  return task;
}

export async function loadDailyPriceVolumeSnapshot() {
  const [twse, tpex] = await Promise.all([loadMarket("twse"), loadMarket("tpex")]);
  return { twse, tpex };
}
