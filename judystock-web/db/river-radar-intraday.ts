export type RiverIntradaySnapshot = { code: string; tradeDate: string; barTs: number; score: number; side: "bull" | "bear" | "neutral"; [key: string]: unknown };
export type RiverIntradaySignal = { code: string; tradeDate: string; direction: "bull" | "bear"; barTs: number; score: number; [key: string]: unknown };
export type RiverStrategySignal = RiverIntradaySignal & { signalType: "daily-strategy" | "black-dragon"; strategyKind: string; strategyName: string };
export type RiverIntradayScanState = {
  status: "running" | "ok" | "degraded" | "error";
  startedAt: number;
  completedAt?: number;
  tradeDate?: string;
  barCoverage?: number;
  totalCandidates?: number;
  strategyHistoryCoverage?: number;
  strategyHistoryCoverageRequired?: boolean;
  strategyWindowActive?: boolean;
  reason?: string;
  nextGroupIndex?: number;
  scannedGroupCount?: number;
  totalGroupCount?: number;
  cycleCompletedAt?: number;
};

const RIVER_INTRADAY_SCAN_KEY = "river-intraday-scan-state";
const RIVER_INTRADAY_SCAN_ENGINE = "river-intraday-scan-v2";

function getD1() { const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }; return runtime.__HANSTOCK_DB ?? null; }

// A production publish can terminate an in-flight request without reaching the
// route's catch block.  Keep the stale-running lease only slightly longer than
// a normal 20–45 second scan so the next foreground cycle can recover quickly.
export async function acquireRiverIntradayScanLease(startedAt = Date.now(), minimumIntervalMs = 2_000, runningLeaseMs = 120_000) {
  const d1 = getD1();
  if (!d1) return true;
  const previous = await readRiverIntradayScanState().catch(() => null);
  const running = { ...previous, status: "running", startedAt, reason: undefined } satisfies RiverIntradayScanState;
  const result = await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET
      config_json=excluded.config_json,
      engine_version=excluded.engine_version,
      updated_at=excluded.updated_at
    WHERE (json_extract(river_radar_config.config_json, '$.status') <> 'running' AND river_radar_config.updated_at <= ?)
      OR river_radar_config.updated_at <= ?`)
    .bind(RIVER_INTRADAY_SCAN_KEY, JSON.stringify(running), RIVER_INTRADAY_SCAN_ENGINE, startedAt, startedAt - minimumIntervalMs, startedAt - runningLeaseMs)
    .run();
  return (result.meta.changes ?? 0) > 0;
}

export async function saveRiverIntradayScanState(state: RiverIntradayScanState) {
  const d1 = getD1();
  if (!d1) return false;
  const updatedAt = state.status === "running" ? state.startedAt : state.completedAt ?? state.startedAt;
  await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET
      config_json=excluded.config_json,
      engine_version=excluded.engine_version,
      updated_at=excluded.updated_at`)
    .bind(RIVER_INTRADAY_SCAN_KEY, JSON.stringify(state), RIVER_INTRADAY_SCAN_ENGINE, updatedAt)
    .run();
  return true;
}

export async function readRiverIntradayScanState() {
  const d1 = getD1();
  if (!d1) return null as RiverIntradayScanState | null;
  const row = await d1.prepare(`SELECT config_json AS configJson FROM river_radar_config WHERE config_key = ?`)
    .bind(RIVER_INTRADAY_SCAN_KEY)
    .first<{ configJson: string }>();
  if (!row?.configJson) return null;
  try {
    const parsed = JSON.parse(row.configJson) as RiverIntradayScanState;
    return parsed?.status && Number.isFinite(parsed.startedAt) ? parsed : null;
  } catch {
    return null;
  }
}

export async function saveRiverIntraday(rows: RiverIntradaySnapshot[], signals: RiverIntradaySignal[]) {
  const d1 = getD1(); if (!d1) return false; const now = Date.now();
  for (let start = 0; start < rows.length; start += 40) await d1.batch(rows.slice(start, start + 40).map((row) => d1.prepare(`INSERT INTO river_radar_intraday (trade_date, stock_code, bar_ts, score, side, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(trade_date, stock_code) DO UPDATE SET bar_ts=excluded.bar_ts, score=excluded.score, side=excluded.side, payload_json=excluded.payload_json, updated_at=excluded.updated_at`).bind(row.tradeDate, row.code, row.barTs, row.score, row.side, JSON.stringify(row), now)));
  for (let start = 0; start < signals.length; start += 40) await d1.batch(signals.slice(start, start + 40).map((signal) => d1.prepare(`INSERT INTO river_radar_signals (trade_date, stock_code, direction, bar_ts, score, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(trade_date, stock_code, direction) DO UPDATE SET bar_ts=CASE WHEN COALESCE(json_extract(river_radar_signals.payload_json, '$.selectionRuleVersion'), '') <> COALESCE(json_extract(excluded.payload_json, '$.selectionRuleVersion'), '') THEN excluded.bar_ts WHEN json_extract(river_radar_signals.payload_json, '$.selectionSource') = 'focus-ranking-67' AND json_extract(river_radar_signals.payload_json, '$.groupName') = json_extract(excluded.payload_json, '$.groupName') THEN MIN(river_radar_signals.bar_ts, excluded.bar_ts) ELSE excluded.bar_ts END, score=excluded.score, payload_json=excluded.payload_json, updated_at=excluded.updated_at`).bind(signal.tradeDate, signal.code, signal.direction, signal.barTs, signal.score, JSON.stringify(signal), now)));
  return true;
}

export async function readRiverSignals(tradeDate: string) {
  const d1 = getD1(); if (!d1) return [] as RiverIntradaySignal[];
  const result = await d1.prepare(`SELECT bar_ts AS barTs, payload_json AS payloadJson, updated_at AS updatedAt FROM river_radar_signals WHERE trade_date = ? ORDER BY updated_at DESC, bar_ts DESC`).bind(tradeDate).all<{ barTs: number; payloadJson: string; updatedAt: number }>();
  return result.results.flatMap((item) => { try { const parsed = JSON.parse(item.payloadJson) as RiverIntradaySignal; return parsed?.code ? [{ ...parsed, barTs: Number(item.barTs), updatedAt: Number(item.updatedAt) }] : []; } catch { return []; } });
}

export async function saveRiverStrategySignals(signals: RiverStrategySignal[]) {
  const d1 = getD1();
  if (!d1 || !signals.length) return false;
  const now = Date.now();
  for (let start = 0; start < signals.length; start += 40) {
    await d1.batch(signals.slice(start, start + 40).map((signal) => d1.prepare(`INSERT INTO river_radar_strategy_signals
      (trade_date, stock_code, strategy_kind, direction, bar_ts, score, payload_json, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(trade_date, stock_code, strategy_kind) DO UPDATE SET
      bar_ts=CASE
        WHEN COALESCE(json_extract(river_radar_strategy_signals.payload_json, '$.selectionRuleVersion'), '')
          <> COALESCE(json_extract(excluded.payload_json, '$.selectionRuleVersion'), '')
        THEN excluded.bar_ts
        WHEN excluded.strategy_kind = 'blackDragon'
        THEN MIN(river_radar_strategy_signals.bar_ts, excluded.bar_ts)
        WHEN json_extract(river_radar_strategy_signals.payload_json, '$.selectionSource') = 'focus-ranking-67'
          AND json_extract(river_radar_strategy_signals.payload_json, '$.groupName') = json_extract(excluded.payload_json, '$.groupName')
        THEN MIN(river_radar_strategy_signals.bar_ts, excluded.bar_ts)
        ELSE excluded.bar_ts
      END, score=excluded.score,
      direction=excluded.direction, payload_json=excluded.payload_json, updated_at=excluded.updated_at
      WHERE excluded.strategy_kind <> 'blackDragon'
        OR COALESCE(json_extract(river_radar_strategy_signals.payload_json, '$.selectionRuleVersion'), '')
          <> COALESCE(json_extract(excluded.payload_json, '$.selectionRuleVersion'), '')
        OR excluded.bar_ts <= river_radar_strategy_signals.bar_ts`)
      .bind(signal.tradeDate, signal.code, signal.strategyKind, signal.direction, signal.barTs, signal.score, JSON.stringify(signal), now)));
  }
  return true;
}

export async function readRiverStrategySignals(tradeDate: string) {
  const d1 = getD1();
  if (!d1) return [] as RiverStrategySignal[];
  const result = await d1.prepare(`SELECT bar_ts AS barTs, payload_json AS payloadJson FROM river_radar_strategy_signals WHERE trade_date = ? ORDER BY bar_ts DESC`)
    .bind(tradeDate).all<{ barTs: number; payloadJson: string }>();
  return result.results.flatMap((item) => {
    try {
      const parsed = JSON.parse(item.payloadJson) as RiverStrategySignal;
      return parsed?.code && parsed?.strategyKind ? [{ ...parsed, barTs: Number(item.barTs) }] : [];
    } catch { return []; }
  });
}
