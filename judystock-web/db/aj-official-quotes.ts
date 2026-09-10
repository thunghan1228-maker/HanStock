import recoveredHistory from "../lib/data/aj-official-history-20260907.json";

export type AjOfficialQuote = { code: string; changePct: number };

function db() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB;
}

export async function readAjOfficialQuotes(tradeDate: string): Promise<AjOfficialQuote[] | null> {
  const row = await db()?.prepare("SELECT config_json AS json FROM river_radar_config WHERE config_key = ?")
    .bind(`aj-official-quotes-v1:${tradeDate}`).first<{ json: string }>();
  if (!row) {
    // Verified TWSE + TPEx reports recovered during the September 7 outage.
    // These are dated historical quotes, never a substitute for a later day.
    const recovered = (recoveredHistory as Record<string, { tradeDate: string; rows: AjOfficialQuote[] }>)[tradeDate];
    if (!recovered || recovered.tradeDate !== tradeDate) return null;
    await saveAjOfficialQuotes(tradeDate, recovered.rows).catch(() => undefined);
    return recovered.rows;
  }
  try {
    const data = JSON.parse(row.json);
    if (data.tradeDate !== tradeDate || !Array.isArray(data.rows) || data.rows.length < 500) return null;
    return data.rows.every((q: AjOfficialQuote) => /^\d{4}$/.test(q.code) && Number.isFinite(q.changePct)) ? data.rows : null;
  } catch { return null; }
}

export async function saveAjOfficialQuotes(tradeDate: string, rows: AjOfficialQuote[]) {
  if (rows.length < 500) return;
  await db()?.prepare(`INSERT INTO river_radar_config (config_key, config_json, engine_version, updated_at)
    VALUES (?, ?, ?, ?) ON CONFLICT(config_key) DO UPDATE SET
    config_json=excluded.config_json, engine_version=excluded.engine_version, updated_at=excluded.updated_at`)
    .bind(`aj-official-quotes-v1:${tradeDate}`, JSON.stringify({ tradeDate, rows }), "aj-official-quotes-v1", Date.now()).run();
}
