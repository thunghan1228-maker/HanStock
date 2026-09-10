import { BLACK_DRAGON_SELECTION_VERSION } from "../lib/black-dragon-session";

export type BlackDragonIntradayScanState = {
  status: "idle" | "running" | "ready" | "error";
  tradeDate: string;
  startIndex: number;
  nextIndex: number;
  processed: number;
  total: number;
  cycle: number;
  barCoverage?: number;
  signalCount?: number;
  updatedAt: number;
  reason?: string;
};

const CONFIG_KEY = `black-dragon-intraday-scan-state:${BLACK_DRAGON_SELECTION_VERSION}:serialized-v1`;
const ENGINE_VERSION = BLACK_DRAGON_SELECTION_VERSION;
let localState: BlackDragonIntradayScanState | null = null;

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

export async function readBlackDragonIntradayScanState() {
  const d1 = getD1();
  if (!d1) return localState;
  const row = await d1.prepare("SELECT config_json AS configJson FROM river_radar_config WHERE config_key = ?")
    .bind(CONFIG_KEY).first<{ configJson: string }>();
  if (!row?.configJson) return null;
  try {
    const parsed = JSON.parse(row.configJson) as BlackDragonIntradayScanState;
    return parsed?.tradeDate && Number.isFinite(parsed.nextIndex) ? parsed : null;
  } catch { return null; }
}

export async function claimBlackDragonIntradayBatch(
  tradeDate: string,
  total: number,
  batchSize: number,
  minimumIntervalMs = 4_000,
  runningLeaseMs = 55_000,
) {
  const now = Date.now();
  const previous = await readBlackDragonIntradayScanState().catch(() => null);
  const sameSession = previous?.tradeDate === tradeDate && previous.total === total;
  const retryUnfinished = previous?.status === "running" || previous?.status === "error";
  const startIndex = sameSession ? Math.max(0, Math.min(total - 1, Number(retryUnfinished ? previous?.startIndex : previous?.nextIndex) || 0)) : 0;
  const processed = Math.min(batchSize, total);
  const wraps = total > 0 && startIndex + processed >= total;
  const state = {
    status: "running",
    tradeDate,
    startIndex,
    nextIndex: total > 0 ? (startIndex + processed) % total : 0,
    processed: sameSession ? Number(previous?.processed) || 0 : 0,
    total,
    cycle: (sameSession ? Number(previous?.cycle) || 0 : 0) + (wraps ? 1 : 0),
    updatedAt: now,
  } satisfies BlackDragonIntradayScanState;
  const d1 = getD1();
  if (!d1) {
    const blocked = localState?.status === "running" && now - localState.updatedAt < runningLeaseMs;
    if (blocked || (localState && now - localState.updatedAt < minimumIntervalMs)) return null;
    localState = state;
    return state;
  }
  const result = await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET
      config_json=excluded.config_json,
      engine_version=excluded.engine_version,
      updated_at=excluded.updated_at
    WHERE (COALESCE(json_extract(river_radar_config.config_json, '$.status'), '') <> 'running'
        AND river_radar_config.updated_at <= ?)
      OR (json_extract(river_radar_config.config_json, '$.status') = 'running'
        AND river_radar_config.updated_at <= ?)
      OR COALESCE(json_extract(river_radar_config.config_json, '$.tradeDate'), '') <> ?`)
    .bind(CONFIG_KEY, JSON.stringify(state), ENGINE_VERSION, now, now - minimumIntervalMs, now - runningLeaseMs, tradeDate)
    .run();
  return (result.meta.changes ?? 0) > 0 ? state : null;
}

export async function finishBlackDragonIntradayBatch(state: BlackDragonIntradayScanState) {
  const finished = { ...state, updatedAt: Date.now() };
  const d1 = getD1();
  if (!d1) {
    if (localState?.updatedAt === state.updatedAt) localState = finished;
    return false;
  }
  // A timed-out request must not overwrite the progress of its replacement.
  const result = await d1.prepare(`UPDATE river_radar_config
    SET config_json = ?, engine_version = ?, updated_at = ?
    WHERE config_key = ? AND updated_at = ?`)
    .bind(JSON.stringify(finished), ENGINE_VERSION, finished.updatedAt, CONFIG_KEY, state.updatedAt)
    .run();
  return (result.meta.changes ?? 0) > 0;
}
