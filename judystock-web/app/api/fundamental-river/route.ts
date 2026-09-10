import { type NextRequest } from "next/server";
import stockGroupsSource from "../../../data/stock_groups.py?raw";
import { recordFundamentalRiverQuery, type FundamentalRiverSnapshotInput } from "../../../db/fundamental-river";
import { readTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import {
  buildFundamentalRiverPoints,
  buildQuarterFundamentals,
  estimateFundamentalWaterline,
  FUNDAMENTAL_RIVER_MODEL_VERSION,
  fundamentalMedian,
  fundamentalRiverPosition,
} from "../../../lib/fundamental-river";

type JsonRow = Record<string, unknown>;
type Market = "twse" | "tpex";
type StockMember = [string, string];
type GroupMap = Record<string, StockMember[]>;
type ValuationRow = { ticker: string; name: string; market: Market; pe: number | null; pb: number | null; dataDate: string };
type GroupStats = { pe: number | null; pb: number | null; sampleSize: number };
type WeeklyReportSeed = { ticker: string; name: string; groupName: string; reportPeriod: string; singleQuarterEps: number };

const browserHeaders = { Accept: "application/json, text/plain, */*", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8", "User-Agent": "Mozilla/5.0 HanStock-Fundamental-River/1.0" };
const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF", "其他"]);
const WEEKLY_REPORT_DATA_DATE = "2026/08/28";
const WEEKLY_REPORTS: WeeklyReportSeed[] = [
  { ticker: "3661", name: "世芯-KY", groupName: "IP", reportPeriod: "2026Q2", singleQuarterEps: 20.01 },
  { ticker: "4157", name: "太景*-KY", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: -0.03 },
  { ticker: "4912", name: "聯德控股-KY", groupName: "電零組", reportPeriod: "2026Q2", singleQuarterEps: 1.50 },
  { ticker: "4927", name: "泰鼎-KY", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: -2.36 },
  { ticker: "4971", name: "IET-KY", groupName: "矽光子", reportPeriod: "2026Q2", singleQuarterEps: 0.43 },
  { ticker: "4977", name: "眾達-KY", groupName: "矽光子", reportPeriod: "2026Q2", singleQuarterEps: 2.66 },
  { ticker: "4991", name: "環宇-KY", groupName: "矽光子", reportPeriod: "2026Q2", singleQuarterEps: 0.92 },
  { ticker: "5284", name: "jpp-KY", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: 4.41 },
  { ticker: "6415", name: "矽力*-KY", groupName: "IP", reportPeriod: "2026Q2", singleQuarterEps: 4.05 },
  { ticker: "6451", name: "訊芯-KY", groupName: "矽光子", reportPeriod: "2026Q2", singleQuarterEps: 0.68 },
  { ticker: "6591", name: "動力-KY", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: -0.02 },
  { ticker: "6741", name: "91APP*-KY", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: 1.15 },
  { ticker: "6781", name: "AES-KY", groupName: "小電腦", reportPeriod: "2026Q2", singleQuarterEps: 12.81 },
  { ticker: "7717", name: "萊德光電-KY", groupName: "低軌衛星", reportPeriod: "2026Q2", singleQuarterEps: 2.77 },
  { ticker: "8105", name: "凌巨", groupName: "其他", reportPeriod: "2026Q2", singleQuarterEps: 0.16 },
];
let marketCache: { expiresAt: number; value: Awaited<ReturnType<typeof buildMarketContext>> } | null = null;
let marketLoad: Promise<Awaited<ReturnType<typeof buildMarketContext>>> | null = null;

function clean(value: unknown) {
  return String(value ?? "").replace(/<[^>]*>/g, "").replaceAll(",", "").trim();
}

function compactKey(value: string) {
  return value.replace(/[\s_()（）\/\-]/g, "").toLowerCase();
}

function pick(row: JsonRow, aliases: string[], contains: string[] = []) {
  const entries = Object.entries(row);
  const exact = new Set(aliases.map(compactKey));
  const found = entries.find(([key]) => exact.has(compactKey(key)))
    ?? entries.find(([key]) => contains.some((needle) => compactKey(key).includes(compactKey(needle))));
  return found ? clean(found[1]) : "";
}

function numberValue(value: unknown) {
  const normalized = clean(value).replaceAll("%", "").replace(/[−－]/g, "-");
  if (!normalized || normalized === "-" || normalized.toUpperCase() === "N/A") return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeOfficialDate(value: string) {
  const compact = value.replace(/\D/g, "");
  if (/^\d{8}$/.test(compact)) return `${compact.slice(0, 4)}/${compact.slice(4, 6)}/${compact.slice(6, 8)}`;
  if (/^\d{7}$/.test(compact)) return `${Number(compact.slice(0, 3)) + 1911}/${compact.slice(3, 5)}/${compact.slice(5, 7)}`;
  return value;
}

function rowsFrom(payload: unknown): JsonRow[] {
  if (Array.isArray(payload)) return payload.filter((row): row is JsonRow => Boolean(row) && typeof row === "object" && !Array.isArray(row));
  if (!payload || typeof payload !== "object") return [];
  const record = payload as JsonRow;
  for (const key of ["data", "rows", "aaData", "result"]) {
    const rows = rowsFrom(record[key]);
    if (rows.length) return rows;
  }
  return [];
}

function parseStockGroups(source: string) {
  const normalized = source.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return {} as GroupMap;
  const dictionary = normalized.slice(start, end + 2).replace(/\(/g, "[").replace(/\)/g, "]").replace(/'/g, '"').replace(/,\s*([}\]])/g, "$1");
  try { return JSON.parse(dictionary) as GroupMap; } catch { return {} as GroupMap; }
}

const groups = Object.entries(parseStockGroups(stockGroupsSource)).flatMap(([name, members]) => EXCLUDED_GROUPS.has(name) ? [] : [{
  name,
  members: [...new Map(members.flatMap(([rawCode, rawName]) => {
    const code = rawCode.trim();
    const name = rawName.trim().replace(/\*$/, "");
    return /^\d{4}$/.test(code) && !code.startsWith("00") && name ? [[code, name] as const] : [];
  }))].map(([code, name]) => ({ code, name })),
}]);
const groupByTicker = new Map<string, string>();
for (const group of groups) for (const member of group.members) if (!groupByTicker.has(member.code)) groupByTicker.set(member.code, group.name);

async function fetchRows(url: string, timeout = 12_000) {
  const response = await fetch(url, { cache: "no-store", headers: browserHeaders, signal: AbortSignal.timeout(timeout) });
  if (!response.ok) throw new Error(`source_http_${response.status}`);
  return rowsFrom(await response.json());
}

async function firstAvailable(urls: string[]) {
  for (const url of urls) {
    try {
      const rows = await fetchRows(url);
      if (rows.length) return rows;
    } catch { /* alternate official endpoint */ }
  }
  return [];
}

function valuationFromRows(rows: JsonRow[], market: Market) {
  return rows.flatMap((row): ValuationRow[] => {
    const ticker = pick(row, ["證券代號", "股票代號", "SecuritiesCompanyCode", "Code"], ["證券代號", "股票代號"]);
    if (!/^\d{4}$/.test(ticker) || ticker.startsWith("00")) return [];
    return [{
      ticker,
      name: pick(row, ["證券名稱", "股票名稱", "CompanyName", "Name"], ["證券名稱", "股票名稱"]),
      market,
      pe: numberValue(pick(row, ["本益比", "PEratio", "PriceEarningRatio", "P/E"], ["本益比", "PriceEarningRatio"])),
      pb: numberValue(pick(row, ["股價淨值比", "PBratio", "PriceBookRatio", "P/B"], ["股價淨值比", "PriceBookRatio"])),
      dataDate: normalizeOfficialDate(pick(row, ["日期", "資料日期", "Date"], ["日期"])),
    }];
  });
}

async function buildMarketContext() {
  const [twseRows, tpexRows, indicators] = await Promise.all([
    firstAvailable(["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"]),
    firstAvailable(["https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]),
    readTechnicalMarketIndicators().catch(() => []),
  ]);
  const valuations = [...valuationFromRows(twseRows, "twse"), ...valuationFromRows(tpexRows, "tpex")];
  const valuationByTicker = new Map(valuations.map((row) => [row.ticker, row]));
  const indicatorByTicker = new Map(indicators.map((row) => [row.code, row]));
  const marketPe = fundamentalMedian(valuations.flatMap((row) => row.pe !== null && row.pe >= 2 && row.pe <= 180 ? [row.pe] : []));
  const marketPb = fundamentalMedian(valuations.flatMap((row) => row.pb !== null && row.pb >= .2 && row.pb <= 20 ? [row.pb] : []));
  const groupStats = new Map<string, GroupStats>();
  for (const group of groups) {
    const peers = group.members.flatMap((member) => {
      const valuation = valuationByTicker.get(member.code);
      return valuation ? [valuation] : [];
    });
    const peValues = peers.flatMap((row) => row.pe !== null && row.pe >= 2 && row.pe <= 180 ? [row.pe] : []);
    const pbValues = peers.flatMap((row) => row.pb !== null && row.pb >= .2 && row.pb <= 20 ? [row.pb] : []);
    groupStats.set(group.name, {
      pe: peValues.length >= 3 ? fundamentalMedian(peValues) : marketPe,
      pb: pbValues.length >= 3 ? fundamentalMedian(pbValues) : marketPb,
      sampleSize: Math.max(peValues.length, pbValues.length),
    });
  }
  return { valuations, valuationByTicker, indicatorByTicker, groupStats, marketPe, marketPb };
}

async function loadMarketContext() {
  if (marketCache && marketCache.expiresAt > Date.now()) return marketCache.value;
  if (!marketLoad) marketLoad = buildMarketContext().then((value) => {
    marketCache = { expiresAt: Date.now() + 15 * 60 * 1_000, value };
    return value;
  }).finally(() => { marketLoad = null; });
  return marketLoad;
}

async function loadDailyCandles(ticker: string) {
  const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval: "1d" } }));
  const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.candles?input=${input}`, { cache: "no-store", headers: browserHeaders, signal: AbortSignal.timeout(12_000) });
  if (!response.ok) return [];
  const payload = await response.json() as { result?: { data?: { json?: { candles?: JsonRow[] } } } };
  return (payload.result?.data?.json?.candles ?? []).flatMap((row) => {
    const date = String(row.date ?? row.time ?? "").slice(0, 10).replaceAll("-", "/");
    const close = numberValue(row.close);
    return date && close !== null && close > 0 ? [{ date, close }] : [];
  }).sort((a, b) => a.date.localeCompare(b.date)).slice(-780);
}

async function loadFinMind(ticker: string, dataset: "TaiwanStockFinancialStatements" | "TaiwanStockMonthRevenue") {
  const start = new Date();
  start.setUTCFullYear(start.getUTCFullYear() - (dataset === "TaiwanStockFinancialStatements" ? 4 : 2));
  const url = new URL("https://api.finmindtrade.com/api/v4/data");
  url.searchParams.set("dataset", dataset);
  url.searchParams.set("data_id", ticker);
  url.searchParams.set("start_date", start.toISOString().slice(0, 10));
  return fetchRows(url.toString(), 22_000).catch(() => []);
}

function revenueSummary(rows: JsonRow[]) {
  const values = rows.flatMap((row) => {
    const year = numberValue(row.revenue_year);
    const month = numberValue(row.revenue_month);
    const revenue = numberValue(row.revenue);
    if (year === null || month === null || revenue === null) return [];
    return [{ year, month, revenue, period: `${year}/${String(month).padStart(2, "0")}` }];
  }).sort((a, b) => a.year - b.year || a.month - b.month);
  const byPeriod = new Map(values.map((row) => [`${row.year}-${row.month}`, row.revenue]));
  const latest = values.at(-1);
  const recent = values.slice(-3).reverse().map((row) => {
    const previousYear = byPeriod.get(`${row.year - 1}-${row.month}`) ?? null;
    return { ...row, yoyPct: previousYear ? (row.revenue / previousYear - 1) * 100 : null };
  });
  if (!latest) return { recent, ytdYoYPct: null };
  const currentYtd = values.filter((row) => row.year === latest.year && row.month <= latest.month).reduce((sum, row) => sum + row.revenue, 0);
  const previousYtd = values.filter((row) => row.year === latest.year - 1 && row.month <= latest.month).reduce((sum, row) => sum + row.revenue, 0);
  return { recent, ytdYoYPct: previousYtd > 0 ? (currentYtd / previousYtd - 1) * 100 : null };
}

function currentScreener(context: Awaited<ReturnType<typeof buildMarketContext>>) {
  return context.valuations.flatMap((valuation) => {
    const indicator = context.indicatorByTicker.get(valuation.ticker);
    const close = numberValue(indicator?.payload.close);
    const maScore = numberValue(indicator?.payload.maScore);
    if (close === null || maScore === null || maScore < 10) return [];
    const groupName = groupByTicker.get(valuation.ticker) ?? "未分類";
    const stats = context.groupStats.get(groupName) ?? { pe: context.marketPe, pb: context.marketPb, sampleSize: 0 };
    const estimate = estimateFundamentalWaterline({ close, pe: valuation.pe, pb: valuation.pb, ttmEps: valuation.pe && valuation.pe > 0 ? close / valuation.pe : null, groupMedianPe: stats.pe, groupMedianPb: stats.pb });
    if (estimate.waterline === null) return [];
    const position = fundamentalRiverPosition(close, estimate.waterline);
    if (position !== "特價" && position !== "便宜" && position !== "跌破特價") return [];
    const distancePct = (close / estimate.waterline - 1) * 100;
    return [{ ticker: valuation.ticker, name: valuation.name || String(indicator?.payload.name ?? valuation.ticker), market: valuation.market, groupName, maScore, close, changePct: numberValue(indicator?.payload.changePct), position, distancePct }];
  }).sort((a, b) => b.maScore - a.maScore || a.distancePct - b.distancePct).slice(0, 50);
}

function currentRiverSnapshot(context: Awaited<ReturnType<typeof buildMarketContext>>, ticker: string) {
  const valuation = context.valuationByTicker.get(ticker);
  const indicator = context.indicatorByTicker.get(ticker);
  const close = numberValue(indicator?.payload.close);
  const maScore = numberValue(indicator?.payload.maScore);
  const groupName = groupByTicker.get(ticker) ?? "未分類";
  const stats = context.groupStats.get(groupName) ?? { pe: context.marketPe, pb: context.marketPb, sampleSize: 0 };
  const estimate = estimateFundamentalWaterline({
    close,
    pe: valuation?.pe ?? null,
    pb: valuation?.pb ?? null,
    ttmEps: close !== null && valuation?.pe != null && valuation.pe > 0 ? close / valuation.pe : null,
    groupMedianPe: stats.pe,
    groupMedianPb: stats.pb,
  });
  const position = close !== null && estimate.waterline !== null ? fundamentalRiverPosition(close, estimate.waterline) : null;
  return {
    ticker,
    name: valuation?.name || String(indicator?.payload.name ?? ticker),
    market: valuation?.market ?? null,
    groupName,
    maScore,
    close,
    waterline: estimate.waterline,
    position,
    distancePct: close !== null && estimate.waterline !== null ? (close / estimate.waterline - 1) * 100 : null,
  };
}

function currentWeeklyReports(context: Awaited<ReturnType<typeof buildMarketContext>>) {
  return WEEKLY_REPORTS.map((report) => {
    const snapshot = currentRiverSnapshot(context, report.ticker);
    return {
      ...report,
      name: report.name || snapshot.name,
      market: snapshot.market ?? "twse",
      groupName: report.groupName || snapshot.groupName,
      position: snapshot.position,
      close: snapshot.close,
    };
  });
}

function summaryPayload(context: Awaited<ReturnType<typeof buildMarketContext>>) {
  const dates = context.valuations.map((row) => row.dataDate).filter(Boolean).sort();
  return {
    dataDate: dates.at(-1) ?? "",
    reportWeek: WEEKLY_REPORT_DATA_DATE,
    recentReports: currentWeeklyReports(context),
    screener: currentScreener(context),
  };
}

export async function GET(request: NextRequest) {
  const requestedMode = request.nextUrl.searchParams.get("mode");
  const mode = requestedMode === "summary" || requestedMode === "ranking" ? requestedMode : "detail";
  const contextPromise = loadMarketContext();
  if (mode === "summary") {
    const context = await contextPromise;
    return Response.json({ ok: true, modelVersion: FUNDAMENTAL_RIVER_MODEL_VERSION, updatedAt: new Date().toISOString(), ...summaryPayload(context) }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
  }
  if (mode === "ranking") {
    const tickers = [...new Set((request.nextUrl.searchParams.get("tickers") ?? "").split(",").map((ticker) => ticker.trim()).filter((ticker) => /^\d{4}$/.test(ticker) && !ticker.startsWith("00")))].slice(0, 20);
    if (!tickers.length) return Response.json({ ok: false, error: "tickers_required" }, { status: 400 });
    const context = await contextPromise;
    return Response.json({ ok: true, updatedAt: new Date().toISOString(), rows: tickers.map((ticker) => currentRiverSnapshot(context, ticker)) }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
  }

  const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
  const market: Market = request.nextUrl.searchParams.get("market") === "tpex" ? "tpex" : "twse";
  if (!/^\d{4}$/.test(ticker) || ticker.startsWith("00")) return Response.json({ ok: false, error: "listed_stock_required" }, { status: 400 });
  const [context, candles, statementRows, revenueRows] = await Promise.all([
    contextPromise,
    loadDailyCandles(ticker).catch(() => []),
    loadFinMind(ticker, "TaiwanStockFinancialStatements"),
    loadFinMind(ticker, "TaiwanStockMonthRevenue"),
  ]);
  const valuation = context.valuationByTicker.get(ticker) ?? { ticker, name: "", market, pe: null, pb: null, dataDate: "" };
  const indicator = context.indicatorByTicker.get(ticker);
  const groupName = groupByTicker.get(ticker) ?? "未分類";
  const group = context.groupStats.get(groupName) ?? { pe: context.marketPe, pb: context.marketPb, sampleSize: 0 };
  const quarters = buildQuarterFundamentals(statementRows.flatMap((row) => {
    const date = clean(row.date).slice(0, 10);
    const type = clean(row.type);
    const value = numberValue(row.value);
    return date && type && value !== null ? [{ date, type, value }] : [];
  }));
  const currentPrice = candles.at(-1)?.close ?? numberValue(indicator?.payload.close);
  const latestQuarter = quarters.at(-1) ?? null;
  const ttmEps = latestQuarter?.ttmEps ?? (currentPrice !== null && valuation.pe !== null && valuation.pe > 0 ? currentPrice / valuation.pe : null);
  const estimate = estimateFundamentalWaterline({ close: currentPrice, pe: valuation.pe, pb: valuation.pb, ttmEps, groupMedianPe: group.pe, groupMedianPb: group.pb });
  const waterline = estimate.waterline;
  const position = currentPrice !== null && waterline !== null ? fundamentalRiverPosition(currentPrice, waterline) : null;
  const distancePct = currentPrice !== null && waterline !== null ? (currentPrice / waterline - 1) * 100 : null;
  const riverPoints = buildFundamentalRiverPoints(candles, quarters, group.pe, waterline);
  const bvps = currentPrice !== null && valuation.pb !== null && valuation.pb > 0 ? currentPrice / valuation.pb : null;
  const summary = summaryPayload(context);
  return Response.json({
    ...summary,
    ok: candles.length > 0 || quarters.length > 0 || valuation.pe !== null,
    modelVersion: FUNDAMENTAL_RIVER_MODEL_VERSION,
    ticker,
    name: valuation.name || String(indicator?.payload.name ?? ticker),
    market,
    groupName,
    updatedAt: new Date().toISOString(),
    dataDate: valuation.dataDate,
    currentPrice,
    pe: valuation.pe,
    pb: valuation.pb,
    bvps,
    ttmEps,
    groupMedianPe: group.pe,
    groupMedianPb: group.pb,
    peerSampleSize: group.sampleSize,
    valuationBasis: estimate.basis,
    waterline,
    position,
    distancePct,
    maScore: numberValue(indicator?.payload.maScore),
    quarters: quarters.slice(-8),
    revenues: revenueSummary(revenueRows),
    riverPoints,
  }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null) as FundamentalRiverSnapshotInput | null;
  if (!body || !/^\d{4}$/.test(body.ticker) || !body.name || !["twse", "tpex"].includes(body.market) || !body.groupName || !body.reportPeriod || !body.position) {
    return Response.json({ ok: false, error: "invalid_snapshot" }, { status: 400 });
  }
  const numericKeys = ["ttmEps", "waterline", "distancePct", "close", "maScore", "pe", "pb"] as const;
  if (numericKeys.some((key) => body[key] !== null && !Number.isFinite(body[key]))) return Response.json({ ok: false, error: "invalid_numbers" }, { status: 400 });
  const saved = await recordFundamentalRiverQuery(body).catch(() => false);
  return Response.json({ ok: true, saved });
}
