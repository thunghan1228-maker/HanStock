import { isPreopenTrialWindow, loadPreopenTrialQuotes, taipeiMarketClock } from "../../../lib/preopen-trial";

type Direction = "strong" | "weak";
type StockMember = [string, string];
type GroupMap = Record<string, StockMember[]>;
type ConfiguredGroup = {
  name?: string;
  members?: Array<{ code?: string; name?: string }>;
};
type QuoteRow = {
  code?: string;
  price?: number | null;
  prevClose?: number | null;
  change?: number | null;
  changePct?: number | null;
  session?: "preopen-trial";
};
type TrpcPayload<T> = { result?: { data?: { json?: T } } };

const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF"]);
const HANSTOCK_GROUP_TOTAL = 67;

function buildStockCatalog(groups: GroupMap) {
  const stocks = new Map<string, { code: string; name: string; groups: string[] }>();
  Object.entries(groups).forEach(([group, members]) => {
    if (EXCLUDED_GROUPS.has(group)) return;
    members.forEach(([code, rawName]) => {
      if (!/^\d{4}$/.test(code) || code.startsWith("00")) return;
      const current = stocks.get(code);
      if (current) {
        if (!current.groups.includes(group)) current.groups.push(group);
      } else {
        stocks.set(code, { code, name: rawName.replace(/\*$/, ""), groups: [group] });
      }
    });
  });
  return stocks;
}

async function loadConfiguredUniverse() {
  const response = await fetch("https://hanstock-battle-minimal.thunghan8.chatgpt.site/api/trpc/stocks.groups", {
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Ranking/1.0" },
    next: { revalidate: 60 },
    signal: AbortSignal.timeout(18_000),
  });
  if (!response.ok) throw new Error(`configured-groups-${response.status}`);
  const payload = await response.json() as TrpcPayload<ConfiguredGroup[]>;
  const configured = payload.result?.data?.json;
  if (!Array.isArray(configured) || configured.length < 60) throw new Error("configured-groups-malformed");

  const groups: GroupMap = {};
  for (const row of configured) {
    const group = row.name?.trim();
    if (!group || EXCLUDED_GROUPS.has(group) || !Array.isArray(row.members)) continue;
    const members = row.members
      .map((member): StockMember => [String(member.code ?? "").trim(), String(member.name ?? "").trim()])
      .filter(([code]) => /^\d{4}$/.test(code) && !code.startsWith("00"));
    if (members.length > 0) groups[group] = members;
  }
  const stockCatalog = buildStockCatalog(groups);
  if (Object.keys(groups).length !== HANSTOCK_GROUP_TOTAL || stockCatalog.size < 300) {
    throw new Error(`configured-universe-not-68-${Object.keys(groups).length}`);
  }
  return { groups, stockCatalog };
}

function readText(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" || typeof value === "number") return String(value).trim();
  }
  return "";
}

async function refreshGroupMemberNames(stockCatalog: Map<string, { code: string; name: string; groups: string[] }>) {
  const sources = await Promise.allSettled([
    fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", {
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Ranking/1.0" },
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(12_000),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`twse-catalog-${response.status}`);
      return await response.json() as Record<string, unknown>[];
    }),
    fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", {
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Ranking/1.0" },
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(12_000),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`tpex-catalog-${response.status}`);
      return await response.json() as Record<string, unknown>[];
    }),
  ]);

  for (const source of sources) {
    if (source.status !== "fulfilled" || !Array.isArray(source.value)) continue;
    for (const row of source.value) {
      const code = readText(row, "Code", "SecuritiesCompanyCode").toUpperCase();
      const name = readText(row, "Name", "CompanyName", "SecuritiesCompanyName").replace(/\*$/, "");
      if (!/^[1-9]\d{3}$/.test(code) || !name) continue;
      const existing = stockCatalog.get(code);
      if (existing) existing.name = existing.name || name;
    }
  }
}

function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replace(/,/g, "").replace(/[^0-9.+-]/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function taipeiNowParts() {
  const shifted = new Date(Date.now() + 8 * 60 * 60 * 1000);
  return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth(), day: shifted.getUTCDate(), weekday: shifted.getUTCDay(), minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes() };
}

function shouldUseOfficialClose() {
  const now = taipeiNowParts();
  return now.weekday === 0 || now.weekday === 6 || now.minutes < 9 * 60 || now.minutes > 13 * 60 + 35;
}

function dateKey(date: Date) {
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

async function loadOfficialQuoteDay(day: Date) {
  const compact = dateKey(day);
  const slash = `${compact.slice(0, 4)}/${compact.slice(4, 6)}/${compact.slice(6, 8)}`;
  const [twseResult, tpexResult] = await Promise.allSettled([
    fetch(`https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${compact}&type=ALLBUT0999&response=json`, { cache: "no-store", headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Ranking/2.1" }, signal: AbortSignal.timeout(20_000) }).then((response) => response.ok ? response.json() : Promise.reject(new Error(`twse-${response.status}`))),
    fetch(`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=${encodeURIComponent(slash)}&id=&response=json`, { cache: "no-store", headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Ranking/2.1" }, signal: AbortSignal.timeout(20_000) }).then((response) => response.ok ? response.json() : Promise.reject(new Error(`tpex-${response.status}`))),
  ]);
  const rows: Array<QuoteRow & { code: string; changePct: number }> = [];
  if (twseResult.status === "fulfilled") {
    const tables = Array.isArray(twseResult.value?.tables) ? twseResult.value.tables : [];
    for (const row of tables.flatMap((table: { data?: unknown[][] }) => Array.isArray(table.data) ? table.data : [])) {
      const code = String(row?.[0] ?? "").trim();
      const price = numberValue(row?.[8]);
      const rawChange = numberValue(row?.[10]);
      const change = rawChange === null ? null : String(row?.[9] ?? "").includes("-") ? -Math.abs(rawChange) : rawChange;
      if (!/^\d{4}$/.test(code) || price === null || change === null || price - change <= 0) continue;
      rows.push({ code, price, change, prevClose: price - change, changePct: change / (price - change) * 100 });
    }
  }
  if (tpexResult.status === "fulfilled") {
    const tables = Array.isArray(tpexResult.value?.tables) ? tpexResult.value.tables : [];
    for (const row of tables.flatMap((table: { data?: unknown[][] }) => Array.isArray(table.data) ? table.data : [])) {
      const code = String(row?.[0] ?? "").trim();
      const price = numberValue(row?.[2]);
      const change = numberValue(row?.[3]);
      if (!/^\d{4}$/.test(code) || price === null || change === null || price - change <= 0) continue;
      rows.push({ code, price, change, prevClose: price - change, changePct: change / (price - change) * 100 });
    }
  }
  return { compact, rows };
}

async function loadOfficialQuotes(stockCatalog: Map<string, { code: string; name: string; groups: string[] }>) {
  const now = taipeiNowParts();
  const start = new Date(Date.UTC(now.year, now.month, now.day));
  if (now.minutes < 14 * 60 + 30) start.setUTCDate(start.getUTCDate() - 1);
  for (let offset = 0; offset < 10; offset += 1) {
    const candidate = new Date(start);
    candidate.setUTCDate(start.getUTCDate() - offset);
    if (candidate.getUTCDay() === 0 || candidate.getUTCDay() === 6) continue;
    const result = await loadOfficialQuoteDay(candidate);
    const rows = result.rows.filter((row) => stockCatalog.has(row.code));
    if (rows.length >= Math.min(400, Math.floor(stockCatalog.size * 0.6))) {
      return { rows, fetchedAt: new Date().toISOString(), priceType: "收盤價", sourceDate: result.compact };
    }
  }
  throw new Error("official-close-unavailable");
}

async function fetchQuoteBatch(tickers: string[]) {
  const input = encodeURIComponent(JSON.stringify({ json: { tickers } }));
  const response = await fetch(`https://hanstock-battle-minimal.thunghan8.chatgpt.site/api/trpc/stocks.liveQuotes?input=${input}`, {
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Ranking/1.0" },
    next: { revalidate: 15 },
    signal: AbortSignal.timeout(18_000),
  });
  if (!response.ok) throw new Error(`live-quotes-${response.status}`);
  const payload = await response.json() as TrpcPayload<{ fetchedAt?: string; priceType?: string; rows?: QuoteRow[] }>;
  const data = payload.result?.data?.json;
  if (!data || !Array.isArray(data.rows)) throw new Error("live-quotes-malformed");
  return data;
}

async function fetchQuoteBatchWithRetry(tickers: string[]) {
  try {
    return await fetchQuoteBatch(tickers);
  } catch {
    return fetchQuoteBatch(tickers);
  }
}

async function loadQuotes(stockCatalog: Map<string, { code: string; name: string; groups: string[] }>) {
  // Ranking candidates must stay inside HanStock's configured group members.
  // The official market catalogs only refresh names; they must never expand
  // this sample to the full TWSE/TPEx market or create an "其他" group.
  await refreshGroupMemberNames(stockCatalog);
  if (isPreopenTrialWindow()) {
    const trial = await loadPreopenTrialQuotes([...stockCatalog.keys()]);
    const clock = taipeiMarketClock();
    return {
      rows: trial.quotes.filter((row) => stockCatalog.has(row.code)),
      fetchedAt: new Date().toISOString(),
      priceType: "盤前試撮",
      sourceDate: `${clock.date.slice(0, 4)}/${clock.date.slice(4, 6)}/${clock.date.slice(6, 8)}`,
    };
  }
  if (shouldUseOfficialClose()) return loadOfficialQuotes(stockCatalog);
  const codes = [...stockCatalog.keys()];
  const batches = Array.from({ length: Math.ceil(codes.length / 50) }, (_, index) => codes.slice(index * 50, index * 50 + 50));
  const settled = await Promise.allSettled(batches.map(fetchQuoteBatchWithRetry));
  const fulfilled = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const rows = fulfilled.flatMap((data) => data.rows ?? []).filter((row): row is QuoteRow & { code: string; changePct: number } =>
    Boolean(row.code && stockCatalog.has(row.code) && typeof row.changePct === "number" && Number.isFinite(row.changePct)),
  );
  if (rows.length < Math.min(500, Math.floor(stockCatalog.size * 0.75))) throw new Error(`quote-coverage-${rows.length}`);
  return {
    rows,
    fetchedAt: fulfilled.map((data) => data.fetchedAt).filter((value): value is string => Boolean(value)).sort().at(-1) ?? new Date().toISOString(),
    priceType: fulfilled.some((data) => data.priceType === "即時價") ? "即時價" : "收盤價",
    sourceDate: undefined,
  };
}

function stockRows(
  quotes: Array<QuoteRow & { code: string; changePct: number }>,
  direction: Direction,
  stockCatalog: Map<string, { code: string; name: string; groups: string[] }>,
  trial = false,
) {
  return [...quotes]
    .filter((quote) => {
      const stock = stockCatalog.get(quote.code);
      return Boolean(stock && stock.groups.length > 0 && !stock.groups.includes("其他"));
    })
    .sort((a, b) => direction === "strong" ? b.changePct - a.changePct : a.changePct - b.changePct)
    .slice(0, 20)
    .map((quote, index) => {
      const stock = stockCatalog.get(quote.code)!;
      const magnitude = Math.min(100, Math.max(1, Math.round(50 + quote.changePct * 5)));
      return {
        rank: index + 1,
        name: `${quote.code} ${stock.name}`,
        change: signedPercent(quote.changePct),
        score: magnitude,
        lead: stock.groups[0] ?? "未分類",
        leadChange: `${trial ? "試撮" : "即時"}漲跌幅第 ${index + 1} 名`,
      };
    });
}

function groupRows(
  quoteByCode: Map<string, QuoteRow & { code: string; changePct: number }>,
  direction: Direction,
  allGroups: GroupMap,
  trial = false,
  limit = 20,
) {
  return Object.entries(allGroups)
    .flatMap(([group, members]) => {
      const quoted = members
        .map(([code, rawName]) => ({ code, name: rawName.replace(/\*$/, ""), quote: quoteByCode.get(code) }))
        .filter((item): item is { code: string; name: string; quote: QuoteRow & { code: string; changePct: number } } => Boolean(item.quote));
      if (quoted.length < 3) return [];
      return [{ group, avgChange: quoted.reduce((sum, item) => sum + item.quote.changePct, 0) / quoted.length, quoted }];
    })
    .sort((a, b) => direction === "strong" ? b.avgChange - a.avgChange : a.avgChange - b.avgChange)
    .slice(0, limit)
    .map((row, index) => {
      const leader = row.quoted
        .sort((a, b) => direction === "strong" ? b.quote.changePct - a.quote.changePct : a.quote.changePct - b.quote.changePct)[0];
      const strength = 50 + row.avgChange * 8;
      return {
        rank: index + 1,
        name: row.group,
        change: signedPercent(row.avgChange),
        score: Math.max(1, Math.min(99, Math.round(direction === "strong" ? strength : 100 - strength))),
        lead: leader ? `${leader.code} ${leader.name}` : trial ? "盤前試撮統計" : "即時統計",
        leadChange: leader ? signedPercent(leader.quote.changePct) : "最新族群均幅",
      };
    });
}

export async function GET() {
  try {
    // The original HanStock group configuration is the single source of truth.
    // Never rank against the bundled fallback catalog or the full market.
    const { groups: allGroups, stockCatalog } = await loadConfiguredUniverse();
    const quotes = await loadQuotes(stockCatalog);
    const trial = quotes.priceType === "盤前試撮";
    const quoteByCode = new Map(quotes.rows.map((quote) => [quote.code, quote]));
    const strongStocks = stockRows(quotes.rows, "strong", stockCatalog, trial);
    const weakStocks = stockRows(quotes.rows, "weak", stockCatalog, trial);
    const fullStrongGroups = groupRows(quoteByCode, "strong", allGroups, trial, 68);
    const fullWeakGroups = groupRows(quoteByCode, "weak", allGroups, trial, 68);
    const topStockCodes = new Set([
      ...strongStocks.map((row) => row.name.slice(0, 4)),
      ...weakStocks.map((row) => row.name.slice(0, 4)),
    ]);
    const quotePayload = quotes.rows
      .filter((quote) => topStockCodes.has(quote.code))
      .map((quote) => ({
        key: quote.code,
        price: typeof quote.price === "number" && Number.isFinite(quote.price) ? quote.price : null,
        change: typeof quote.change === "number" && Number.isFinite(quote.change) ? quote.change : null,
        changePct: quote.changePct,
        session: quote.session,
      }));

    return Response.json({
      ok: true,
      fetchedAt: quotes.fetchedAt,
      sourceDate: quotes.sourceDate ?? quotes.fetchedAt,
      liveData: quotes.priceType === "即時價" || quotes.priceType === "盤前試撮",
      priceType: quotes.priceType,
      coverage: { received: quotes.rows.length, total: stockCatalog.size },
      rankings: {
        stocks: { strong: strongStocks, weak: weakStocks },
        groups: {
          strong: fullStrongGroups.slice(0, 20),
          weak: fullWeakGroups.slice(0, 20),
        },
      },
      groupRankings: { strong: fullStrongGroups, weak: fullWeakGroups },
      quotes: quotePayload,
    }, { headers: { "Cache-Control": "public, max-age=15, s-maxage=30, stale-while-revalidate=300" } });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : "live-ranking-failed" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
