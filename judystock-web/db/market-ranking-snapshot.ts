type SavedMarketRanking = {
  dataDate: string;
  fetchedAt: string;
  [key: string]: unknown;
};

type D1Statement = {
  bind(...values: unknown[]): D1Statement;
  run(): Promise<unknown>;
  first<T>(): Promise<T | null>;
};
type HanstockD1 = {
  prepare(query: string): D1Statement;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: HanstockD1 }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("market_ranking_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.prepare(`CREATE TABLE IF NOT EXISTS market_ranking_latest (
      snapshot_key TEXT PRIMARY KEY,
      data_date TEXT NOT NULL,
      fetched_at TEXT NOT NULL,
      payload_gzip_base64 TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )`).run().then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(value: string) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

async function gzipJson(value: unknown) {
  const stream = new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream("gzip"));
  return bytesToBase64(new Uint8Array(await new Response(stream).arrayBuffer()));
}

async function ungzipJson(value: string) {
  const stream = new Blob([base64ToBytes(value)]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text()) as SavedMarketRanking;
}

export async function saveLatestMarketRanking(payload: SavedMarketRanking, snapshotKey = "latest") {
  const d1 = await database();
  const compressed = await gzipJson(payload);
  await d1.prepare(`INSERT INTO market_ranking_latest
    (snapshot_key, data_date, fetched_at, payload_gzip_base64, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(snapshot_key) DO UPDATE SET
      data_date=excluded.data_date,
      fetched_at=excluded.fetched_at,
      payload_gzip_base64=excluded.payload_gzip_base64,
      updated_at=excluded.updated_at
    WHERE excluded.data_date >= market_ranking_latest.data_date`)
    .bind(snapshotKey, payload.dataDate, payload.fetchedAt, compressed, Date.now())
    .run();
}

export async function readLatestMarketRanking(snapshotKey = "latest") {
  const d1 = await database();
  const row = await d1.prepare(`SELECT data_date AS dataDate, payload_gzip_base64 AS payload
    FROM market_ranking_latest WHERE snapshot_key=? LIMIT 1`)
    .bind(snapshotKey)
    .first<{ dataDate: string; payload: string }>();
  if (!row?.payload) return null;
  const payload = await ungzipJson(row.payload);
  return payload.dataDate === row.dataDate ? payload : null;
}
