import type { RankingForceClose } from "../lib/ranking-force-close.ts";

const memory = new Map<string, RankingForceClose>();
const prefix = (date: string) => `ranking-force-close:${date}:`;
function getD1() { return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB; }

export async function readRankingForceClose(tradeDate: string): Promise<RankingForceClose[]> {
  const db = getD1();
  if (!db) return [...memory.values()].filter(row => row.tradeDate === tradeDate);
  const result = await db.prepare(`SELECT config_json AS configJson FROM river_radar_config
    WHERE config_key >= ? AND config_key < ?`).bind(prefix(tradeDate), prefix(tradeDate) + "\uffff").all<{ configJson: string }>();
  return result.results.flatMap(row => {
    try { const value = JSON.parse(row.configJson) as RankingForceClose; return value.tradeDate === tradeDate && value.source === "historical-ticks" ? [value] : []; }
    catch { return []; }
  });
}

export async function saveRankingForceClose(rows: RankingForceClose[]) {
  const db = getD1();
  for (const row of rows) memory.set(prefix(row.tradeDate) + row.ticker, row);
  if (!db || !rows.length) return;
  await db.batch(rows.map(row => db.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?) ON CONFLICT(config_key) DO UPDATE SET
    config_json=excluded.config_json, engine_version=excluded.engine_version, updated_at=excluded.updated_at`)
    .bind(prefix(row.tradeDate) + row.ticker, JSON.stringify(row), "ranking-force-close-v1", Date.now())));
}
