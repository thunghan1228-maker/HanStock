export type WeeklyMainForceHistoryRecord = {
  weekEndDate: string;
  ticker: string;
  institutionalScore: number;
  brokerBranchScore: number;
  tdccLargeHolderScore: number;
  compositeScore: number;
  label: string;
};

type D1Result<T> = { results?: T[] };
type D1Statement = {
  bind(...values: unknown[]): D1Statement;
  all<T>(): Promise<D1Result<T>>;
};
type HanstockD1 = {
  prepare(query: string): D1Statement;
  batch(statements: D1Statement[]): Promise<unknown>;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: HanstockD1 }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("weekly_main_force_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS weekly_main_force_history (
        week_end_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        institutional_score REAL NOT NULL,
        broker_branch_score REAL NOT NULL,
        tdcc_large_holder_score REAL NOT NULL,
        composite_score REAL NOT NULL,
        label TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (week_end_date, ticker)
      )`),
      d1.prepare("CREATE INDEX IF NOT EXISTS weekly_main_force_ticker_date_idx ON weekly_main_force_history (ticker, week_end_date DESC)"),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveWeeklyMainForceHistory(records: WeeklyMainForceHistoryRecord[]) {
  const d1 = await database();
  const updatedAt = Date.now();
  for (let start = 0; start < records.length; start += 80) {
    await d1.batch(records.slice(start, start + 80).map((record) => d1.prepare(`INSERT INTO weekly_main_force_history
      (week_end_date, ticker, institutional_score, broker_branch_score, tdcc_large_holder_score, composite_score, label, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(week_end_date, ticker) DO UPDATE SET
        institutional_score=excluded.institutional_score,
        broker_branch_score=excluded.broker_branch_score,
        tdcc_large_holder_score=excluded.tdcc_large_holder_score,
        composite_score=excluded.composite_score,
        label=excluded.label,
        updated_at=excluded.updated_at
      WHERE excluded.week_end_date >= (
        SELECT MAX(existing.week_end_date) FROM weekly_main_force_history AS existing
        WHERE existing.ticker=excluded.ticker
      )`)
      .bind(record.weekEndDate, record.ticker, record.institutionalScore, record.brokerBranchScore, record.tdccLargeHolderScore, record.compositeScore, record.label, updatedAt)));
  }
}

export async function readWeeklyMainForceTickerHistory(ticker: string, limit = 156) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT week_end_date AS weekEndDate, ticker,
    institutional_score AS institutionalScore, broker_branch_score AS brokerBranchScore,
    tdcc_large_holder_score AS tdccLargeHolderScore, composite_score AS compositeScore,
    label FROM weekly_main_force_history WHERE ticker = ? ORDER BY week_end_date DESC LIMIT ?`)
    .bind(ticker, Math.max(1, Math.min(520, limit)))
    .all<WeeklyMainForceHistoryRecord>();
  return result.results ?? [];
}

export async function readLatestWeeklyMainForceHistory(limit = 3_000) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT week_end_date AS weekEndDate, ticker,
    institutional_score AS institutionalScore, broker_branch_score AS brokerBranchScore,
    tdcc_large_holder_score AS tdccLargeHolderScore, composite_score AS compositeScore,
    label FROM weekly_main_force_history
    WHERE week_end_date = (SELECT MAX(week_end_date) FROM weekly_main_force_history)
    ORDER BY ticker LIMIT ?`)
    .bind(Math.max(1, Math.min(3_000, limit)))
    .all<WeeklyMainForceHistoryRecord>();
  return result.results ?? [];
}
