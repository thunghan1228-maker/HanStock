import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";
import weeklyBaselineSnapshot from "../../data/market-ranking-snapshot-2026-08-14.json";
import { readLatestMarketRanking, saveLatestMarketRanking } from "../../../db/market-ranking-snapshot";

type Exchange = "twse" | "tpex";
type MarketCategory = Exchange | "etf";
type FlowKey = "foreign" | "trust" | "dealer" | "hedge";

type MarketFlowRow = {
  code: string;
  name: string;
  exchange: Exchange;
  date: string;
  foreign: number;
  trust: number;
  dealer: number;
  hedge: number;
};

type ScoredFlowRow = MarketFlowRow & Record<FlowKey, number>;
type JsonRecord = Record<string, unknown>;

type MarketRankingSnapshot = {
  ok: boolean;
  fetchedAt: string;
  dataDate: string;
  rows: unknown[];
  coverage: {
    total: number;
    stocks: number;
    twse: number;
    tpex: number;
    etf: number;
    tradingDays: number;
    completeMarkets: boolean;
  };
  sources: string[];
};

const flowKeys: FlowKey[] = ["foreign", "trust", "dealer", "hedge"];
const browserHeaders = {
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
};
const snapshot = marketRankingSnapshot as unknown as MarketRankingSnapshot;
const weeklyBaseline = weeklyBaselineSnapshot as unknown as MarketRankingSnapshot;
const RANKING_REFRESH_CACHE_MS = 5 * 60 * 1000;
let rankingRefreshCache: { expiresAt: number; response: Response } | null = null;
let rankingRefreshInFlight: Promise<Response> | null = null;
let weeklyRankingRefreshCache: { expiresAt: number; response: Response } | null = null;
let weeklyRankingRefreshInFlight: Promise<Response> | null = null;

function snapshotResponse(reason?: string) {
  return Response.json(
    {
      ...snapshot,
      ok: true,
      snapshotFallback: true,
      refreshAttemptedAt: reason ? new Date().toISOString() : null,
      refreshFallbackReason: reason ?? null,
    },
    { headers: { "Cache-Control": reason ? "no-store, max-age=0" : "public, max-age=60, s-maxage=300" } },
  );
}

type RankingSeriesPoint = { date: string; foreign: number; trust: number; dealer: number; hedge: number };
type RankingSnapshotRow = {
  code: string;
  name: string;
  market: MarketCategory;
  exchange: Exchange;
  series: RankingSeriesPoint[];
};

function mergeWeeklySnapshots(current: MarketRankingSnapshot, baseline: MarketRankingSnapshot): MarketRankingSnapshot {
  const baselineByKey = new Map((baseline.rows as RankingSnapshotRow[]).map((row) => [`${row.exchange}:${row.code}`, row]));
  const rows = (current.rows as RankingSnapshotRow[]).map((row) => {
    const old = baselineByKey.get(`${row.exchange}:${row.code}`);
    const points = new Map<string, RankingSeriesPoint>();
    for (const point of old?.series ?? []) points.set(point.date, point);
    for (const point of row.series ?? []) points.set(point.date, point);
    return { ...row, series: [...points.values()].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 20) };
  });
  const tradingDays = rows.length ? Math.min(20, ...rows.map((row) => row.series.length)) : 0;
  return {
    ...current,
    rows,
    coverage: { ...current.coverage, tradingDays },
    sources: [...new Set([...(current.sources ?? []), "2026/08/14 已驗證週基準快照"])],
  };
}

async function latestAvailableResponse(reason?: string, snapshotKey = "latest") {
  // 週籌碼至少需要兩個完整五日區間。D1 在尚未綁定、短暫不可用或
  // 首次啟動時不能讓畫面退回只有六日的普通快照，否則前端會判定為
  // 「0 週」。先以內建的 8/14 已驗證週基準補齊，讓第一批週比較可用。
  const weeklyFallback = (current: MarketRankingSnapshot, fallbackReason?: string) => {
    const merged = mergeWeeklySnapshots(current, weeklyBaseline);
    if (!merged.rows.some((row) => (row as RankingSnapshotRow).series.length >= 10)) return null;
    return Response.json(
      {
        ...merged,
        ok: true,
        snapshotFallback: true,
        refreshAttemptedAt: reason ? new Date().toISOString() : null,
        refreshFallbackReason: fallbackReason ?? reason ?? "weekly_verified_baseline",
      },
      { headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  };
  try {
    const saved = await readLatestMarketRanking(snapshotKey);
    const savedHasWeeklyWindow = snapshotKey !== "weekly"
      || (Array.isArray(saved?.rows) && (saved.rows as RankingSnapshotRow[]).some((row) => row.series.length >= 10));
    if (saved && savedHasWeeklyWindow && saved.dataDate >= snapshot.dataDate && Array.isArray(saved.rows) && saved.rows.length > 0) {
      return Response.json(
        {
          ...saved,
          ok: true,
          snapshotFallback: false,
          refreshAttemptedAt: reason ? new Date().toISOString() : null,
          refreshFallbackReason: reason ?? null,
        },
        { headers: { "Cache-Control": "no-store, max-age=0" } },
      );
    }
    if (snapshotKey === "weekly") {
      const latest = await readLatestMarketRanking("latest");
      // weekly 快照可能是早期六日盤後快照；不足兩週時必須由最新
      // 盤後資料加上已驗證基準補齊，不能直接回傳六日而讓前端停在 0 週。
      const current = latest && Array.isArray(latest.rows) && latest.rows.length > 0
        ? latest as MarketRankingSnapshot
        : saved && Array.isArray(saved.rows) && saved.rows.length > 0
          ? saved as MarketRankingSnapshot
          : snapshot;
      const fallback = weeklyFallback(current);
      if (fallback) return fallback;
    }
  } catch (error) {
    console.error("[market-ranking] durable snapshot unavailable", error);
  }
  if (snapshotKey === "weekly") {
    const fallback = weeklyFallback(snapshot, "weekly_storage_unavailable_using_verified_baseline");
    if (fallback) return fallback;
  }
  return snapshotResponse(reason);
}

function formatTwseDate(date: Date) {
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

function displayDate(date: Date) {
  return `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

function displayTwseReportedDate(value: unknown) {
  const text = String(value ?? "").trim();
  if (!/^\d{8}$/.test(text)) return null;
  return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
}

function stripHtml(value: unknown) {
  return String(value ?? "")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/&minus;|&#8722;/gi, "-")
    .replace(/&amp;/gi, "&")
    .trim();
}

function toNumber(value: unknown) {
  const normalized = stripHtml(value)
    .replace(/[−－]/g, "-")
    .replace(/＋/g, "+")
    .replace(/,|\s/g, "");
  if (!normalized || normalized === "--") return 0;
  const isParenthesized = /^\(.*\)$/.test(normalized);
  const number = Number(normalized.replace(/^\(|\)$/g, ""));
  if (!Number.isFinite(number)) return 0;
  return isParenthesized ? -Math.abs(number) : number;
}

function toLots(value: unknown) {
  const number = toNumber(value);
  return Number.isFinite(number) ? Math.round((number / 1000) * 10) / 10 : 0;
}

function classifySecurity(code: string, exchange: Exchange): MarketCategory | null {
  if (/^00[0-9A-Z]{2,4}$/.test(code)) return "etf";
  if (/^[1-9][0-9]{3}[A-Z]?$/.test(code) || /^91[0-9]{4}$/.test(code)) return exchange;
  return null;
}

function findField(fields: string[], patterns: RegExp[]) {
  return fields.findIndex((field) => patterns.some((pattern) => pattern.test(stripHtml(field))));
}

function readByField(row: unknown[], fields: string[], patterns: RegExp[], fallbackIndex: number) {
  const index = findField(fields, patterns);
  return toLots(row[index >= 0 ? index : fallbackIndex]);
}

function extractTpexTable(payload: JsonRecord) {
  const directRows = payload.aaData ?? payload.data;
  if (Array.isArray(directRows)) {
    return {
      fields: Array.isArray(payload.fields) ? payload.fields.map(String) : [],
      rows: directRows as unknown[][],
    };
  }

  if (Array.isArray(payload.tables)) {
    const table = payload.tables.find((candidate) => {
      const record = candidate as JsonRecord;
      return Array.isArray(record.data) && record.data.length > 0;
    }) as JsonRecord | undefined;
    return {
      fields: Array.isArray(table?.fields) ? table.fields.map(String) : [],
      rows: Array.isArray(table?.data) ? table.data as unknown[][] : [],
    };
  }

  return { fields: [] as string[], rows: [] as unknown[][] };
}

function normalizeTwsePayload(payload: unknown, fallbackDate?: Date): { date: string; rows: MarketFlowRow[] } {
  const record = payload as JsonRecord;
  if (record.stat !== "OK" || !Array.isArray(record.fields) || !Array.isArray(record.data)) {
    throw new Error("twse_payload_invalid");
  }
  const reportedDate = displayTwseReportedDate(record.date) ?? (fallbackDate ? displayDate(fallbackDate) : null);
  if (!reportedDate) throw new Error("twse_payload_date_missing");
  const fields = record.fields.map(String);
  const codeIndex = findField(fields, [/證券代號/, /代號/]);
  const nameIndex = findField(fields, [/證券名稱/, /名稱/]);
  if (codeIndex < 0) throw new Error("twse_payload_code_field_missing");

  const rows = (record.data as unknown[][])
    .map((row) => ({
      code: stripHtml(row[codeIndex]).toUpperCase(),
      name: stripHtml(row[nameIndex >= 0 ? nameIndex : 1]),
      exchange: "twse" as const,
      date: reportedDate,
      foreign: readByField(row, fields, [/外陸資買賣超股數\(不含外資自營商\)/, /外資及陸資買賣超股數/, /外陸資買賣超股數/], 4),
      trust: readByField(row, fields, [/投信買賣超股數/], 10),
      dealer: readByField(row, fields, [/自營商買賣超股數\(自行買賣\)/], 16),
      hedge: readByField(row, fields, [/自營商買賣超股數\(避險\)/], 19),
    }))
    .filter((row) => classifySecurity(row.code, row.exchange) !== null);
  if (rows.length < 700) throw new Error(`twse_payload_incomplete_${rows.length}`);
  return { date: reportedDate, rows };
}

async function fetchTwseDay(date: Date): Promise<MarketFlowRow[]> {
  // 正式 JSON 路徑必須保留 /rwd/zh；Cloudflare 機房遭證交所擋下時，
  // 唯讀代理網址也必須把查詢字串的 & 編碼進目標 URL，不能被代理本身吃掉。
  const url = new URL("https://www.twse.com.tw/rwd/zh/fund/T86");
  url.searchParams.set("date", formatTwseDate(date));
  url.searchParams.set("selectType", "ALLBUT0999");
  url.searchParams.set("response", "json");

  let payload: JsonRecord | null = null;
  const proxyTarget = url.toString().replace(/^https:/, "http:").replace(/&/g, "%26");
  const sources = [url.toString(), `https://r.jina.ai/${proxyTarget}`];
  for (const source of sources) {
    try {
      const response = await fetch(source, {
        headers: {
          ...browserHeaders,
          Origin: "https://www.twse.com.tw",
          Referer: "https://www.twse.com.tw/zh/trading/foreign/t86.html",
        },
        signal: AbortSignal.timeout(8_000),
        cache: "no-store",
      });
      if (response.ok) {
        if (source.startsWith("https://r.jina.ai/")) {
          const text = await response.text();
          const start = text.indexOf("{");
          const end = text.lastIndexOf("}");
          if (start < 0 || end <= start) continue;
          payload = JSON.parse(text.slice(start, end + 1)) as JsonRecord;
        } else {
          payload = (await response.json()) as JsonRecord;
        }
        break;
      }
      if (response.status !== 429 && response.status < 500) return [];
    } catch {
      // TWSE 歷史日報會節流，同日期改由唯讀代理取得同一份官方 JSON。
    }
  }
  if (!payload) return [];
  try {
    const normalized = normalizeTwsePayload(payload, date);
    return normalized.date === displayDate(date) ? normalized.rows : [];
  } catch {
    return [];
  }
}

function displayOpenApiDate(value: unknown) {
  const text = String(value ?? "").trim();
  if (!/^\d{7}$/.test(text)) return null;
  return `${Number(text.slice(0, 3)) + 1911}/${text.slice(3, 5)}/${text.slice(5, 7)}`;
}

function displayTpexReportedDate(value: unknown) {
  const text = stripHtml(value);
  const rocSlash = text.match(/^(\d{2,3})\/(\d{1,2})\/(\d{1,2})$/);
  if (rocSlash) {
    return `${Number(rocSlash[1]) + 1911}/${rocSlash[2].padStart(2, "0")}/${rocSlash[3].padStart(2, "0")}`;
  }
  if (/^\d{7}$/.test(text)) return displayOpenApiDate(text);
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
  return text;
}

function formatTpexRocDate(date: Date) {
  return `${date.getUTCFullYear() - 1911}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
}

async function fetchTpexHub(source: string): Promise<{ date: string; rows: MarketFlowRow[] }> {
  const response = await fetch(source, {
    headers: { ...browserHeaders, "User-Agent": "HanStock-Battle/3.0" },
    signal: AbortSignal.timeout(10000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`tpex_hub_http_${response.status}`);
  const payload = await response.json() as JsonRecord;
  if (payload.status !== "ok" || !Array.isArray(payload.rows)) throw new Error("tpex_hub_payload_invalid");
  const rows = payload.rows.flatMap((item): MarketFlowRow[] => {
    const row = item as JsonRecord;
    const date = stripHtml(row.date);
    const code = stripHtml(row.code).toUpperCase();
    if (!/^\d{4}\/\d{2}\/\d{2}$/.test(date) || !classifySecurity(code, "tpex")) return [];
    return [{
      code,
      name: stripHtml(row.name),
      exchange: "tpex",
      date,
      foreign: toLots(row.foreign),
      trust: toLots(row.trust),
      dealer: toLots(row.dealer),
      hedge: toLots(row.hedge),
    }];
  });
  const latestDate = rows.map((row) => row.date).sort().at(-1);
  const latestRows = latestDate ? rows.filter((row) => row.date === latestDate) : [];
  if (!latestDate || latestRows.length < 600) throw new Error(`tpex_hub_incomplete_${latestRows.length}`);
  return { date: latestDate, rows: latestRows };
}

function normalizeTpexOpenApiPayload(payload: unknown): { date: string; rows: MarketFlowRow[] } {
  if (!Array.isArray(payload) || payload.length === 0) throw new Error("tpex_openapi_empty");
  const parsed = payload.flatMap((item): MarketFlowRow[] => {
    const row = item as JsonRecord;
    const date = displayOpenApiDate(row.Date);
    const code = stripHtml(row.SecuritiesCompanyCode).toUpperCase();
    const foreignKey = "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference";
    const trustKey = "SecuritiesInvestmentTrustCompanies-Difference";
    const dealerKey = "Dealers-Difference";
    if (!date || !(foreignKey in row) || !(trustKey in row) || !(dealerKey in row)) return [];
    const parsedRow: MarketFlowRow = {
      code,
      name: stripHtml(row.CompanyName),
      exchange: "tpex",
      date,
      foreign: toLots(row[foreignKey]),
      trust: toLots(row[trustKey]),
      dealer: toLots(row[dealerKey]),
      hedge: 0,
    };
    return classifySecurity(code, "tpex") ? [parsedRow] : [];
  });
  const latestDate = parsed.map((row) => row.date).sort().at(-1);
  if (!latestDate) throw new Error("tpex_openapi_unattributed");
  const rows = parsed.filter((row) => row.date === latestDate);
  if (rows.length < 600) throw new Error(`tpex_openapi_incomplete_${rows.length}`);
  return { date: latestDate, rows };
}

async function fetchTpexOpenApi(source: string): Promise<{ date: string; rows: MarketFlowRow[] }> {
  const response = await fetch(source, {
    headers: browserHeaders,
    signal: AbortSignal.timeout(10000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`tpex_openapi_http_${response.status}`);
  let text = await response.text();
  if (source.startsWith("https://r.jina.ai/")) {
    const start = text.indexOf("[");
    const end = text.lastIndexOf("]");
    if (start < 0 || end <= start) throw new Error("tpex_openapi_proxy_payload_missing");
    text = text.slice(start, end + 1);
  }
  return normalizeTpexOpenApiPayload(JSON.parse(text) as unknown);
}

async function fetchTpexLatest(minimumDate: string): Promise<{ date: string; rows: MarketFlowRow[] }> {
  const officialUrl = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading";
  const attempts = [
    fetchTpexHub("https://raw.githubusercontent.com/thunghan1228-maker/HanStock/main/data/tpex-institutional-latest.json"),
    fetchTpexHub("https://hanstock.xyz/api/hub/official/tpex-institutional-latest"),
    fetchTpexHub("https://hanstock-production.up.railway.app/api/hub/official/tpex-institutional-latest"),
    fetchTpexOpenApi(officialUrl),
    fetchTpexOpenApi(`https://r.jina.ai/${officialUrl}`),
  ].map(async (attempt) => {
    const candidate = await attempt;
    if (candidate.date < minimumDate) throw new Error(`tpex_latest_stale_${candidate.date}_expected_${minimumDate}`);
    return candidate;
  });
  try {
    // 任一正式来源先回到同一交易日即可，不必等待其余较慢的备用来源。
    return await Promise.any(attempts);
  } catch {
    throw new Error(`tpex_latest_unavailable_expected_${minimumDate}`);
  }
}

async function fetchTpexDay(date: Date): Promise<MarketFlowRow[]> {
  const legacyUrl = new URL("https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php");
  legacyUrl.searchParams.set("l", "zh-tw");
  legacyUrl.searchParams.set("d", formatTpexRocDate(date));
  legacyUrl.searchParams.set("se", "EW");
  legacyUrl.searchParams.set("t", "D");
  const modernUrl = new URL("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade");
  modernUrl.searchParams.set("type", "Daily");
  modernUrl.searchParams.set("sect", "EW");
  modernUrl.searchParams.set("date", displayDate(date));
  modernUrl.searchParams.set("response", "json");
  const legacyProxyUrl = `https://r.jina.ai/${legacyUrl.toString().replace(/&/g, "%26")}`;

  const loadSource = async (url: URL | string) => {
    const proxied = typeof url === "string";
    const response = await fetch(url, {
      headers: proxied ? browserHeaders : {
          ...browserHeaders,
          Origin: "https://www.tpex.org.tw",
          Referer: "https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/detail/day.html",
        },
      // TPEx 歷史日報在盤後與多日期查詢時常需 8–12 秒才回應。
      signal: AbortSignal.timeout(20_000),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`tpex_history_http_${response.status}`);
    let payload: JsonRecord;
    if (proxied) {
      const text = await response.text();
      const start = text.indexOf("{");
      const end = text.lastIndexOf("}");
      if (start < 0 || end <= start) throw new Error("tpex_history_proxy_payload_missing");
      payload = JSON.parse(text.slice(start, end + 1)) as JsonRecord;
    } else {
      payload = await response.json() as JsonRecord;
    }
    if (payload.stat !== "ok") {
      throw new Error(`tpex_history_status_${stripHtml(payload.stat) || "unknown"}`);
    }
    const reportedDate = stripHtml(payload.date);
    if (reportedDate && displayTpexReportedDate(reportedDate) !== displayDate(date)) {
      throw new Error(`tpex_history_date_mismatch_${reportedDate}`);
    }
    const { fields, rows } = extractTpexTable(payload);
    const parsedRows = rows.flatMap((row): MarketFlowRow[] => {
      if (row.length < 20) return [];
      const code = stripHtml(row[0]).toUpperCase();
      if (!classifySecurity(code, "tpex")) return [];
      const hasFields = fields.length === row.length;
      return [{
        code,
        name: stripHtml(row[1]),
        exchange: "tpex",
        date: displayDate(date),
        foreign: hasFields ? readByField(row, fields, [/外資及陸資.*買賣超/, /外陸資.*買賣超/], 10) : toLots(row[10]),
        trust: hasFields ? readByField(row, fields, [/投信.*買賣超/], 13) : toLots(row[13]),
        dealer: hasFields ? readByField(row, fields, [/自營商.*自行買賣.*買賣超/], 16) : toLots(row[16]),
        hedge: hasFields ? readByField(row, fields, [/自營商.*避險.*買賣超/], 19) : toLots(row[19]),
      }];
    });
    if (parsedRows.length < 600) throw new Error(`tpex_history_incomplete_${parsedRows.length}`);
    return parsedRows;
  };

  try {
    // 舊版依序等待兩條來源，第一條逾時才輪到第二條，五個歷史日容易
    // 累加超過 Worker 總時限。兩條官方路徑同時競速，第一份完整資料即採用。
    return await Promise.any([
      legacyUrl,
      modernUrl,
      legacyProxyUrl,
    ].map(loadSource));
  } catch (error) {
    const reasons = error instanceof AggregateError
      ? error.errors.map((reason) => reason instanceof Error ? reason.message : String(reason)).join("; ")
      : error instanceof Error ? error.message : "tpex_history_unavailable";
    throw new Error(`tpex_history_unavailable_${reasons}`);
  }
}

async function fetchWithConcurrency<T, R>(items: T[], limit: number, task: (item: T) => Promise<R>) {
  const results: Array<PromiseSettledResult<R>> = new Array(items.length);
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      try {
        results[index] = { status: "fulfilled", value: await task(items[index]) };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

async function fetchHistoricalDayWithRetry(date: Date, loader: (date: Date) => Promise<MarketFlowRow[]>) {
  let lastError: unknown = new Error("historical_day_unavailable");
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const rows = await loader(date);
      if (rows.length) return rows;
      lastError = new Error(`historical_day_empty_${displayDate(date)}`);
    } catch (error) {
      lastError = error;
    }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 220 * (attempt + 1)));
  }
  throw lastError;
}

function rowKey(row: Pick<MarketFlowRow, "exchange" | "code">) {
  return `${row.exchange}:${row.code}`;
}

function percentileMap(rows: MarketFlowRow[], field: FlowKey) {
  const sorted = [...rows].sort((a, b) => a[field] - b[field]);
  const result = new Map<string, number>();
  if (sorted.length <= 1) {
    sorted.forEach((row) => result.set(rowKey(row), 0));
    return result;
  }

  let index = 0;
  while (index < sorted.length) {
    let end = index;
    while (end + 1 < sorted.length && sorted[end + 1][field] === sorted[index][field]) end += 1;
    const averageRank = (index + end) / 2;
    const score = (averageRank / (sorted.length - 1)) * 200 - 100;
    for (let cursor = index; cursor <= end; cursor += 1) result.set(rowKey(sorted[cursor]), score);
    index = end + 1;
  }
  return result;
}

function normalizeDay(rows: MarketFlowRow[]): ScoredFlowRow[] {
  const scores = Object.fromEntries(flowKeys.map((field) => [field, percentileMap(rows, field)])) as Record<FlowKey, Map<string, number>>;
  return rows.map((row) => ({
    ...row,
    foreign: scores.foreign.get(rowKey(row)) ?? 0,
    trust: scores.trust.get(rowKey(row)) ?? 0,
    dealer: scores.dealer.get(rowKey(row)) ?? 0,
    hedge: scores.hedge.get(rowKey(row)) ?? 0,
  }));
}

export async function GET(request?: Request) {
  const search = request ? new URL(request.url).searchParams : new URLSearchParams();
  const refresh = search.get("refresh") ?? "0";
  const weekly = search.get("weekly") === "1";
  const snapshotKey = weekly ? "weekly" : "latest";
  const tradingDays = weekly ? 20 : 6;
  const backfill = refresh.startsWith("backfill-");
  if (!refresh || refresh === "0") return latestAvailableResponse(undefined, snapshotKey);
  if (backfill) return backfillOneMissingDay(snapshotKey, tradingDays);
  const cached = weekly ? weeklyRankingRefreshCache : rankingRefreshCache;
  if (cached && cached.expiresAt > Date.now()) {
    return cached.response.clone();
  }
  const currentInFlight = weekly ? weeklyRankingRefreshInFlight : rankingRefreshInFlight;
  if (!currentInFlight) {
    const inFlight = buildMarketRankingResponse(undefined, tradingDays).then(async (response) => {
      const payload = await response.clone().json() as { snapshotFallback?: boolean };
      if (response.ok && !payload.snapshotFallback) {
        const nextCache = { expiresAt: Date.now() + RANKING_REFRESH_CACHE_MS, response: response.clone() };
        if (weekly) weeklyRankingRefreshCache = nextCache;
        else rankingRefreshCache = nextCache;
      }
      return response;
    }).finally(() => {
      if (weekly) weeklyRankingRefreshInFlight = null;
      else rankingRefreshInFlight = null;
    });
    if (weekly) weeklyRankingRefreshInFlight = inFlight;
    else rankingRefreshInFlight = inFlight;
  }
  return (await (weekly ? weeklyRankingRefreshInFlight : rankingRefreshInFlight)!).clone();
}

async function backfillOneMissingDay(snapshotKey: string, tradingDays: number) {
  const saved = await readLatestMarketRanking(snapshotKey)
    ?? (snapshotKey === "weekly" ? await readLatestMarketRanking("latest") : null);
  if (!saved || !Array.isArray(saved.rows) || saved.rows.length === 0) {
    return latestAvailableResponse("backfill_base_snapshot_unavailable", snapshotKey);
  }

  const base = saved as unknown as MarketRankingSnapshot;
  const rows = base.rows as RankingSnapshotRow[];
  const dateCoverage = new Map<string, number>();
  for (const row of rows) for (const point of row.series ?? []) {
    dateCoverage.set(point.date, (dateCoverage.get(point.date) ?? 0) + 1);
  }
  const verifiedThreshold = Math.min(1_300, Math.floor(rows.length * 0.8));
  const [year, month, day] = base.dataDate.split("/").map(Number);
  const latestDate = new Date(Date.UTC(year, month - 1, day, 12));
  const candidates: Date[] = [];
  for (let offset = 1; offset < 40 && candidates.length < tradingDays + 8; offset += 1) {
    const date = new Date(latestDate);
    date.setUTCDate(latestDate.getUTCDate() - offset);
    if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) candidates.push(date);
  }
  const target = candidates.find((date) => (dateCoverage.get(displayDate(date)) ?? 0) < verifiedThreshold);
  if (!target) {
    return Response.json({ ...base, ok: true, snapshotFallback: false, refreshDays: [] }, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  }

  const [twseResult, tpexResult] = await Promise.allSettled([
    fetchHistoricalDayWithRetry(target, fetchTwseDay),
    fetchHistoricalDayWithRetry(target, fetchTpexDay),
  ]);
  const twse = twseResult.status === "fulfilled" ? twseResult.value : [];
  const tpex = tpexResult.status === "fulfilled" ? tpexResult.value : [];
  const tpexError = tpexResult.status === "rejected"
    ? tpexResult.reason instanceof Error ? tpexResult.reason.message : "tpex_history_unavailable"
    : null;
  const refreshDays = [{
    date: displayDate(target),
    twseCount: twse.length,
    tpexCount: tpex.length,
    tpexError,
  }];
  if (twse.length < 700 || tpex.length < 600) {
    return Response.json({
      ...base,
      ok: true,
      snapshotFallback: false,
      refreshAttemptedAt: new Date().toISOString(),
      refreshFallbackReason: "historical_backfill_incomplete",
      refreshDays,
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  }

  const pointByKey = new Map(normalizeDay([...twse, ...tpex]).map((row) => [rowKey(row), row]));
  const mergedRows = rows.map((row) => {
    const point = pointByKey.get(`${row.exchange}:${row.code}`);
    if (!point) return row;
    const series = new Map((row.series ?? []).map((item) => [item.date, item]));
    series.set(point.date, {
      date: point.date,
      foreign: point.foreign,
      trust: point.trust,
      dealer: point.dealer,
      hedge: point.hedge,
    });
    return { ...row, series: [...series.values()].sort((a, b) => b.date.localeCompare(a.date)).slice(0, tradingDays) };
  });
  const payload: MarketRankingSnapshot = {
    ...base,
    ok: true,
    fetchedAt: new Date().toISOString(),
    rows: mergedRows,
    coverage: {
      ...base.coverage,
      tradingDays: Math.min(tradingDays, ...mergedRows.map((row) => row.series.length)),
    },
    sources: [...new Set([...(base.sources ?? []), "逐日官方歷史回補並永久保存"])],
  };
  await saveLatestMarketRanking(payload, snapshotKey);
  return Response.json({ ...payload, snapshotFallback: false, refreshDays }, {
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}

async function buildMarketRankingResponse(tpexOpenApiPayload?: unknown, requestedTradingDays = 6, twseOfficialPayload?: unknown) {
  const tradingDays = requestedTradingDays >= 14 ? 20 : 6;
  const snapshotKey = tradingDays >= 14 ? "weekly" : "latest";
  const start = new Date();
  start.setUTCHours(12, 0, 0, 0);
  const dates: Date[] = [];
  for (let offset = 0; offset < 40 && dates.length < tradingDays + 8; offset += 1) {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() - offset);
    if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6) dates.push(date);
  }

  // 先以 TWSE 找出最近已完整公布的交易日，再要求 TPEx 必須使用同一天。
  // 不能只因 Hub 筆數足夠就接受較舊日期，否則整個排行會長期卡在舊快照。
  const twseAttempts: Array<{ date: string; count: number; error: string | null }> = [];
  let candidate = dates[0];
  let twse: MarketFlowRow[] = [];
  if (twseOfficialPayload !== undefined) {
    try {
      const normalized = normalizeTwsePayload(twseOfficialPayload);
      const [year, month, day] = normalized.date.split("/").map(Number);
      candidate = new Date(Date.UTC(year, month - 1, day, 12));
      twse = normalized.rows;
      twseAttempts.push({ date: normalized.date, count: normalized.rows.length, error: null });
    } catch (reason) {
      twseAttempts.push({
        date: displayDate(candidate),
        count: 0,
        error: reason instanceof Error ? reason.message : "twse_browser_payload_unavailable",
      });
    }
  } else {
    for (const date of dates) {
      try {
        const rows = await fetchTwseDay(date);
        twseAttempts.push({ date: displayDate(date), count: rows.length, error: null });
        if (rows.length < 700) continue;
        candidate = date;
        twse = rows;
        break;
      } catch (reason) {
        twseAttempts.push({
          date: displayDate(date),
          count: 0,
          error: reason instanceof Error ? reason.message : "twse_unavailable",
        });
      }
    }
  }
  if (twse.length < 700) {
    console.error("[market-ranking] latest TWSE market day unavailable", JSON.stringify(twseAttempts));
    return latestAvailableResponse("latest_complete_twse_institutional_data_unavailable", snapshotKey);
  }

  const dataDate = displayDate(candidate);
  const latestTpexResult = tpexOpenApiPayload !== undefined
    ? (() => {
      try {
        return { ok: true as const, value: normalizeTpexOpenApiPayload(tpexOpenApiPayload) };
      } catch (reason) {
        return { ok: false as const, reason };
      }
    })()
    : await fetchTpexLatest(dataDate).then(
      (value) => ({ ok: true as const, value }),
      (reason: unknown) => ({ ok: false as const, reason }),
    );
  // OpenAPI 有可能已切到下一個交易日，或 Hub 仍停留在較舊交易日；
  // 兩者日期只要不同，就改抓與 TWSE 完全相同日期的櫃買歷史日報。
  const tpexResult = latestTpexResult.ok && latestTpexResult.value.date === dataDate
    ? latestTpexResult
    : await fetchTpexDay(candidate).then(
      (rows) => ({ ok: true as const, value: { date: dataDate, rows } }),
      (reason: unknown) => ({ ok: false as const, reason }),
    );
  const tpex = tpexResult.ok ? tpexResult.value.rows : [];
  const latestTpexReason = latestTpexResult.ok
    ? `tpex_latest_date_${latestTpexResult.value.date}_expected_${dataDate}`
    : latestTpexResult.reason instanceof Error ? latestTpexResult.reason.message : "tpex_latest_unavailable";
  const tpexError = tpexResult.ok
    ? null
    : `${latestTpexReason}; ${tpexResult.reason instanceof Error ? tpexResult.reason.message : "tpex_history_unavailable"}`;
  const complete = tpex.length >= 600;
  const dayResults: Array<{ date: string; twseCount: number; tpexCount: number; tpexError: string | null; rows: ScoredFlowRow[] }> = [{
    date: dataDate,
    twseCount: twse.length,
    tpexCount: tpex.length,
    tpexError,
    rows: complete ? normalizeDay([...twse, ...tpex]) : [],
  }];

  // 五日平均、法人三日與連續買超都必須使用真正連續的最近交易日。
  // 舊版只更新最新一天、其餘沿用內建快照，會造成資料日雖然變新，
  // 五日內容卻仍混入一週前的數字。這裡同步抓最近完整交易日補齊。
  const durableSnapshots: MarketRankingSnapshot[] = [];
  try {
    const savedForMode = await readLatestMarketRanking(snapshotKey);
    const savedLatest = snapshotKey === "latest" ? null : await readLatestMarketRanking("latest");
    if (savedForMode && Array.isArray(savedForMode.rows)) durableSnapshots.push(savedForMode as MarketRankingSnapshot);
    if (savedLatest && Array.isArray(savedLatest.rows)) durableSnapshots.push(savedLatest as MarketRankingSnapshot);
  } catch (error) {
    console.error("[market-ranking] previous durable series unavailable", error);
  }
  const fallbackSnapshots = [snapshot, ...(snapshotKey === "weekly" ? [weeklyBaseline] : []), ...durableSnapshots];
  const verifiedDates = new Set(fallbackSnapshots.flatMap((fallbackSnapshot) =>
    (fallbackSnapshot.rows as RankingSnapshotRow[]).flatMap((row) => row.series?.map((point) => point.date) ?? [])));
  const candidateIndex = Math.max(0, dates.findIndex((date) => displayDate(date) === dataDate));
  const historyCandidates = dates.slice(candidateIndex + 1, candidateIndex + tradingDays + 8);
  // 每次刷新只補最接近的一個缺日，完成後立即寫入 D1；下一輪再補下一日。
  // 這樣不會因五至二十日的官方請求累加而超過 Worker 時限，也不會用
  // 一次失敗的長刷新覆蓋已驗證資料。既有日期直接由永久快照合併。
  const historyDates = historyCandidates.filter((date) => !verifiedDates.has(displayDate(date))).slice(0, 1);
  const [twseHistory, tpexHistory] = await Promise.all([
    fetchWithConcurrency(historyDates, 3, (date) => fetchHistoricalDayWithRetry(date, fetchTwseDay)),
    // 每個日期內已有兩條官方來源競速；日期間改為逐日處理，避免 TPEx
    // 同時承受多日查詢而節流，五日回補仍可在 Worker 時限內完成。
    fetchWithConcurrency(historyDates, 1, (date) => fetchHistoricalDayWithRetry(date, fetchTpexDay)),
  ]);
  historyDates.forEach((date, index) => {
    const historicalTwse = twseHistory[index].status === "fulfilled" ? twseHistory[index].value : [];
    const historicalTpex = tpexHistory[index].status === "fulfilled" ? tpexHistory[index].value : [];
    const historicalComplete = historicalTwse.length >= 700 && historicalTpex.length >= 600;
    dayResults.push({
      date: displayDate(date),
      twseCount: historicalTwse.length,
      tpexCount: historicalTpex.length,
      tpexError: tpexHistory[index].status === "rejected"
        ? tpexHistory[index].reason instanceof Error ? tpexHistory[index].reason.message : "tpex_history_unavailable"
        : null,
      rows: historicalComplete ? normalizeDay([...historicalTwse, ...historicalTpex]) : [],
    });
  });

  const availableDays = dayResults
    .filter((day) => day.rows.length > 0)
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, tradingDays);
  // 今天上市、上櫃資料完整就先發布今天；歷史日若暫時下載失敗，
  // 後面會用最近一次驗證快照補足，不再讓整批排行退回昨天。
  if (!availableDays.some((day) => day.date === dataDate)) {
    return latestAvailableResponse("latest_complete_joint_market_day_unavailable", snapshotKey);
  }

  const seriesByStock = new Map<string, Array<{ date: string; foreign: number; trust: number; dealer: number; hedge: number }>>();
  for (const day of availableDays) {
    for (const row of day.rows) {
      const key = rowKey(row);
      const series = seriesByStock.get(key) ?? [];
      series.push({ date: row.date, foreign: row.foreign, trust: row.trust, dealer: row.dealer, hedge: row.hedge });
      seriesByStock.set(key, series);
    }
  }

  // Preserve every previously verified day. A refresh must add or replace a
  // date, never shrink the time series when one historical exchange request is
  // briefly rate-limited. Weekly mode also retains the verified older baseline.
  for (const fallbackSnapshot of fallbackSnapshots) for (const snapshotRow of fallbackSnapshot.rows as Array<{
    code?: string;
    exchange?: Exchange;
    series?: Array<{ date: string; foreign: number; trust: number; dealer: number; hedge: number }>;
  }>) {
    if (!snapshotRow.code || !snapshotRow.exchange || !Array.isArray(snapshotRow.series)) continue;
    const key = `${snapshotRow.exchange}:${snapshotRow.code}`;
    const series = seriesByStock.get(key) ?? [];
    const knownDates = new Set(series.map((point) => point.date));
    for (const point of snapshotRow.series) {
      if (!knownDates.has(point.date)) {
        series.push(point);
        knownDates.add(point.date);
      }
    }
    series.sort((a, b) => b.date.localeCompare(a.date));
    seriesByStock.set(key, series);
  }

  const latestRows = availableDays[0].rows.flatMap((row) => {
    const series = seriesByStock.get(rowKey(row))?.slice(0, tradingDays) ?? [];
    if (series.length < 6) return [];
    return [{
      code: row.code,
      name: row.name,
      market: classifySecurity(row.code, row.exchange) ?? row.exchange,
      exchange: row.exchange,
      series,
    }];
  });
  const coverage = latestRows.reduce(
    (counts, row) => ({ ...counts, [row.market]: counts[row.market] + 1 }),
    { twse: 0, tpex: 0, etf: 0 },
  );
  if (coverage.twse < 700 || coverage.tpex < 600) {
    console.error("[market-ranking] complete security coverage unavailable", JSON.stringify(coverage));
    return latestAvailableResponse("complete_six_day_security_coverage_unavailable", snapshotKey);
  }

  const payload: MarketRankingSnapshot = {
      ok: true,
      fetchedAt: new Date().toISOString(),
      dataDate: availableDays[0].date,
      rows: latestRows,
      coverage: {
        total: latestRows.length,
        stocks: coverage.twse + coverage.tpex,
        twse: coverage.twse,
        tpex: coverage.tpex,
        etf: coverage.etf,
        tradingDays: Math.min(tradingDays, ...latestRows.map((row) => row.series.length)),
        completeMarkets: true,
      },
      sources: [`TWSE 最近${tradingDays}個交易日三大法人日報`, `TPEx 最近${tradingDays}個交易日三大法人日報`, "最近驗證快照（僅在歷史日短暫缺漏時補齊）"],
    };
  try {
    await saveLatestMarketRanking(payload, snapshotKey);
  } catch (error) {
    console.error("[market-ranking] durable snapshot save failed", error);
  }
  return Response.json(
    {
      ...payload,
      refreshDays: dayResults.map(({ date, twseCount, tpexCount, tpexError }) => ({
        date,
        twseCount,
        tpexCount,
        tpexError,
      })),
    },
    { headers: { "Cache-Control": "no-store, max-age=0" } },
  );
}

export async function POST(request: Request) {
  let tpexPayload: unknown;
  let twsePayload: unknown;
  let tradingDays = 6;
  try {
    const body = await request.json() as JsonRecord;
    tpexPayload = body.tpexPayload;
    twsePayload = body.twsePayload;
    tradingDays = Number(body.tradingDays) >= 14 ? 20 : 6;
  } catch {
    return Response.json({ ok: false, error: "official_payload_invalid_json" }, { status: 400 });
  }
  if (!Array.isArray(tpexPayload)) {
    return Response.json({ ok: false, error: "tpex_payload_required" }, { status: 422 });
  }
  if (!twsePayload || typeof twsePayload !== "object") {
    return Response.json({ ok: false, error: "twse_payload_required" }, { status: 422 });
  }
  return buildMarketRankingResponse(tpexPayload, tradingDays, twsePayload);
}
