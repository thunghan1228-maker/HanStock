import type {
  MonthlyRevenueRecord,
  RevenueSignalDraft,
  StoredRevenueObservation,
} from "../lib/monthly-revenue-records";

type D1Result<T> = { results?: T[] };
type D1Statement = {
  bind(...values: unknown[]): D1Statement;
  run(): Promise<unknown>;
  first<T>(): Promise<T | null>;
  all<T>(): Promise<D1Result<T>>;
};
type HanstockD1 = {
  prepare(query: string): D1Statement;
  batch(statements: D1Statement[]): Promise<unknown>;
};

export type MonthlyRevenueSignalRow = {
  id: number;
  revenueMonth: string;
  stockCode: string;
  name: string;
  market: string;
  signalKind: string;
  revenue: number;
  previousMonthRevenue: number | null;
  previousYearRevenue: number | null;
  momPct: number | null;
  yoyPct: number | null;
  comparisonRevenue: number;
  comparisonMonth: string;
  historyMonths: number;
  sourcePublishedDate: string;
  firstObservedAt: number;
  updatedAt: number;
};

export type MonthlyRevenueSyncRow = {
  revenueMonth: string;
  sourcePublishedDate: string;
  coverageTotal: number;
  coverageTwse: number;
  coverageTpex: number;
  sourcesJson: string;
  checkedAt: number;
  completedAt: number;
};

export type MonthlyRevenueSnapshotRow = {
  revenueMonth: string;
  stockCode: string;
  name: string;
  market: string;
  revenue: number;
  previousMonthRevenue: number | null;
  previousYearRevenue: number | null;
  momPct: number | null;
  yoyPct: number | null;
  sourcePublishedDate: string;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: HanstockD1 }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("monthly_revenue_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS monthly_revenue_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        revenue_month TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        name TEXT NOT NULL,
        market TEXT NOT NULL,
        revenue INTEGER NOT NULL,
        previous_month_revenue INTEGER,
        previous_year_revenue INTEGER,
        mom_pct REAL,
        yoy_pct REAL,
        source_published_date TEXT NOT NULL,
        first_observed_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS monthly_revenue_month_code_idx ON monthly_revenue_snapshots(revenue_month, stock_code)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS monthly_revenue_code_month_idx ON monthly_revenue_snapshots(stock_code, revenue_month)"),
      d1.prepare(`CREATE TABLE IF NOT EXISTS monthly_revenue_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        revenue_month TEXT NOT NULL,
        stock_code TEXT NOT NULL,
        name TEXT NOT NULL,
        market TEXT NOT NULL,
        signal_kind TEXT NOT NULL,
        revenue INTEGER NOT NULL,
        previous_month_revenue INTEGER,
        previous_year_revenue INTEGER,
        mom_pct REAL,
        yoy_pct REAL,
        comparison_revenue INTEGER NOT NULL,
        comparison_month TEXT NOT NULL,
        history_months INTEGER NOT NULL,
        source_published_date TEXT NOT NULL,
        first_observed_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS monthly_revenue_signal_unique_idx ON monthly_revenue_signals(revenue_month, stock_code, signal_kind)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS monthly_revenue_signal_observed_idx ON monthly_revenue_signals(first_observed_at)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS monthly_revenue_signal_month_kind_idx ON monthly_revenue_signals(revenue_month, signal_kind)"),
      d1.prepare(`CREATE TABLE IF NOT EXISTS monthly_revenue_sync_state (
        revenue_month TEXT PRIMARY KEY,
        source_published_date TEXT NOT NULL,
        coverage_total INTEGER NOT NULL,
        coverage_twse INTEGER NOT NULL,
        coverage_tpex INTEGER NOT NULL,
        sources_json TEXT NOT NULL,
        checked_at INTEGER NOT NULL,
        completed_at INTEGER NOT NULL
      )`),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function readMonthlyRevenueObservations(afterMonth: string): Promise<StoredRevenueObservation[]> {
  const d1 = await database();
  const result = await d1.prepare(`SELECT revenue_month AS revenueMonth, stock_code AS stockCode, revenue
    FROM monthly_revenue_snapshots WHERE revenue_month > ? ORDER BY revenue_month ASC`)
    .bind(afterMonth)
    .all<StoredRevenueObservation>();
  return result.results ?? [];
}

export async function readMonthlyRevenueSnapshots(revenueMonth: string, limit = 3_000) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT revenue_month AS revenueMonth, stock_code AS stockCode, name, market,
      revenue, previous_month_revenue AS previousMonthRevenue, previous_year_revenue AS previousYearRevenue,
      mom_pct AS momPct, yoy_pct AS yoyPct, source_published_date AS sourcePublishedDate
    FROM monthly_revenue_snapshots WHERE revenue_month=?
    ORDER BY yoy_pct DESC, stock_code ASC LIMIT ?`)
    .bind(revenueMonth, limit)
    .all<MonthlyRevenueSnapshotRow>();
  return result.results ?? [];
}

export async function saveMonthlyRevenueRefresh(input: {
  records: MonthlyRevenueRecord[];
  signals: RevenueSignalDraft[];
  revenueMonth: string;
  sourcePublishedDate: string;
  coverage: { total: number; twse: number; tpex: number };
  sources: string[];
  checkedAt: number;
}) {
  const d1 = await database();
  const now = input.checkedAt;
  for (let start = 0; start < input.records.length; start += 40) {
    const chunk = input.records.slice(start, start + 40);
    await d1.batch(chunk.flatMap((record) => [
      d1.prepare(`INSERT INTO monthly_revenue_snapshots
        (revenue_month, stock_code, name, market, revenue, previous_month_revenue, previous_year_revenue, mom_pct, yoy_pct, source_published_date, first_observed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(revenue_month, stock_code) DO UPDATE SET
          name=excluded.name, market=excluded.market, revenue=excluded.revenue,
          previous_month_revenue=excluded.previous_month_revenue, previous_year_revenue=excluded.previous_year_revenue,
          mom_pct=excluded.mom_pct, yoy_pct=excluded.yoy_pct, updated_at=excluded.updated_at`)
        .bind(record.revenueMonth, record.stockCode, record.name, record.market, record.revenue,
          record.previousMonthRevenue, record.previousYearRevenue, record.momPct, record.yoyPct,
          record.sourcePublishedDate, now, now),
      d1.prepare("UPDATE monthly_revenue_signals SET active=0, updated_at=? WHERE revenue_month=? AND stock_code=?")
        .bind(now, record.revenueMonth, record.stockCode),
    ]));
  }

  for (let start = 0; start < input.signals.length; start += 40) {
    await d1.batch(input.signals.slice(start, start + 40).map((signal) => d1.prepare(`INSERT INTO monthly_revenue_signals
      (revenue_month, stock_code, name, market, signal_kind, revenue, previous_month_revenue, previous_year_revenue, mom_pct, yoy_pct,
        comparison_revenue, comparison_month, history_months, source_published_date, first_observed_at, updated_at, active)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
      ON CONFLICT(revenue_month, stock_code, signal_kind) DO UPDATE SET
        name=excluded.name, market=excluded.market, revenue=excluded.revenue,
        previous_month_revenue=excluded.previous_month_revenue, previous_year_revenue=excluded.previous_year_revenue,
        mom_pct=excluded.mom_pct, yoy_pct=excluded.yoy_pct,
        comparison_revenue=excluded.comparison_revenue, comparison_month=excluded.comparison_month,
        history_months=excluded.history_months, updated_at=excluded.updated_at, active=1`)
      .bind(signal.revenueMonth, signal.stockCode, signal.name, signal.market, signal.signalKind, signal.revenue,
        signal.previousMonthRevenue, signal.previousYearRevenue, signal.momPct, signal.yoyPct,
        signal.comparisonRevenue, signal.comparisonMonth, signal.historyMonths, signal.sourcePublishedDate, now, now)));
  }

  await d1.prepare(`INSERT INTO monthly_revenue_sync_state
    (revenue_month, source_published_date, coverage_total, coverage_twse, coverage_tpex, sources_json, checked_at, completed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(revenue_month) DO UPDATE SET
      source_published_date=excluded.source_published_date, coverage_total=excluded.coverage_total,
      coverage_twse=excluded.coverage_twse, coverage_tpex=excluded.coverage_tpex,
      sources_json=excluded.sources_json, checked_at=excluded.checked_at, completed_at=excluded.completed_at`)
    .bind(input.revenueMonth, input.sourcePublishedDate, input.coverage.total, input.coverage.twse, input.coverage.tpex,
      JSON.stringify(input.sources), now, now)
    .run();
}

export async function readMonthlyRevenueSignals(historyStart: string, limit = 5_000) {
  const d1 = await database();
  const startAt = Date.parse(`${historyStart}T00:00:00+08:00`);
  const result = await d1.prepare(`SELECT
      id, revenue_month AS revenueMonth, stock_code AS stockCode, name, market, signal_kind AS signalKind,
      revenue, previous_month_revenue AS previousMonthRevenue, previous_year_revenue AS previousYearRevenue,
      mom_pct AS momPct, yoy_pct AS yoyPct, comparison_revenue AS comparisonRevenue,
      comparison_month AS comparisonMonth, history_months AS historyMonths,
      source_published_date AS sourcePublishedDate, first_observed_at AS firstObservedAt, updated_at AS updatedAt
    FROM monthly_revenue_signals
    WHERE active=1 AND first_observed_at >= ?
    ORDER BY first_observed_at DESC, revenue_month DESC, stock_code ASC, signal_kind ASC
    LIMIT ?`)
    .bind(startAt, limit)
    .all<MonthlyRevenueSignalRow>();
  return result.results ?? [];
}

export async function readMonthlyRevenueSyncState(revenueMonth?: string) {
  const d1 = await database();
  const statement = revenueMonth
    ? d1.prepare(`SELECT revenue_month AS revenueMonth, source_published_date AS sourcePublishedDate,
        coverage_total AS coverageTotal, coverage_twse AS coverageTwse, coverage_tpex AS coverageTpex,
        sources_json AS sourcesJson, checked_at AS checkedAt, completed_at AS completedAt
      FROM monthly_revenue_sync_state WHERE revenue_month=? LIMIT 1`).bind(revenueMonth)
    : d1.prepare(`SELECT revenue_month AS revenueMonth, source_published_date AS sourcePublishedDate,
        coverage_total AS coverageTotal, coverage_twse AS coverageTwse, coverage_tpex AS coverageTpex,
        sources_json AS sourcesJson, checked_at AS checkedAt, completed_at AS completedAt
      FROM monthly_revenue_sync_state ORDER BY revenue_month DESC LIMIT 1`);
  return statement.first<MonthlyRevenueSyncRow>();
}
