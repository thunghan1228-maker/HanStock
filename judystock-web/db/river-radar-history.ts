import { RIVER_ENGINE_VERSION } from "../lib/river-radar";

export type StoredRiverRadarRow = {
  code: string;
  market: "twse" | "tpex";
  riverScore: number;
  riverStatus: string;
  riverSide: "bull" | "neutral" | "bear";
  [key: string]: unknown;
};

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

export async function saveRiverRadarDaily(dataDate: string, rows: StoredRiverRadarRow[]) {
  const d1 = getD1();
  if (!d1 || !/^\d{4}\/\d{2}\/\d{2}$/.test(dataDate) || rows.length < 1_000) return false;
  const now = Date.now();
  for (let start = 0; start < rows.length; start += 40) {
    await d1.batch(rows.slice(start, start + 40).map((row) => d1.prepare(`INSERT INTO river_radar_daily
      (data_date, stock_code, market, score, status, side, payload_json, engine_version, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(data_date, stock_code) DO UPDATE SET
        market=excluded.market, score=excluded.score, status=excluded.status, side=excluded.side,
        payload_json=excluded.payload_json, engine_version=excluded.engine_version, updated_at=excluded.updated_at`)
      .bind(dataDate, row.code, row.market, row.riverScore, row.riverStatus, row.riverSide, JSON.stringify(row), RIVER_ENGINE_VERSION, now)));
  }
  await d1.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES ('production', ?, ?, ?)
    ON CONFLICT(config_key) DO UPDATE SET config_json=excluded.config_json, engine_version=excluded.engine_version, updated_at=excluded.updated_at`)
    .bind(JSON.stringify({ maPeriods: [5, 10, 20, 60, 120, 240], scoreRange: [0, 100] }), RIVER_ENGINE_VERSION, now).run();
  return true;
}

export async function readLatestRiverRadarDaily() {
  const d1 = getD1();
  if (!d1) return null;
  const latest = await d1.prepare("SELECT MAX(data_date) AS dataDate FROM river_radar_daily").first<{ dataDate: string | null }>();
  if (!latest?.dataDate) return null;
  const result = await d1.prepare(`SELECT payload_json AS payloadJson, engine_version AS engineVersion, updated_at AS updatedAt FROM river_radar_daily
    WHERE data_date = ? ORDER BY score DESC`).bind(latest.dataDate).all<{ payloadJson: string; engineVersion: string; updatedAt: number }>();
  const rows = result.results.flatMap((item) => {
    try {
      const parsed = JSON.parse(item.payloadJson) as StoredRiverRadarRow;
      return parsed?.code ? [parsed] : [];
    } catch {
      return [];
    }
  });
  const updatedAt = result.results.reduce((maximum, item) => Math.max(maximum, Number(item.updatedAt) || 0), 0);
  return rows.length ? {
    dataDate: latest.dataDate,
    rows,
    engineVersion: result.results[0]?.engineVersion ?? null,
    updatedAt: updatedAt > 0 ? new Date(updatedAt).toISOString() : null,
  } : null;
}
