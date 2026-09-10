import type { IntradayLargeForceValue } from "../lib/intraday-large-force";

export type IntradayLargeForceScanProgress = {
  revision?: string;
  tradeDate: string;
  status: "idle" | "running" | "completed" | "error";
  nextIndex: number;
  processed: number;
  available: number;
  signalCount: number;
  total: number;
  cycle: number;
  startedAt: number;
  updatedAt: number;
  completedAt?: number;
  leaseId?: string;
  message?: string;
};

export type IntradayLargeForceMonitorRow = IntradayLargeForceValue & {
  ticker: string;
  name: string;
  group: string;
};

const ENGINE = "intraday-large-force-scan-v1";
const SCAN_REVISION = "ordinary-stock-full-pattern-batch-20260907";
const MONITOR_ENGINE = "intraday-large-force-monitor-v1";
const LEASE_MS = 50_000;
const memoryMonitorChunks = new Map<string, { updatedAt: number; rows: IntradayLargeForceMonitorRow[] }>();

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

function key(tradeDate: string) {
  return `intraday-large-force-scan:${tradeDate}`;
}

function monitorKey(tradeDate: string, batchStart: number) {
  return `intraday-large-force-monitor:${tradeDate}:${String(batchStart).padStart(5, "0")}`;
}

function monitorPrefix(tradeDate: string) {
  return `intraday-large-force-monitor:${tradeDate}:%`;
}

export async function saveIntradayLargeForceMonitorRows(options: {
  tradeDate: string;
  batchStart: number;
  rows: IntradayLargeForceMonitorRow[];
}) {
  const updatedAt = Date.now();
  const configKey = monitorKey(options.tradeDate, options.batchStart);
  const payload = { tradeDate: options.tradeDate, batchStart: options.batchStart, updatedAt, rows: options.rows };
  memoryMonitorChunks.set(configKey, { updatedAt, rows: options.rows });
  const d1 = getD1();
  if (!d1) return payload;
  await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET
      config_json=excluded.config_json,
      engine_version=excluded.engine_version,
      updated_at=excluded.updated_at`)
    .bind(configKey, JSON.stringify(payload), MONITOR_ENGINE, updatedAt)
    .run();
  return payload;
}

export async function readIntradayLargeForceMonitorRows(tradeDate: string) {
  const chunks: Array<{ updatedAt: number; rows: IntradayLargeForceMonitorRow[] }> = [];
  const d1 = getD1();
  if (d1) {
    const result = await d1.prepare(`SELECT config_json AS configJson, updated_at AS updatedAt
      FROM river_radar_config
      WHERE config_key LIKE ?
      ORDER BY config_key ASC`)
      .bind(monitorPrefix(tradeDate))
      .all<{ configJson: string; updatedAt: number }>();
    for (const row of result.results ?? []) {
      try {
        const parsed = JSON.parse(row.configJson) as { rows?: IntradayLargeForceMonitorRow[] };
        if (Array.isArray(parsed.rows)) chunks.push({ updatedAt: row.updatedAt, rows: parsed.rows });
      } catch {
        // 略過單一損壞分批，其他已完成的大戶力資料仍可顯示。
      }
    }
  } else {
    for (const [configKey, chunk] of memoryMonitorChunks) {
      if (configKey.startsWith(`intraday-large-force-monitor:${tradeDate}:`)) chunks.push(chunk);
    }
  }
  const byTicker = new Map<string, IntradayLargeForceMonitorRow>();
  for (const chunk of chunks) {
    for (const row of chunk.rows) {
      const previous = byTicker.get(row.ticker);
      if (!previous || row.barTs >= previous.barTs) byTicker.set(row.ticker, row);
    }
  }
  return {
    tradeDate,
    updatedAt: chunks.reduce((latest, chunk) => Math.max(latest, chunk.updatedAt), 0),
    rows: [...byTicker.values()].sort((left, right) => Math.abs(right.forcePct) - Math.abs(left.forcePct) || right.barTs - left.barTs || left.ticker.localeCompare(right.ticker)),
  };
}

function freshProgress(tradeDate: string, total: number, now: number, cycle = 1): IntradayLargeForceScanProgress {
  return {
    revision: SCAN_REVISION,
    tradeDate,
    status: "idle",
    nextIndex: 0,
    processed: 0,
    available: 0,
    signalCount: 0,
    total,
    cycle,
    startedAt: now,
    updatedAt: now,
  };
}

export async function readIntradayLargeForceScanProgress(tradeDate: string) {
  const d1 = getD1();
  if (!d1) return null as IntradayLargeForceScanProgress | null;
  const row = await d1.prepare("SELECT config_json AS configJson FROM river_radar_config WHERE config_key = ?")
    .bind(key(tradeDate))
    .first<{ configJson: string }>();
  if (!row?.configJson) return null;
  try {
    const parsed = JSON.parse(row.configJson) as IntradayLargeForceScanProgress;
    return parsed?.tradeDate === tradeDate && Number.isFinite(parsed.nextIndex) ? parsed : null;
  } catch {
    return null;
  }
}

export async function acquireIntradayLargeForceScanBatch(options: {
  tradeDate: string;
  total: number;
  restartCompleted: boolean;
  restartCooldownMs?: number;
}) {
  const now = Date.now();
  const d1 = getD1();
  const previous = await readIntradayLargeForceScanProgress(options.tradeDate).catch(() => null);
  const restartCooldownMs = options.restartCooldownMs ?? 60_000;
  // Revisit an older incomplete algorithm once, including after the close.
  // The D1 lease still prevents two viewers from scanning the same batch.
  const revisionChanged = Boolean(previous && previous.revision !== SCAN_REVISION);
  const shouldRestart = Boolean(previous?.status === "completed"
    && (revisionChanged || (options.restartCompleted && now - previous.updatedAt >= restartCooldownMs)));
  if (previous?.status === "completed" && !shouldRestart) {
    return { acquired: false, progress: previous };
  }
  if (previous?.status === "running" && now - previous.updatedAt < LEASE_MS) {
    return { acquired: false, progress: previous };
  }

  const base = shouldRestart || revisionChanged
    ? freshProgress(options.tradeDate, options.total, now, (previous?.cycle ?? 0) + 1)
    : { ...freshProgress(options.tradeDate, options.total, now), ...previous, total: options.total };
  const leaseId = crypto.randomUUID();
  const progress: IntradayLargeForceScanProgress = {
    ...base,
    revision: SCAN_REVISION,
    status: "running",
    startedAt: now,
    updatedAt: now,
    completedAt: undefined,
    leaseId,
    message: undefined,
  };
  if (!d1) return { acquired: true, progress };

  const result = await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET
      config_json=excluded.config_json,
      engine_version=excluded.engine_version,
      updated_at=excluded.updated_at
    WHERE json_extract(river_radar_config.config_json, '$.status') <> 'running'
      OR river_radar_config.updated_at <= ?`)
    .bind(key(options.tradeDate), JSON.stringify(progress), ENGINE, now, now - LEASE_MS)
    .run();
  if ((result.meta.changes ?? 0) > 0) return { acquired: true, progress };
  return {
    acquired: false,
    progress: await readIntradayLargeForceScanProgress(options.tradeDate).catch(() => previous),
  };
}

export async function finishIntradayLargeForceScanBatch(options: {
  progress: IntradayLargeForceScanProgress;
  processed: number;
  available: number;
  signalCount: number;
}) {
  const now = Date.now();
  const nextIndex = Math.min(options.progress.total, options.progress.nextIndex + options.processed);
  const completed = nextIndex >= options.progress.total;
  const progress: IntradayLargeForceScanProgress = {
    ...options.progress,
    status: completed ? "completed" : "idle",
    nextIndex,
    processed: Math.min(options.progress.total, options.progress.processed + options.processed),
    available: options.progress.available + options.available,
    signalCount: options.progress.signalCount + options.signalCount,
    updatedAt: now,
    completedAt: completed ? now : undefined,
    leaseId: undefined,
  };
  const d1 = getD1();
  if (!d1) return progress;
  await d1.prepare(`UPDATE river_radar_config
    SET config_json = ?, engine_version = ?, updated_at = ?
    WHERE config_key = ? AND json_extract(config_json, '$.leaseId') = ?`)
    .bind(JSON.stringify(progress), ENGINE, now, key(progress.tradeDate), options.progress.leaseId ?? "")
    .run();
  return await readIntradayLargeForceScanProgress(progress.tradeDate).catch(() => progress) ?? progress;
}

export async function failIntradayLargeForceScanBatch(progress: IntradayLargeForceScanProgress, message: string) {
  const now = Date.now();
  const failed: IntradayLargeForceScanProgress = {
    ...progress,
    status: "error",
    updatedAt: now,
    leaseId: undefined,
    message,
  };
  const d1 = getD1();
  if (!d1) return failed;
  await d1.prepare(`UPDATE river_radar_config
    SET config_json = ?, engine_version = ?, updated_at = ?
    WHERE config_key = ? AND json_extract(config_json, '$.leaseId') = ?`)
    .bind(JSON.stringify(failed), ENGINE, now, key(progress.tradeDate), progress.leaseId ?? "")
    .run();
  return failed;
}
