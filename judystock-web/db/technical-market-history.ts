export type TechnicalMarketSnapshotRow = {
  code: string;
  name?: string;
  market: "twse" | "tpex";
  open: number;
  high?: number;
  low?: number;
  close: number;
  volume?: number;
};

type StoredSnapshot = {
  tradeDate: string;
  payloadJson: string;
};

export type TechnicalMarketSnapshotMetadata = {
  latestDate: string;
  dayCount: number;
};

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

export async function saveTechnicalMarketSnapshots(days: Array<{ tradeDate: string; rows: TechnicalMarketSnapshotRow[] }>) {
  const d1 = getD1();
  if (!d1 || days.length === 0) return false;
  const now = Date.now();
  // A full-market day is a fairly large JSON value. Writing twenty of them in
  // one D1 batch caused the whole batch to be rejected when the request/body
  // limit was reached, leaving only a few scattered dates in the database.
  // Save one day at a time so a single large day cannot discard the rest.
  for (const day of days) {
    await d1.prepare(`INSERT INTO technical_market_daily_snapshots
      (trade_date, payload_json, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(trade_date) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at`)
      .bind(day.tradeDate, JSON.stringify(day.rows), now).run();
  }
  return true;
}

export async function readTechnicalMarketSnapshots(limit = 270) {
  const d1 = getD1();
  if (!d1) return [] as Array<{ tradeDate: string; rows: TechnicalMarketSnapshotRow[] }>;
  const result = await d1.prepare(`SELECT trade_date AS tradeDate, payload_json AS payloadJson
    FROM technical_market_daily_snapshots ORDER BY trade_date DESC LIMIT ?`).bind(limit).all<StoredSnapshot>();
  return result.results.flatMap((snapshot: StoredSnapshot) => {
    try {
      const rows = JSON.parse(snapshot.payloadJson) as TechnicalMarketSnapshotRow[];
      return Array.isArray(rows) ? [{ tradeDate: snapshot.tradeDate, rows }] : [];
    } catch {
      return [];
    }
  });
}

export async function readPreviousTechnicalMarketSnapshot(tradeDate: string) {
  const d1 = getD1();
  if (!d1) return null;
  const date = tradeDate.replaceAll("-", "/");
  const snapshot = await d1.prepare(`SELECT trade_date AS tradeDate, payload_json AS payloadJson
    FROM technical_market_daily_snapshots WHERE trade_date < ? ORDER BY trade_date DESC LIMIT 1`)
    .bind(date).first<StoredSnapshot>();
  if (!snapshot) return null;
  try {
    const rows = JSON.parse(snapshot.payloadJson) as TechnicalMarketSnapshotRow[];
    return Array.isArray(rows) ? { tradeDate: snapshot.tradeDate, rows } : null;
  } catch { return null; }
}

export async function readLatestTechnicalMarketSnapshot() {
  const d1 = getD1();
  if (!d1) return null as { tradeDate: string; rows: TechnicalMarketSnapshotRow[] } | null;
  const snapshot = await d1.prepare(`SELECT trade_date AS tradeDate, payload_json AS payloadJson
    FROM technical_market_daily_snapshots ORDER BY trade_date DESC LIMIT 1`).first<StoredSnapshot>();
  if (!snapshot) return null;
  try {
    const rows = JSON.parse(snapshot.payloadJson) as TechnicalMarketSnapshotRow[];
    return Array.isArray(rows) ? { tradeDate: snapshot.tradeDate, rows } : null;
  } catch {
    return null;
  }
}

export async function readTechnicalMarketSnapshotMetadata() {
  const d1 = getD1();
  if (!d1) return null as TechnicalMarketSnapshotMetadata | null;
  // Keep this metadata lookup index-only. Reading the length of every stored
  // full-market JSON snapshot defeated the purpose of the ranking cache.
  const row = await d1.prepare(`SELECT COALESCE(MAX(trade_date), '') AS latestDate,
    COUNT(*) AS dayCount FROM technical_market_daily_snapshots`).first<{ latestDate: string; dayCount: number }>();
  return row ? {
    latestDate: String(row.latestDate ?? ""),
    dayCount: Number(row.dayCount) || 0,
  } : null;
}
