import type { KlineSnapshot } from "../lib/kline-snapshot";

let ready: Promise<void> | null = null;
async function database() {
  const db = (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB;
  if (!db) throw Error("kline_snapshot_storage_unavailable");
  ready ??= db.prepare(`CREATE TABLE IF NOT EXISTS kline_snapshots_v1 (
    ticker TEXT NOT NULL, interval TEXT NOT NULL, payload TEXT NOT NULL, refreshed_at INTEGER NOT NULL, phase TEXT NOT NULL,
    PRIMARY KEY(ticker, interval)
  )`).run().then(() => undefined).catch(error => { ready = null; throw error; });
  await ready;
  return db;
}

export async function readKlineSnapshot(ticker: string, interval: string) {
  const db = await database();
  const row = await db.prepare("SELECT payload, refreshed_at AS refreshedAt, phase FROM kline_snapshots_v1 WHERE ticker = ? AND interval = ?")
    .bind(ticker, interval).first<{ payload: string; refreshedAt: number; phase: string }>();
  if (!row) return null;
  const value = JSON.parse(row.payload) as KlineSnapshot;
  return { value, available: value.candles?.length > 0, refreshedAt: row.refreshedAt, phase: row.phase };
}

export async function saveKlineSnapshot(ticker: string, interval: string, value: KlineSnapshot, refreshedAt: number, phase: string) {
  const payload = JSON.stringify(value);
  if (!value.candles.length || new TextEncoder().encode(payload).length > 1_800_000) return;
  const db = await database();
  await db.prepare(`INSERT INTO kline_snapshots_v1 (ticker, interval, payload, refreshed_at, phase) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(ticker, interval) DO UPDATE SET payload = excluded.payload, refreshed_at = excluded.refreshed_at, phase = excluded.phase`)
    .bind(ticker, interval, payload, refreshedAt, phase).run();
}
