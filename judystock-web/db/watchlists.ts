import {
  applyWatchlistOperations,
  AFTER_HOURS_WATCHLIST_ID,
  normalizeWatchlists,
  type WatchlistDeviceKind,
  type WatchlistFolder,
  type WatchlistOperation,
  type WatchlistStock,
} from "../lib/watchlists";

type WatchlistStateRow = {
  userEmail: string;
  payload: string;
  revision: number;
  primaryDeviceId: string;
  updatedByDeviceId: string;
  updatedByDeviceKind: WatchlistDeviceKind;
  createdAt: number;
  updatedAt: number;
};

export type StoredWatchlistState = Omit<WatchlistStateRow, "payload"> & {
  watchlists: WatchlistFolder[];
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("watchlist_sync_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.prepare(`CREATE TABLE IF NOT EXISTS watchlist_sync_state (
      user_email TEXT PRIMARY KEY NOT NULL,
      payload TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1,
      primary_device_id TEXT NOT NULL,
      updated_by_device_id TEXT NOT NULL,
      updated_by_device_kind TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    )`).run().then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

function normalizeEmail(email: string) {
  return email.trim().toLowerCase().slice(0, 320);
}

function toStoredState(row: WatchlistStateRow | null): StoredWatchlistState | null {
  if (!row) return null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(row.payload);
  } catch {
    parsed = null;
  }
  return {
    userEmail: row.userEmail,
    watchlists: normalizeWatchlists(parsed),
    revision: Number(row.revision) || 1,
    primaryDeviceId: row.primaryDeviceId,
    updatedByDeviceId: row.updatedByDeviceId,
    updatedByDeviceKind: row.updatedByDeviceKind,
    createdAt: Number(row.createdAt),
    updatedAt: Number(row.updatedAt),
  };
}

function cleanDeviceId(deviceId: string) {
  const cleaned = deviceId.trim().slice(0, 100);
  if (!/^[0-9A-Za-z._:-]{8,100}$/.test(cleaned)) throw new Error("invalid_watchlist_device_id");
  return cleaned;
}

function cleanDeviceKind(deviceKind: string): WatchlistDeviceKind {
  if (deviceKind === "desktop" || deviceKind === "iphone" || deviceKind === "ipad") return deviceKind;
  throw new Error("invalid_watchlist_device_kind");
}

function cleanOperations(value: unknown): WatchlistOperation[] {
  if (!Array.isArray(value)) return [];
  const validFolderIds = new Set(Array.from({ length: 7 }, (_, index) => `watchlist-${index + 1}`));
  const operations: WatchlistOperation[] = [];
  for (const item of value.slice(0, 500)) {
    if (!item || typeof item !== "object") continue;
    const operation = item as Record<string, unknown>;
    const folderId = typeof operation.folderId === "string" ? operation.folderId : "";
    if (!validFolderIds.has(folderId)) continue;
    const automatic = folderId === AFTER_HOURS_WATCHLIST_ID;
    const tradeDate = typeof operation.tradeDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(operation.tradeDate) ? operation.tradeDate : undefined;
    if (automatic && !tradeDate) continue;
    if (operation.type === "moveStock" && typeof operation.ticker === "string" &&
      typeof operation.targetFolderId === "string" && validFolderIds.has(operation.targetFolderId) && operation.targetFolderId !== AFTER_HOURS_WATCHLIST_ID) {
      operations.push({type: "moveStock", folderId, targetFolderId: operation.targetFolderId, ticker: operation.ticker,
        ...(automatic ? {tradeDate, stock: operation.stock as WatchlistStock} : {})});
    } else if (!automatic && operation.type === "renameFolder" && typeof operation.name === "string") {
      operations.push({ type: "renameFolder", folderId, name: operation.name });
    } else if (operation.type === "removeStock" && typeof operation.ticker === "string") {
      operations.push({ type: "removeStock", folderId, ticker: operation.ticker, ...(automatic ? {tradeDate} : {}) });
    } else if (!automatic && operation.type === "addStock" && operation.stock && typeof operation.stock === "object") {
      operations.push({ type: "addStock", folderId, stock: operation.stock as WatchlistStock });
    }
  }
  return operations;
}

export async function readWatchlistState(email: string): Promise<StoredWatchlistState | null> {
  const d1 = await database();
  const row = await d1.prepare(`SELECT
    user_email AS userEmail,
    payload,
    revision,
    primary_device_id AS primaryDeviceId,
    updated_by_device_id AS updatedByDeviceId,
    updated_by_device_kind AS updatedByDeviceKind,
    created_at AS createdAt,
    updated_at AS updatedAt
    FROM watchlist_sync_state WHERE user_email = ? LIMIT 1`)
    .bind(normalizeEmail(email))
    .first<WatchlistStateRow>();
  return toStoredState(row);
}

export async function initializeWatchlistState(input: {
  email: string;
  watchlists: unknown;
  deviceId: string;
  deviceKind: string;
}): Promise<StoredWatchlistState> {
  const d1 = await database();
  const email = normalizeEmail(input.email);
  const deviceId = cleanDeviceId(input.deviceId);
  const deviceKind = cleanDeviceKind(input.deviceKind);
  const now = Date.now();
  const watchlists = normalizeWatchlists(input.watchlists);
  await d1.prepare(`INSERT OR IGNORE INTO watchlist_sync_state
    (user_email, payload, revision, primary_device_id, updated_by_device_id, updated_by_device_kind, created_at, updated_at)
    VALUES (?, ?, 1, ?, ?, ?, ?, ?)`)
    .bind(email, JSON.stringify(watchlists), deviceId, deviceId, deviceKind, now, now)
    .run();
  const state = await readWatchlistState(email);
  if (!state) throw new Error("watchlist_initialization_failed");
  return state;
}

export async function updateWatchlistState(input: {
  email: string;
  operations: unknown;
  deviceId: string;
  deviceKind: string;
}): Promise<StoredWatchlistState | null> {
  const d1 = await database();
  const email = normalizeEmail(input.email);
  const deviceId = cleanDeviceId(input.deviceId);
  const deviceKind = cleanDeviceKind(input.deviceKind);
  const operations = cleanOperations(input.operations);

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const current = await readWatchlistState(email);
    if (!current) return null;
    if (operations.length === 0) return current;
    const watchlists = applyWatchlistOperations(current.watchlists, operations);
    const updatedAt = Date.now();
    const nextRevision = current.revision + 1;
    const result = await d1.prepare(`UPDATE watchlist_sync_state SET
      payload = ?, revision = ?, updated_by_device_id = ?, updated_by_device_kind = ?, updated_at = ?
      WHERE user_email = ? AND revision = ?`)
      .bind(JSON.stringify(watchlists), nextRevision, deviceId, deviceKind, updatedAt, email, current.revision)
      .run();
    if ((result.meta.changes ?? 0) > 0) {
      return {
        ...current,
        watchlists,
        revision: nextRevision,
        updatedByDeviceId: deviceId,
        updatedByDeviceKind: deviceKind,
        updatedAt,
      };
    }
  }
  throw new Error("watchlist_concurrent_update_retry_exhausted");
}
