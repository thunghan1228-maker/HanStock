export type IntradayForceHomeworkRow = {
  ticker: string;
  name: string;
  group: string;
  tradeDate: string;
  forcePct: number;
  barTs: number;
  price?: number | null;
  change?: number | null;
  changePct?: number | null;
  buyAmount?: number | null;
  sellAmount?: number | null;
  netAmount?: number | null;
  turnoverAmount?: number | null;
};

export function rankIntradayForceHomework(rows: unknown, limit = 20) {
  const byTicker = new Map<string, IntradayForceHomeworkRow>();
  for (const value of Array.isArray(rows) ? rows : []) {
    if (!value || typeof value !== "object") continue;
    const row = value as Partial<IntradayForceHomeworkRow>;
    const ticker = String(row.ticker ?? "").trim().toUpperCase();
    const group = String(row.group ?? "").trim();
    if (!/^[1-9]\d{3}[A-Z]?$/.test(ticker) || !group || group === "未分類") continue;
    if (typeof row.forcePct !== "number" || !Number.isFinite(row.forcePct)) continue;
    if (typeof row.barTs !== "number" || !Number.isFinite(row.barTs) || row.barTs <= 0) continue;
    const normalized: IntradayForceHomeworkRow = {
      ticker,
      name: String(row.name ?? ticker).trim() || ticker,
      group,
      tradeDate: String(row.tradeDate ?? ""),
      forcePct: row.forcePct,
      barTs: row.barTs,
      price: typeof row.price === "number" && Number.isFinite(row.price) ? row.price : null,
      change: typeof row.change === "number" && Number.isFinite(row.change) ? row.change : null,
      changePct: typeof row.changePct === "number" && Number.isFinite(row.changePct) ? row.changePct : null,
      buyAmount: typeof row.buyAmount === "number" && Number.isFinite(row.buyAmount) ? row.buyAmount : null,
      sellAmount: typeof row.sellAmount === "number" && Number.isFinite(row.sellAmount) ? row.sellAmount : null,
      netAmount: typeof row.netAmount === "number" && Number.isFinite(row.netAmount) ? row.netAmount : null,
      turnoverAmount: typeof row.turnoverAmount === "number" && Number.isFinite(row.turnoverAmount) ? row.turnoverAmount : null,
    };
    const previous = byTicker.get(ticker);
    if (!previous || normalized.barTs >= previous.barTs) byTicker.set(ticker, normalized);
  }
  const available = [...byTicker.values()];
  const size = Math.max(1, Math.min(100, Math.trunc(limit) || 20));
  return {
    bullish: [...available].sort((a, b) => b.forcePct - a.forcePct || b.barTs - a.barTs || a.ticker.localeCompare(b.ticker)).slice(0, size),
    bearish: [...available].sort((a, b) => a.forcePct - b.forcePct || b.barTs - a.barTs || a.ticker.localeCompare(b.ticker)).slice(0, size),
    availableCount: available.length,
  };
}

export function homeworkPriceChange(price: unknown, previousClose: unknown) {
  if (typeof price !== "number" || !Number.isFinite(price) || price <= 0
    || typeof previousClose !== "number" || !Number.isFinite(previousClose) || previousClose <= 0) {
    return { change: null, changePct: null };
  }
  const change = price - previousClose;
  return { change, changePct: change / previousClose * 100 };
}
