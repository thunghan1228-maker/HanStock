export type BrokerBranchWeeklyRecord = { ticker: string; weekEndDate: string; netAmount: number; concentration: number; activeBranches: number };
type D1Result<T> = { results: T[] };
type D1Statement = { bind(...values: unknown[]): D1Statement; run(): Promise<unknown>; all<T>(): Promise<D1Result<T>> };
type D1 = { prepare(sql: string): D1Statement; batch(statements: D1Statement[]): Promise<unknown> };
let schemaReady: Promise<void> | null = null;

function database() { return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1 }).__HANSTOCK_DB ?? null; }

async function ensureSchema() {
  const d1 = database();
  if (!d1) throw new Error("broker_branch_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS broker_branch_weekly (
        week_end_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        net_amount REAL NOT NULL,
        concentration REAL NOT NULL,
        active_branches INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (week_end_date, ticker)
      )`),
      d1.prepare("CREATE INDEX IF NOT EXISTS broker_branch_weekly_ticker_date_idx ON broker_branch_weekly (ticker, week_end_date DESC)"),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveBrokerBranchWeekly(records: BrokerBranchWeeklyRecord[]) {
  const d1 = await ensureSchema();
  const now = Date.now();
  for (let i = 0; i < records.length; i += 80) await d1.batch(records.slice(i, i + 80).map((r) => d1.prepare(`INSERT INTO broker_branch_weekly
    (week_end_date,ticker,net_amount,concentration,active_branches,updated_at) VALUES (?,?,?,?,?,?)
    ON CONFLICT(week_end_date,ticker) DO UPDATE SET net_amount=excluded.net_amount,concentration=excluded.concentration,active_branches=excluded.active_branches,updated_at=excluded.updated_at`)
    .bind(r.weekEndDate, r.ticker, r.netAmount, r.concentration, r.activeBranches, now)));
}

export async function readLatestBrokerBranchWeekly() {
  const d1 = await ensureSchema();
  const result = await d1.prepare(`SELECT ticker, week_end_date AS weekEndDate,
    net_amount AS netAmount, concentration, active_branches AS activeBranches
    FROM broker_branch_weekly
    WHERE week_end_date = (SELECT MAX(week_end_date) FROM broker_branch_weekly)
    ORDER BY ticker ASC`).all<BrokerBranchWeeklyRecord>();
  return result.results;
}
