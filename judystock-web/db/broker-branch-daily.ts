export type BrokerBranchDailyRecord = { ticker: string; tradeDate: string; netAmount: number; netLots?: number | null; concentration: number; activeBranches: number };
type D1Result<T> = { results: T[] };
type D1Statement = { bind(...values: unknown[]): D1Statement; run(): Promise<unknown>; all<T>(): Promise<D1Result<T>> };
type D1 = { prepare(sql: string): D1Statement; batch(statements: D1Statement[]): Promise<unknown> };
let schemaReady: Promise<void> | null = null;

function database() { return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1 }).__HANSTOCK_DB ?? null; }

async function ensureSchema() {
  const d1 = database();
  if (!d1) throw new Error("broker_branch_daily_storage_unavailable");
  if (!schemaReady) {
    schemaReady = (async () => {
      await d1.batch([
        d1.prepare(`CREATE TABLE IF NOT EXISTS broker_branch_daily (
          trade_date TEXT NOT NULL,
          ticker TEXT NOT NULL,
          net_amount REAL NOT NULL,
          net_lots REAL,
          concentration REAL NOT NULL,
          active_branches INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (trade_date, ticker)
        )`),
        d1.prepare("CREATE INDEX IF NOT EXISTS broker_branch_daily_ticker_date_idx ON broker_branch_daily (ticker, trade_date DESC)"),
      ]);
      // 既有正式站資料表保留原資料，只新增可空白欄位。
      try { await d1.prepare("ALTER TABLE broker_branch_daily ADD COLUMN net_lots REAL").run(); } catch { /* 欄位已存在。 */ }
    })().catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveBrokerBranchDaily(records: BrokerBranchDailyRecord[]) {
  const d1 = await ensureSchema();
  const now = Date.now();
  for (let i = 0; i < records.length; i += 80) await d1.batch(records.slice(i, i + 80).map((r) => d1.prepare(`INSERT INTO broker_branch_daily
    (trade_date,ticker,net_amount,net_lots,concentration,active_branches,updated_at) VALUES (?,?,?,?,?,?,?)
    ON CONFLICT(trade_date,ticker) DO UPDATE SET net_amount=excluded.net_amount,net_lots=excluded.net_lots,concentration=excluded.concentration,active_branches=excluded.active_branches,updated_at=excluded.updated_at`)
    .bind(r.tradeDate, r.ticker, r.netAmount, typeof r.netLots === "number" && Number.isFinite(r.netLots) ? r.netLots : null, r.concentration, r.activeBranches, now)));
}

export async function readLatestBrokerBranchDaily() {
  const d1 = await ensureSchema();
  const result = await d1.prepare(`SELECT ticker, trade_date AS tradeDate,
    net_amount AS netAmount, net_lots AS netLots, concentration, active_branches AS activeBranches
    FROM broker_branch_daily
    WHERE trade_date = (SELECT MAX(trade_date) FROM broker_branch_daily)
    ORDER BY ticker ASC`).all<BrokerBranchDailyRecord>();
  return result.results;
}
