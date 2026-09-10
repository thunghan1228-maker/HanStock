export type TdccSnapshotRecord = {
  ticker: string;
  dataDate: string;
  largeHolderPct: number;
};

type StoredRow = {
  ticker: string;
  dataDate: string;
  largeHolderPct: number;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

async function ensureSchema() {
  const d1 = getD1();
  if (!d1) throw new Error("tdcc_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS tdcc_weekly_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        large_holder_pct REAL NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS tdcc_weekly_date_ticker_idx ON tdcc_weekly_snapshots (data_date, ticker)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS tdcc_weekly_ticker_date_idx ON tdcc_weekly_snapshots (ticker, data_date)"),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveAndReadTdccSnapshots(records: TdccSnapshotRecord[]) {
  const d1 = await ensureSchema();
  const now = Date.now();
  for (let start = 0; start < records.length; start += 80) {
    const chunk = records.slice(start, start + 80);
    await d1.batch(chunk.map((record) => d1.prepare(`INSERT INTO tdcc_weekly_snapshots
      (data_date, ticker, large_holder_pct, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(data_date, ticker) DO UPDATE SET
        large_holder_pct = excluded.large_holder_pct,
        updated_at = excluded.updated_at`)
      .bind(record.dataDate, record.ticker, record.largeHolderPct, now)));
  }
  const dates = await d1.prepare("SELECT DISTINCT data_date AS dataDate FROM tdcc_weekly_snapshots ORDER BY data_date DESC LIMIT 2").all<{ dataDate: string }>();
  const selected = dates.results.map((row) => row.dataDate);
  if (selected.length === 0) return { dates: [], rows: [] as StoredRow[], updatedAt: null as number | null };
  // Each weekly snapshot has more than 3,300 rows. Reading two weeks in one
  // D1 result can cross the per-query row/result limit, so read each date in a
  // separate bounded query and combine them in newest-first order.
  const [results, timestamp] = await Promise.all([
    Promise.all(selected.map((dataDate) => d1.prepare(`SELECT ticker, data_date AS dataDate, large_holder_pct AS largeHolderPct
      FROM tdcc_weekly_snapshots WHERE data_date = ? ORDER BY ticker ASC`)
      .bind(dataDate)
      .all<StoredRow>())),
    d1.prepare("SELECT MAX(updated_at) AS updatedAt FROM tdcc_weekly_snapshots").first<{ updatedAt: number | null }>(),
  ]);
  return { dates: selected, rows: results.flatMap((result) => result.results), updatedAt: timestamp?.updatedAt ?? null };
}
