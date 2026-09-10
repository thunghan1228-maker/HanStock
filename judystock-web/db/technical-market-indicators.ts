export type StoredTechnicalMarketIndicator = {
  code: string;
  market: "twse" | "tpex";
  dataDate: string;
  updatedAt?: string | null;
  payload: Record<string, unknown>;
};

type StoredRow = {
  code: string;
  market: string;
  dataDate: string;
  payloadJson: string;
  updatedAt: number;
};

export type TechnicalMarketIndicatorMetadata = {
  latestDate: string;
  stockCount: number;
};

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

export async function saveTechnicalMarketIndicators(rows: StoredTechnicalMarketIndicator[]) {
  const d1 = getD1();
  if (!d1 || rows.length === 0) return false;
  const now = Date.now();
  for (let start = 0; start < rows.length; start += 20) {
    await d1.batch(rows.slice(start, start + 20).map((row) => d1.prepare(`INSERT INTO technical_market_stock_indicators
      (code, market, data_date, payload_json, updated_at) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(code) DO UPDATE SET market=excluded.market, data_date=excluded.data_date,
      payload_json=excluded.payload_json, updated_at=excluded.updated_at`)
      .bind(row.code, row.market, row.dataDate, JSON.stringify(row.payload), now)));
  }
  return true;
}

export async function readTechnicalMarketIndicators() {
  const d1 = getD1();
  if (!d1) return [] as StoredTechnicalMarketIndicator[];
  const result = await d1.prepare(`SELECT code, market, data_date AS dataDate, payload_json AS payloadJson, updated_at AS updatedAt
    FROM technical_market_stock_indicators ORDER BY code`).all<StoredRow>();
  return result.results.flatMap((row) => {
    try {
      const payload = JSON.parse(row.payloadJson) as Record<string, unknown>;
      return row.market === "twse" || row.market === "tpex"
        ? [{ code: row.code, market: row.market, dataDate: row.dataDate, updatedAt: Number(row.updatedAt) > 0 ? new Date(Number(row.updatedAt)).toISOString() : null, payload }]
        : [];
    } catch {
      return [];
    }
  });
}

export async function readTechnicalMarketIndicatorMetadata() {
  const d1 = getD1();
  if (!d1) return null as TechnicalMarketIndicatorMetadata | null;
  const row = await d1.prepare(`SELECT COALESCE(MAX(data_date), '') AS latestDate,
    COUNT(*) AS stockCount FROM technical_market_stock_indicators`).first<{ latestDate: string; stockCount: number }>();
  return row ? {
    latestDate: String(row.latestDate ?? ""),
    stockCount: Number(row.stockCount) || 0,
  } : null;
}
