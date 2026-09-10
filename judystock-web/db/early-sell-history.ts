export type EarlySellSignalRecord = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "daytradeEarlySell50" | "daytradeEarlyBuy50" | "intradayExtraLargeSell" | "intradayExtraLargeBuy" | "intradayLargeForceBuy" | "intradayLargeForceSell" | "instantLargeSell" | "instantLargeBuy" | "fourGateBullish" | "fourGateBearish" | "mainForceTurnBullish" | "mainForceStrongBullish" | "mainForceTurnBearish" | "mainForceStrongBearish" | "triangleNearBreakout" | "triangleBreakoutPendingVolume" | "triangleVolumeBreakout" | "fiveMinuteTwelveShort" | "fiveMinuteOnePlusTwoLong";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

let schemaReady: Promise<void> | null = null;

const MAIN_FORCE_KINDS = [
  "mainForceTurnBullish",
  "mainForceStrongBullish",
  "mainForceTurnBearish",
  "mainForceStrongBearish",
] as const;

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("early_sell_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS early_sell_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        label TEXT NOT NULL,
        bar_ts INTEGER NOT NULL,
        price REAL NOT NULL,
        note TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS early_sell_signal_unique_idx ON early_sell_signals (trade_date, ticker, kind, bar_ts)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS early_sell_signal_date_time_idx ON early_sell_signals (trade_date, bar_ts)"),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveEarlySellSignals(records: EarlySellSignalRecord[]) {
  if (records.length === 0) return;
  const d1 = await database();
  const now = Date.now();
  for (let start = 0; start < records.length; start += 60) {
    await d1.batch(records.slice(start, start + 60).map((record) => d1.prepare(`INSERT INTO early_sell_signals
      (trade_date, ticker, name, kind, label, bar_ts, price, note, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(trade_date, ticker, kind, bar_ts) DO UPDATE SET
        name = excluded.name,
        label = excluded.label,
        price = excluded.price,
        note = CASE
          WHEN early_sell_signals.note LIKE '%｜族群同步 %'
            AND excluded.note NOT LIKE '%｜族群同步 %'
          THEN early_sell_signals.note
          WHEN (CAST(excluded.bar_ts / 60000 AS INTEGER) + 480) % 1440 = 870
            AND excluded.kind IN ('instantLargeBuy', 'instantLargeSell')
            AND early_sell_signals.price = excluded.price
            AND instr(early_sell_signals.note, '｜盤後大戶力 ') > 0
            AND instr(excluded.note, '盤後大戶力 ') = 0
          THEN excluded.note
            || CASE WHEN instr(excluded.note, '14:30 盤後定價成交') = 0 THEN '｜14:30 盤後定價成交' ELSE '' END
            || substr(early_sell_signals.note, instr(early_sell_signals.note, '｜盤後大戶力 '),
              instr(substr(early_sell_signals.note, instr(early_sell_signals.note, '｜盤後大戶力 ')), '%'))
          ELSE excluded.note
        END,
        updated_at = excluded.updated_at`)
      .bind(
        record.tradeDate,
        record.ticker,
        record.name,
        record.kind,
        record.label,
        Math.trunc(record.barTs),
        record.price,
        record.note,
        now,
        now,
      )));
  }
}

export async function appendEarlySellChipRankAnnotations(records: EarlySellSignalRecord[]) {
  if (records.length === 0) return;
  const d1 = await database();
  const now = Date.now();
  for (let start = 0; start < records.length; start += 60) {
    await d1.batch(records.slice(start, start + 60).map((record) => d1.prepare(`UPDATE early_sell_signals
      SET note = ?, updated_at = ?
      WHERE trade_date = ? AND ticker = ? AND kind = ? AND bar_ts = ?`)
      .bind(record.note, now, record.tradeDate, record.ticker, record.kind, Math.trunc(record.barTs))));
  }
}

export async function deleteEarlySellSignalsForTickers(tradeDate: string, tickers: string[]) {
  // 盤中訊號屬於永久歷史紀錄；即使個股之後不再符合即時濾網，也不回頭刪除已發出的訊號。
  void tradeDate;
  void tickers;
}

export async function deleteEarlySellSignalsForKindTickers(
  tradeDate: string,
  kind: EarlySellSignalRecord["kind"],
  tickers: string[],
) {
  const uniqueTickers = [...new Set(tickers.map((ticker) => ticker.trim()).filter(Boolean))];
  if (!tradeDate || uniqueTickers.length === 0) return;
  const d1 = await database();
  for (let start = 0; start < uniqueTickers.length; start += 60) {
    const batch = uniqueTickers.slice(start, start + 60);
    await d1.prepare(`DELETE FROM early_sell_signals
      WHERE trade_date = ? AND kind = ? AND ticker IN (${batch.map(() => "?").join(", ")})`)
      .bind(tradeDate, kind, ...batch)
      .run();
  }
}

export async function readEarlySellSignals(options: { tradeDate?: string; query?: string; limit?: number; kinds?: EarlySellSignalRecord["kind"][]; noteIncludes?: string; minuteOfDay?: number } = {}) {
  const d1 = await database();
  const conditions: string[] = [];
  const bindings: Array<string | number> = [];
  if (options.tradeDate) {
    conditions.push("trade_date = ?");
    bindings.push(options.tradeDate);
  }
  const query = options.query?.trim();
  if (query) {
    conditions.push("(ticker LIKE ? OR name LIKE ?)");
    const pattern = `%${query}%`;
    bindings.push(pattern, pattern);
  }
  if (options.kinds?.length) {
    conditions.push(`kind IN (${options.kinds.map(() => "?").join(", ")})`);
    bindings.push(...options.kinds);
  }
  if (options.noteIncludes) {
    conditions.push("note LIKE ?");
    bindings.push(`%${options.noteIncludes}%`);
  }
  if (Number.isInteger(options.minuteOfDay) && options.minuteOfDay! >= 0 && options.minuteOfDay! < 1440) {
    conditions.push("(CAST(bar_ts / 60000 AS INTEGER) + 480) % 1440 = ?");
    bindings.push(options.minuteOfDay!);
  }
  // 活躍盤勢的族群瞬間大單可能超過 2,000 筆；讀取層不可先截斷，
  // 否則早盤已永久保存的訊號永遠無法回到畫面。
  const limit = Math.max(1, Math.min(10_000, Math.trunc(options.limit ?? 500)));
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
  const result = await d1.prepare(`SELECT
      trade_date AS tradeDate,
      ticker,
      name,
      kind,
      label,
      bar_ts AS barTs,
      price,
      note
    FROM early_sell_signals
    ${where}
    ORDER BY bar_ts DESC, ticker ASC
    LIMIT ?`)
    .bind(...bindings, limit)
    .all<EarlySellSignalRecord>();
  return result.results;
}

export async function readEarlySellSignalSummary(tradeDate: string, options: { mainForceNoteIncludes?: string } = {}) {
  const d1 = await database();
  const markerPattern = options.mainForceNoteIncludes ? `%${options.mainForceNoteIncludes}%` : null;
  const mainForceFilter = markerPattern
    ? `AND (kind NOT IN (${MAIN_FORCE_KINDS.map(() => "?").join(", ")}) OR note LIKE ?)`
    : "";
  const mainForceBindings = markerPattern ? [...MAIN_FORCE_KINDS, markerPattern] : [];
  const result = await d1.prepare(`SELECT
      kind,
      COUNT(*) AS eventCount,
      COUNT(DISTINCT ticker) AS stockCount
    FROM early_sell_signals
    WHERE trade_date = ?
      ${mainForceFilter}
    GROUP BY kind
    ORDER BY kind ASC`)
    .bind(tradeDate, ...mainForceBindings)
    .all<{ kind: EarlySellSignalRecord["kind"]; eventCount: number; stockCount: number }>();
  const byKind = Object.fromEntries(result.results.map((row) => [row.kind, {
    events: Number(row.eventCount),
    stocks: Number(row.stockCount),
  }]));
  const unique = await d1.prepare(`SELECT COUNT(DISTINCT ticker) AS stockCount
    FROM early_sell_signals WHERE trade_date = ? ${mainForceFilter}`)
    .bind(tradeDate, ...mainForceBindings)
    .first<{ stockCount: number }>();
  return {
    total: result.results.reduce((sum, row) => sum + Number(row.eventCount), 0),
    uniqueStocks: Number(unique?.stockCount ?? 0),
    byKind,
  };
}

export async function readEarlySellDates(limit = 90) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT trade_date AS tradeDate
    FROM early_sell_signals
    GROUP BY trade_date
    ORDER BY trade_date DESC
    LIMIT ?`)
    .bind(Math.max(1, Math.min(365, Math.trunc(limit))))
    .all<{ tradeDate: string }>();
  return result.results.map((row) => row.tradeDate);
}
