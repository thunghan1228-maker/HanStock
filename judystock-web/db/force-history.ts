type ForceInterval = "1m" | "5m";

export type DailyForceRecord = {
  ticker: string;
  tradeDate: string;
  netVolume: number;
  barCount: number;
  sourceInterval: ForceInterval;
  lastBarAt: string;
  updatedAt: number;
};

export type IntradayForceRecord = {
  ticker: string;
  tradeDate: string;
  interval: ForceInterval;
  barTime: string;
  netVolume: number;
  buyAmount: number;
  sellAmount: number;
  netAmount: number;
  mainTickCount: number;
  updatedAt: number;
  observed?: number;
  amountsAvailable?: number;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
  return runtime.__HANSTOCK_DB ?? null;
}

async function ensureForceHistorySchema() {
  const d1 = getD1();
  if (!d1) throw new Error("daily_force_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS daily_force_totals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        net_volume INTEGER NOT NULL,
        bar_count INTEGER NOT NULL,
        source_interval TEXT NOT NULL,
        last_bar_at TEXT NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS daily_force_ticker_date_idx ON daily_force_totals (ticker, trade_date)"),
      d1.prepare(`CREATE TABLE IF NOT EXISTS intraday_force_bars (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        interval TEXT NOT NULL,
        bar_time TEXT NOT NULL,
        net_volume INTEGER NOT NULL,
        main_tick_count INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS intraday_force_ticker_interval_time_idx ON intraday_force_bars (ticker, interval, bar_time)"),
      d1.prepare(`CREATE TABLE IF NOT EXISTS intraday_force_bars_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        interval TEXT NOT NULL,
        bar_time TEXT NOT NULL,
        net_volume INTEGER NOT NULL,
        buy_amount REAL NOT NULL,
        sell_amount REAL NOT NULL,
        net_amount REAL NOT NULL,
        main_tick_count INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
      )`),
      d1.prepare("CREATE UNIQUE INDEX IF NOT EXISTS intraday_force_v2_ticker_interval_time_idx ON intraday_force_bars_v2 (ticker, interval, bar_time)"),
      d1.prepare(`CREATE TABLE IF NOT EXISTS intraday_force_refresh_state (
        ticker TEXT NOT NULL, interval TEXT NOT NULL, refreshed_at INTEGER NOT NULL, phase TEXT NOT NULL,
        PRIMARY KEY (ticker, interval)
      )`),
    ]).then(async () => {
      const columns = await d1.prepare("PRAGMA table_info(intraday_force_bars_v2)").all<{ name: string }>();
      const existing = new Set(columns.results.map(row => row.name));
      for (const column of ["observed", "amounts_available"]) {
        if (existing.has(column)) continue;
        try { await d1.prepare(`ALTER TABLE intraday_force_bars_v2 ADD COLUMN ${column} INTEGER NOT NULL DEFAULT 0`).run(); }
        catch (error) {
          // Another isolate can finish this additive migration concurrently.
          const check = await d1.prepare("PRAGMA table_info(intraday_force_bars_v2)").all<{ name: string }>();
          if (!check.results.some(row => row.name === column)) throw error;
        }
      }
    }).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

export async function saveIntradayForce(records: IntradayForceRecord[]) {
  if (records.length === 0) return;
  const d1 = await ensureForceHistorySchema();
  for (let start = 0; start < records.length; start += 80) {
    const chunk = records.slice(start, start + 80);
    await d1.batch(chunk.map((record) => d1.prepare(`INSERT INTO intraday_force_bars_v2
      (ticker, trade_date, interval, bar_time, net_volume, buy_amount, sell_amount, net_amount, main_tick_count, updated_at, observed, amounts_available)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(ticker, interval, bar_time) DO UPDATE SET
        trade_date = excluded.trade_date,
        net_volume = excluded.net_volume,
        buy_amount = excluded.buy_amount,
        sell_amount = excluded.sell_amount,
        net_amount = excluded.net_amount,
        main_tick_count = excluded.main_tick_count,
        updated_at = excluded.updated_at,
        observed = MAX(intraday_force_bars_v2.observed, excluded.observed),
        amounts_available = MAX(intraday_force_bars_v2.amounts_available, excluded.amounts_available)`)
      .bind(
        record.ticker,
        record.tradeDate,
        record.interval,
        record.barTime,
        Math.round(record.netVolume),
        Math.round(record.buyAmount),
        Math.round(record.sellAmount),
        Math.round(record.netAmount),
        Math.round(record.mainTickCount),
        record.updatedAt,
        record.observed ? 1 : 0,
        record.amountsAvailable ? 1 : 0,
      )));
  }
}

export async function saveDailyForce(record: DailyForceRecord) {
  const d1 = await ensureForceHistorySchema();
  await d1.prepare(`INSERT INTO daily_force_totals
    (ticker, trade_date, net_volume, bar_count, source_interval, last_bar_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ticker, trade_date) DO UPDATE SET
      net_volume = excluded.net_volume,
      bar_count = excluded.bar_count,
      source_interval = excluded.source_interval,
      last_bar_at = excluded.last_bar_at,
      updated_at = excluded.updated_at
    WHERE MIN(271, excluded.bar_count * CASE excluded.source_interval WHEN '5m' THEN 5 ELSE 1 END)
      >= MIN(271, daily_force_totals.bar_count * CASE daily_force_totals.source_interval WHEN '5m' THEN 5 ELSE 1 END)`)
    .bind(
      record.ticker,
      record.tradeDate,
      Math.round(record.netVolume),
      record.barCount,
      record.sourceInterval,
      record.lastBarAt,
      record.updatedAt,
    )
    .run();
}

export async function readIntradayForce(ticker: string, tradeDate: string, interval: ForceInterval) {
  const d1 = await ensureForceHistorySchema();
  const result = await d1.prepare(`SELECT
      ticker,
      trade_date AS tradeDate,
      interval,
      bar_time AS barTime,
      net_volume AS netVolume,
      buy_amount AS buyAmount,
      sell_amount AS sellAmount,
      net_amount AS netAmount,
      main_tick_count AS mainTickCount,
      updated_at AS updatedAt
    FROM intraday_force_bars_v2
    WHERE ticker = ? AND trade_date = ? AND interval = ?
    ORDER BY bar_time ASC`)
    .bind(ticker, tradeDate, interval)
    .all<IntradayForceRecord>();
  const legacy = await d1.prepare(`SELECT
      ticker,
      trade_date AS tradeDate,
      interval,
      bar_time AS barTime,
      net_volume AS netVolume,
      0 AS buyAmount,
      0 AS sellAmount,
      0 AS netAmount,
      main_tick_count AS mainTickCount,
      updated_at AS updatedAt
    FROM intraday_force_bars
    WHERE ticker = ? AND trade_date = ? AND interval = ?
    ORDER BY bar_time ASC`)
    .bind(ticker, tradeDate, interval)
    .all<IntradayForceRecord>();
  const merged = new Map(legacy.results.map(row => [row.barTime, row]));
  for (const row of result.results) {
    const previous = merged.get(row.barTime);
    if (row.netVolume !== 0 || row.buyAmount !== 0 || row.sellAmount !== 0 || row.mainTickCount > 0 || !previous) merged.set(row.barTime, row);
  }
  return [...merged.values()].sort((a, b) => a.barTime.localeCompare(b.barTime));
}

/** Read saved dates independently of the upstream response, which can be empty. */
export async function readIntradayForceDates(ticker: string, interval: ForceInterval) {
  const d1 = await ensureForceHistorySchema();
  const result = await d1.prepare(`SELECT trade_date AS tradeDate FROM intraday_force_bars_v2
    WHERE ticker = ? AND interval = ?
    UNION SELECT trade_date AS tradeDate FROM intraday_force_bars WHERE ticker = ? AND interval = ?
    ORDER BY tradeDate DESC LIMIT 31`).bind(ticker, interval, ticker, interval).all<{ tradeDate: string }>();
  return result.results.map(row => row.tradeDate);
}

/** One read for all saved sessions, instead of two sequential reads per day. */
export async function readIntradayForceHistory(ticker: string, interval: ForceInterval) {
  const d1 = await ensureForceHistorySchema();
  const result = await d1.prepare(`WITH dates AS (
      SELECT trade_date FROM intraday_force_bars_v2 WHERE ticker = ? AND interval = ?
      UNION SELECT trade_date FROM intraday_force_bars WHERE ticker = ? AND interval = ?
      ORDER BY trade_date DESC LIMIT 31
    ), history AS (
      SELECT ticker, trade_date AS tradeDate, interval, bar_time AS barTime, net_volume AS netVolume,
        buy_amount AS buyAmount, sell_amount AS sellAmount, net_amount AS netAmount,
        main_tick_count AS mainTickCount, updated_at AS updatedAt, observed, amounts_available AS amountsAvailable, 2 AS version
      FROM intraday_force_bars_v2 WHERE ticker = ? AND interval = ? AND trade_date IN (SELECT trade_date FROM dates)
      UNION ALL
      SELECT ticker, trade_date AS tradeDate, interval, bar_time AS barTime, net_volume AS netVolume,
        0 AS buyAmount, 0 AS sellAmount, 0 AS netAmount,
        main_tick_count AS mainTickCount, updated_at AS updatedAt, 0 AS observed, 0 AS amountsAvailable, 1 AS version
      FROM intraday_force_bars WHERE ticker = ? AND interval = ? AND trade_date IN (SELECT trade_date FROM dates)
    ) SELECT * FROM history ORDER BY barTime ASC, version ASC`)
    .bind(ticker, interval, ticker, interval, ticker, interval, ticker, interval).all<IntradayForceRecord>();
  const merged = new Map<string, IntradayForceRecord>();
  for (const row of result.results) {
    if (row.observed === 1 || row.netVolume !== 0 || row.buyAmount !== 0 || row.sellAmount !== 0 || row.mainTickCount > 0 || !merged.has(row.barTime)) merged.set(row.barTime, row);
  }
  return [...merged.values()];
}

export async function readForceRefreshState(ticker: string, interval: ForceInterval) {
  const d1 = await ensureForceHistorySchema();
  return d1.prepare("SELECT refreshed_at AS refreshedAt, phase FROM intraday_force_refresh_state WHERE ticker = ? AND interval = ?")
    .bind(ticker, interval).first<{ refreshedAt: number; phase: string }>();
}

export async function saveForceRefreshState(ticker: string, interval: ForceInterval, refreshedAt: number, phase: string) {
  const d1 = await ensureForceHistorySchema();
  await d1.prepare(`INSERT INTO intraday_force_refresh_state (ticker, interval, refreshed_at, phase) VALUES (?, ?, ?, ?)
    ON CONFLICT(ticker, interval) DO UPDATE SET refreshed_at = excluded.refreshed_at, phase = excluded.phase`)
    .bind(ticker, interval, refreshedAt, phase).run();
}

export async function readIntradayForceForTickers(tickers: string[], tradeDate: string, interval: ForceInterval) {
  const uniqueTickers = [...new Set(tickers.map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))];
  if (uniqueTickers.length === 0) return [];
  const d1 = await ensureForceHistorySchema();
  const records: IntradayForceRecord[] = [];
  for (let start = 0; start < uniqueTickers.length; start += 80) {
    const chunk = uniqueTickers.slice(start, start + 80);
    const result = await d1.prepare(`SELECT
        ticker,
        trade_date AS tradeDate,
        interval,
        bar_time AS barTime,
        net_volume AS netVolume,
        buy_amount AS buyAmount,
        sell_amount AS sellAmount,
        net_amount AS netAmount,
        main_tick_count AS mainTickCount,
        updated_at AS updatedAt
      FROM intraday_force_bars_v2
      WHERE trade_date = ? AND interval = ? AND ticker IN (${chunk.map(() => "?").join(", ")})
      ORDER BY ticker ASC, bar_time ASC`)
      .bind(tradeDate, interval, ...chunk)
      .all<IntradayForceRecord>();
    records.push(...result.results);
  }
  return records;
}

export async function readDailyForce(ticker: string, limit = 90) {
  const d1 = await ensureForceHistorySchema();
  const result = await d1.prepare(`SELECT
      ticker,
      trade_date AS tradeDate,
      net_volume AS netVolume,
      bar_count AS barCount,
      source_interval AS sourceInterval,
      last_bar_at AS lastBarAt,
      updated_at AS updatedAt
    FROM daily_force_totals
    WHERE ticker = ?
    ORDER BY trade_date DESC
    LIMIT ?`)
    .bind(ticker, Math.max(1, Math.min(260, Math.floor(limit))))
    .all<DailyForceRecord>();
  return [...result.results].reverse();
}
