export type FullMarketLargeFlowBaseline = {
  ticker: string;
  name: string;
  tradeDate: string;
  netLargeAmount: number;
  turnoverAmount: number;
  closePrice: number;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("full_market_large_flow_storage_unavailable");
  if (!schemaReady) schemaReady = (async () => {
    await d1.batch([
      d1.prepare("CREATE TABLE IF NOT EXISTS full_market_large_flow_daily_v1 (ticker TEXT NOT NULL, name TEXT NOT NULL, trade_date TEXT NOT NULL, net_large_amount REAL NOT NULL, turnover_amount REAL NOT NULL, close_price REAL NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (ticker, trade_date))"),
      d1.prepare("CREATE INDEX IF NOT EXISTS full_market_large_flow_date_idx ON full_market_large_flow_daily_v1 (trade_date DESC, ticker)"),
    ]);
  })().catch((error) => { schemaReady = null; throw error; });
  await schemaReady;
  return d1;
}

export async function saveFullMarketLargeFlowBaselines(rows: FullMarketLargeFlowBaseline[]) {
  if (!rows.length) return;
  const d1 = await database();
  const updatedAt = Date.now();
  for (let start = 0; start < rows.length; start += 60) {
    await d1.batch(rows.slice(start, start + 60).map((row) => d1.prepare(
      "INSERT INTO full_market_large_flow_daily_v1 (ticker,name,trade_date,net_large_amount,turnover_amount,close_price,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticker, trade_date) DO UPDATE SET name=excluded.name, net_large_amount=excluded.net_large_amount, turnover_amount=excluded.turnover_amount, close_price=excluded.close_price, updated_at=excluded.updated_at",
    ).bind(row.ticker, row.name, row.tradeDate, row.netLargeAmount, row.turnoverAmount, row.closePrice, updatedAt)));
  }
}

export async function readFullMarketLargeFlowBaselines(tradeDate: string) {
  const d1 = await database();
  const result = await d1.prepare(
    "SELECT ticker,name,trade_date AS tradeDate,net_large_amount AS netLargeAmount,turnover_amount AS turnoverAmount,close_price AS closePrice FROM full_market_large_flow_daily_v1 WHERE trade_date=? ORDER BY ticker ASC",
  ).bind(tradeDate).all<FullMarketLargeFlowBaseline>();
  return result.results;
}
