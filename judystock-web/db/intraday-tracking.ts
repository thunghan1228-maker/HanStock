import {
  normalizeIntradayTrackingSettings,
  type StoredIntradayTrackingState,
} from "../lib/intraday-tracking";
import type { WatchlistDeviceKind } from "../lib/watchlists";

type IntradayTrackingStateRow = {
  userEmail: string;
  payload: string;
  revision: number;
  updatedByDeviceId: string;
  updatedByDeviceKind: WatchlistDeviceKind;
  createdAt: number;
  updatedAt: number;
};

function database() {
  const d1 = (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB;
  if (!d1) throw new Error("intraday_tracking_storage_unavailable");
  return d1;
}

function normalizeEmail(email: string) {
  return email.trim().toLowerCase().slice(0, 320);
}

function cleanDeviceId(deviceId: string) {
  const cleaned = deviceId.trim().slice(0, 100);
  if (!/^[0-9A-Za-z._:-]{8,100}$/.test(cleaned)) throw new Error("invalid_intraday_tracking_device_id");
  return cleaned;
}

function cleanDeviceKind(deviceKind: string): WatchlistDeviceKind {
  if (deviceKind === "desktop" || deviceKind === "iphone" || deviceKind === "ipad") return deviceKind;
  throw new Error("invalid_intraday_tracking_device_kind");
}

function toStoredState(row: IntradayTrackingStateRow | null): StoredIntradayTrackingState | null {
  if (!row) return null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(row.payload);
  } catch {
    parsed = null;
  }
  return {
    userEmail: row.userEmail,
    settings: normalizeIntradayTrackingSettings(parsed),
    revision: Number(row.revision) || 1,
    updatedByDeviceId: row.updatedByDeviceId,
    updatedByDeviceKind: row.updatedByDeviceKind,
    createdAt: Number(row.createdAt),
    updatedAt: Number(row.updatedAt),
  };
}

export async function readIntradayTrackingState(email: string): Promise<StoredIntradayTrackingState | null> {
  const row = await database().prepare(`SELECT
    user_email AS userEmail,
    payload,
    revision,
    updated_by_device_id AS updatedByDeviceId,
    updated_by_device_kind AS updatedByDeviceKind,
    created_at AS createdAt,
    updated_at AS updatedAt
    FROM intraday_tracking_sync_state WHERE user_email = ? LIMIT 1`)
    .bind(normalizeEmail(email))
    .first<IntradayTrackingStateRow>();
  return toStoredState(row);
}

export async function initializeIntradayTrackingState(input: {
  email: string;
  settings: unknown;
  deviceId: string;
  deviceKind: string;
}): Promise<StoredIntradayTrackingState> {
  const d1 = database();
  const email = normalizeEmail(input.email);
  const deviceId = cleanDeviceId(input.deviceId);
  const deviceKind = cleanDeviceKind(input.deviceKind);
  const settings = normalizeIntradayTrackingSettings(input.settings);
  const now = Date.now();
  await d1.prepare(`INSERT OR IGNORE INTO intraday_tracking_sync_state
    (user_email, payload, revision, updated_by_device_id, updated_by_device_kind, created_at, updated_at)
    VALUES (?, ?, 1, ?, ?, ?, ?)`)
    .bind(email, JSON.stringify(settings), deviceId, deviceKind, now, now)
    .run();
  const state = await readIntradayTrackingState(email);
  if (!state) throw new Error("intraday_tracking_initialization_failed");
  return state;
}

export async function updateIntradayTrackingState(input: {
  email: string;
  settings: unknown;
  deviceId: string;
  deviceKind: string;
}): Promise<StoredIntradayTrackingState | null> {
  const d1 = database();
  const email = normalizeEmail(input.email);
  const deviceId = cleanDeviceId(input.deviceId);
  const deviceKind = cleanDeviceKind(input.deviceKind);
  const settings = normalizeIntradayTrackingSettings(input.settings);

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const current = await readIntradayTrackingState(email);
    if (!current) return null;
    const updatedAt = Date.now();
    const nextRevision = current.revision + 1;
    const result = await d1.prepare(`UPDATE intraday_tracking_sync_state SET
      payload = ?, revision = ?, updated_by_device_id = ?, updated_by_device_kind = ?, updated_at = ?
      WHERE user_email = ? AND revision = ?`)
      .bind(JSON.stringify(settings), nextRevision, deviceId, deviceKind, updatedAt, email, current.revision)
      .run();
    if ((result.meta.changes ?? 0) > 0) {
      return {
        ...current,
        settings,
        revision: nextRevision,
        updatedByDeviceId: deviceId,
        updatedByDeviceKind: deviceKind,
        updatedAt,
      };
    }
  }
  throw new Error("intraday_tracking_concurrent_update_retry_exhausted");
}
