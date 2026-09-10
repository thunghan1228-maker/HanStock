type ArchiveItem = {
  key: string;
  code: string | null;
  name: string;
  groupName: string;
  market: string;
  score: number;
  increaseRank: number | null;
  decreaseRank: number | null;
};

export type WeeklyChipArchive = {
  weekEndDate: string;
  comparedWeekEndDate: string | null;
  weights: Record<string, number>;
  stocks: ArchiveItem[];
  groups: ArchiveItem[];
};

export type WeeklyChipArchiveSummary = {
  weekEndDate: string;
  comparedWeekEndDate: string | null;
  stockIncreaseAverage: number | null;
  stockDecreaseAverage: number | null;
  strongestStock: string;
  weakestStock: string;
  strongestGroup: string;
  weakestGroup: string;
  stockCount: number;
  groupCount: number;
  savedAt: number;
};

type D1Result<T> = { results?: T[] };
type D1Statement = {
  bind(...values: unknown[]): D1Statement;
  run(): Promise<unknown>;
  first<T>(): Promise<T | null>;
  all<T>(): Promise<D1Result<T>>;
};
type HanstockD1 = {
  prepare(query: string): D1Statement;
  batch(statements: D1Statement[]): Promise<unknown>;
};

let schemaReady: Promise<void> | null = null;

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: HanstockD1 }).__HANSTOCK_DB ?? null;
}

async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("weekly_chip_storage_unavailable");
  if (!schemaReady) {
    schemaReady = d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS weekly_chip_history (
        week_end_date TEXT PRIMARY KEY,
        compared_week_end_date TEXT,
        payload_gzip_base64 TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        saved_at INTEGER NOT NULL
      )`),
      d1.prepare(`CREATE TABLE IF NOT EXISTS weekly_chip_prices (
        week_end_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        close_price REAL NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (week_end_date, ticker)
      )`),
      d1.prepare("CREATE INDEX IF NOT EXISTS weekly_chip_history_saved_idx ON weekly_chip_history (saved_at DESC)"),
      d1.prepare("CREATE INDEX IF NOT EXISTS weekly_chip_prices_ticker_idx ON weekly_chip_prices (ticker, week_end_date DESC)"),
    ]).then(() => undefined).catch((error) => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return d1;
}

function round1(value: number) {
  return Math.round(value * 10) / 10;
}

function average(items: ArchiveItem[]) {
  return items.length ? round1(items.reduce((sum, item) => sum + item.score, 0) / items.length) : null;
}

function top(items: ArchiveItem[], direction: "increase" | "decrease") {
  const rankKey = direction === "increase" ? "increaseRank" : "decreaseRank";
  return items
    .filter((item) => item[rankKey] !== null && Number(item[rankKey]) <= 20)
    .sort((a, b) => Number(a[rankKey]) - Number(b[rankKey]));
}

function summarize(archive: WeeklyChipArchive, savedAt: number): WeeklyChipArchiveSummary {
  const stockIncrease = top(archive.stocks, "increase");
  const stockDecrease = top(archive.stocks, "decrease");
  const groupIncrease = top(archive.groups, "increase");
  const groupDecrease = top(archive.groups, "decrease");
  return {
    weekEndDate: archive.weekEndDate,
    comparedWeekEndDate: archive.comparedWeekEndDate,
    stockIncreaseAverage: average(stockIncrease),
    stockDecreaseAverage: average(stockDecrease),
    strongestStock: stockIncrease[0] ? `${stockIncrease[0].code ?? ""} ${stockIncrease[0].name}`.trim() : "—",
    weakestStock: stockDecrease[0] ? `${stockDecrease[0].code ?? ""} ${stockDecrease[0].name}`.trim() : "—",
    strongestGroup: groupIncrease[0]?.name ?? "—",
    weakestGroup: groupDecrease[0]?.name ?? "—",
    stockCount: archive.stocks.length,
    groupCount: archive.groups.length,
    savedAt,
  };
}

function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  for (let start = 0; start < bytes.length; start += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(value: string) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

async function gzipJson(value: unknown) {
  const stream = new Blob([JSON.stringify(value)]).stream().pipeThrough(new CompressionStream("gzip"));
  return bytesToBase64(new Uint8Array(await new Response(stream).arrayBuffer()));
}

async function ungzipJson<T>(value: string) {
  const stream = new Blob([base64ToBytes(value)]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text()) as T;
}

export async function saveWeeklyChipArchives(archives: WeeklyChipArchive[]) {
  const d1 = await database();
  const savedAt = Date.now();
  const statements: D1Statement[] = [];
  for (const archive of archives) {
    const payload = await gzipJson(archive);
    const summary = summarize(archive, savedAt);
    statements.push(d1.prepare(`INSERT INTO weekly_chip_history
      (week_end_date, compared_week_end_date, payload_gzip_base64, summary_json, saved_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(week_end_date) DO UPDATE SET
        compared_week_end_date=excluded.compared_week_end_date,
        payload_gzip_base64=excluded.payload_gzip_base64,
        summary_json=excluded.summary_json,
        saved_at=excluded.saved_at`)
      .bind(archive.weekEndDate, archive.comparedWeekEndDate, payload, JSON.stringify(summary), savedAt));
  }
  if (statements.length) await d1.batch(statements);
  return listWeeklyChipArchiveSummaries();
}

export async function listWeeklyChipArchiveSummaries(limit = 156) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT summary_json AS summary
    FROM weekly_chip_history ORDER BY week_end_date DESC LIMIT ?`)
    .bind(Math.max(1, Math.min(520, limit)))
    .all<{ summary: string }>();
  return (result.results ?? []).flatMap((row) => {
    try { return [JSON.parse(row.summary) as WeeklyChipArchiveSummary]; } catch { return []; }
  });
}

export async function listWeeklyChipArchives(limit = 3) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT week_end_date AS weekEndDate, payload_gzip_base64 AS payload
    FROM weekly_chip_history ORDER BY week_end_date DESC LIMIT ?`)
    .bind(Math.max(1, Math.min(12, limit)))
    .all<{ weekEndDate: string; payload: string }>();
  const archives: WeeklyChipArchive[] = [];
  for (const row of result.results ?? []) {
    try {
      const archive = await ungzipJson<WeeklyChipArchive>(row.payload);
      if (archive.weekEndDate === row.weekEndDate) archives.push(archive);
    } catch {
      // One damaged legacy archive must not hide the other permanently saved weeks.
    }
  }
  return archives;
}

export async function readWeeklyChipTickerHistory(ticker: string, limit = 156) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT week_end_date AS weekEndDate, payload_gzip_base64 AS payload
    FROM weekly_chip_history ORDER BY week_end_date DESC LIMIT ?`)
    .bind(Math.max(1, Math.min(520, limit)))
    .all<{ weekEndDate: string; payload: string }>();
  const history: Array<{ weekEndDate: string; score: number; increaseRank: number | null; decreaseRank: number | null }> = [];
  for (const row of result.results ?? []) {
    try {
      const archive = await ungzipJson<WeeklyChipArchive>(row.payload);
      const stock = archive.stocks.find((item) => item.code === ticker);
      if (stock) history.push({ weekEndDate: row.weekEndDate, score: stock.score, increaseRank: stock.increaseRank, decreaseRank: stock.decreaseRank });
    } catch {
      // Keep other saved weeks visible even if one legacy payload is unreadable.
    }
  }
  return history;
}

export async function readWeeklyChipPrices(tickers: string[], weekDates: string[]) {
  const d1 = await database();
  const uniqueTickers = [...new Set(tickers)].slice(0, 80);
  const uniqueDates = [...new Set(weekDates)].slice(0, 8);
  if (!uniqueTickers.length || !uniqueDates.length) return new Map<string, number>();
  const tickerMarks = uniqueTickers.map(() => "?").join(",");
  const dateMarks = uniqueDates.map(() => "?").join(",");
  const result = await d1.prepare(`SELECT week_end_date AS weekEndDate, ticker, close_price AS closePrice
    FROM weekly_chip_prices WHERE ticker IN (${tickerMarks}) AND week_end_date IN (${dateMarks})`)
    .bind(...uniqueTickers, ...uniqueDates)
    .all<{ weekEndDate: string; ticker: string; closePrice: number }>();
  return new Map((result.results ?? []).map((row) => [`${row.weekEndDate}:${row.ticker}`, Number(row.closePrice)]));
}

export async function saveWeeklyChipPrices(rows: Array<{ weekEndDate: string; ticker: string; closePrice: number }>) {
  const d1 = await database();
  const updatedAt = Date.now();
  const valid = rows.filter((row) => /^\d{4}\/\d{2}\/\d{2}$/.test(row.weekEndDate) && /^[0-9A-Z]{4,7}$/.test(row.ticker) && Number.isFinite(row.closePrice) && row.closePrice > 0);
  if (!valid.length) return;
  await d1.batch(valid.map((row) => d1.prepare(`INSERT INTO weekly_chip_prices
    (week_end_date, ticker, close_price, updated_at) VALUES (?, ?, ?, ?)
    ON CONFLICT(week_end_date, ticker) DO UPDATE SET close_price=excluded.close_price, updated_at=excluded.updated_at`)
    .bind(row.weekEndDate, row.ticker, row.closePrice, updatedAt)));
}
