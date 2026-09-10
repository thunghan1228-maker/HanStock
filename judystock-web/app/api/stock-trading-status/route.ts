import { NextRequest } from "next/server";
import { observeMarketSource } from "../../../lib/market-source-diagnostics";
import { GET as getDispositionRisk } from "../disposition-risk/route";
import { isIndividualStockCode, parseTaifexStockFutures, parseTpexDayTradeCsv, parseTpexDayTradeRows, parseTpexMarginCsv, parseTpexMarginRows, parseTwseDayTradeRows, parseTwseMarginRows, type StockTradingStatus } from "../../../lib/stock-trading-status";

type DispositionPayload = { dispositions?: Array<{ code?: string; status?: StockTradingStatus["disposition"] | "已結束" }> };
type CachedSources = {
  expiresAt: number;
  updatedAt: string;
  complete: boolean;
  twse: ReturnType<typeof parseTwseMarginRows> | null;
  tpex: ReturnType<typeof parseTpexMarginRows> | null;
  twseDayTrade: ReturnType<typeof parseTwseDayTradeRows> | null;
  tpexDayTrade: ReturnType<typeof parseTpexDayTradeRows> | null;
  futures: ReturnType<typeof parseTaifexStockFutures> | null;
  dispositions: Map<string, StockTradingStatus["disposition"]> | null;
};

const SOURCE_CACHE_MS = 30 * 60_000;
const PARTIAL_SOURCE_CACHE_MS = 10_000;
let cache: CachedSources | null = null;
let sourceLoadPromise: Promise<CachedSources> | null = null;

const sourceHeaders = { Accept: "application/json", "User-Agent": "HanStock-Trading-Status/1.0" };

async function fetchJson(url: string, timeout = 10_000) {
  const response = await fetch(url, { cache: "no-store", headers: sourceHeaders, signal: AbortSignal.timeout(timeout) });
  if (!response.ok) throw new Error(`trading-status-${response.status}`);
  return response.json() as Promise<unknown>;
}

async function fetchProxyJson(officialUrl: string) {
  const hostAndPath = officialUrl.replace(/^https?:\/\//, "");
  const response = await fetch(`https://r.jina.ai/http://${hostAndPath}`, {
    cache: "no-store",
    headers: { Accept: "text/plain,*/*", "User-Agent": "Mozilla/5.0 HanStock-Trading-Status/1.2" },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`trading-status-proxy-${response.status}`);
  const text = await response.text();
  try {
    return JSON.parse(text) as unknown;
  } catch {
    const start = text.indexOf("[");
    const end = text.lastIndexOf("]");
    if (start < 0 || end <= start) throw new Error("trading-status-proxy-payload");
    return JSON.parse(text.slice(start, end + 1)) as unknown;
  }
}

function fetchTpexJson(path: string) {
  const officialUrl = `https://www.tpex.org.tw/openapi/v1/${path}`;
  return observeMarketSource("stock-trading-status", "tpex-daytrade-json", Promise.any([fetchJson(officialUrl, 15_000), fetchProxyJson(officialUrl)]), ["official-json", "proxy-json"]);
}

async function fetchTpexMarginCsv(url: string) {
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "text/csv,text/plain,*/*",
      "User-Agent": "Mozilla/5.0 HanStock-Trading-Status/1.2",
      Referer: "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html",
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`trading-status-margin-csv-${response.status}`);
  const rows = parseTpexMarginCsv(await response.text());
  if (rows.size < 100) throw new Error(`trading-status-margin-csv-incomplete-${rows.size}`);
  return rows;
}

async function fetchTpexMarginSource() {
  const openApiUrl = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance";
  const csvUrl = "https://www.tpex.org.tw/www/zh-tw/margin/balance?response=csv-u8";
  const proxyCsvUrl = `https://r.jina.ai/http://${csvUrl.replace(/^https?:\/\//, "")}`;
  return observeMarketSource("stock-trading-status", "tpex-margin", Promise.any([
    fetchJson(openApiUrl, 15_000).then(parseTpexMarginRows).then(validateTpexMarginRows),
    fetchProxyJson(openApiUrl).then(parseTpexMarginRows).then(validateTpexMarginRows),
    fetchTpexMarginCsv(csvUrl),
    fetchTpexMarginCsv(proxyCsvUrl),
  ]), ["official-json", "proxy-json", "official-csv", "proxy-csv"]);
}

function validateTpexMarginRows(rows: ReturnType<typeof parseTpexMarginRows>) {
  if (rows.size < 100) throw new Error(`trading-status-margin-incomplete-${rows.size}`);
  return rows;
}

async function fetchTpexDayTradeCsv(url: string) {
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "text/csv,text/plain,*/*",
      "User-Agent": "Mozilla/5.0 HanStock-Trading-Status/1.1",
      Referer: "https://www.tpex.org.tw/zh-tw/mainboard/trading/day-trading/securities.html",
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`trading-status-csv-${response.status}`);
  const codes = parseTpexDayTradeCsv(await response.text());
  if (codes.size < 100) throw new Error(`trading-status-csv-incomplete-${codes.size}`);
  return codes;
}

async function fetchTpexDayTradeSource() {
  const legacyUrl = "https://www.tpex.org.tw/web/stock/trading/intraday_trading/intraday_trading_list_result.php?l=zh-tw&o=data";
  const proxyUrl = `https://r.jina.ai/http://${legacyUrl.replace(/^https?:\/\//, "")}`;
  return observeMarketSource("stock-trading-status", "tpex-daytrade", Promise.any([
    fetchTpexJson("tpex_securities").then((payload) => {
      const codes = parseTpexDayTradeRows(payload);
      if (codes.size < 100) throw new Error(`trading-status-openapi-incomplete-${codes.size}`);
      return codes;
    }),
    fetchTpexDayTradeCsv(legacyUrl),
    fetchTpexDayTradeCsv(proxyUrl),
  ]), ["json-race", "official-csv", "proxy-csv"]);
}

async function fetchText(url: string) {
  const response = await fetch(url, { cache: "no-store", headers: { Accept: "text/html", "User-Agent": "HanStock-Trading-Status/1.0" }, signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`trading-status-${response.status}`);
  return response.text();
}

async function loadSources(force = false) {
  if (!force && cache && cache.expiresAt > Date.now()) return cache;
  if (sourceLoadPromise) return sourceLoadPromise;
  sourceLoadPromise = (async () => {
    const settled = await Promise.allSettled([
      fetchJson("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"),
      fetchTpexMarginSource(),
      fetchText("https://www.taifex.com.tw/cht/4/traderPLEquity"),
      getDispositionRisk().then((response) => response.json() as Promise<DispositionPayload>),
      fetchJson("https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"),
      fetchTpexDayTradeSource(),
    ]);
    const freshTwseDayTrade = settled[4].status === "fulfilled" ? parseTwseDayTradeRows(settled[4].value) : null;
    const freshTpexDayTrade = settled[5].status === "fulfilled" ? settled[5].value : null;
    const tpexDayTrade = freshTpexDayTrade && freshTpexDayTrade.size >= 100 ? freshTpexDayTrade : cache?.tpexDayTrade ?? null;
    const freshTpexMargin = settled[1].status === "fulfilled" ? settled[1].value : null;
    // Cash day-trading membership alone does not prove margin eligibility.
    // Check only disappearing, previously observed ordinary-stock margin rows
    // that are still on the target list; otherwise legitimate differences
    // between the two official lists would force endless ten-second refreshes.
    const missingTpexCodes = freshTpexMargin && cache?.tpex
      ? [...cache.tpex.keys()].filter((code) => isIndividualStockCode(code) && tpexDayTrade?.has(code) && !freshTpexMargin.has(code))
      : [];
    const tpexMargin = freshTpexMargin && missingTpexCodes.length
      ? new Map([...freshTpexMargin, ...missingTpexCodes.map((code) => [code, cache!.tpex!.get(code)!] as const)])
      : freshTpexMargin ?? cache?.tpex ?? null;
    const tpexIncomplete = settled[1].status === "rejected" || !freshTpexDayTrade || freshTpexDayTrade.size < 100 || missingTpexCodes.length > 0;
    const next: CachedSources = {
      expiresAt: 0,
      updatedAt: new Date().toISOString(),
      complete: false,
      twse: settled[0].status === "fulfilled" ? parseTwseMarginRows(settled[0].value) : cache?.twse ?? null,
      // Preserve only missing rows; fresh O/X suspensions must override old
      // available values even when another stock is temporarily missing.
      tpex: tpexMargin,
      // A healthy official target list contains hundreds of codes. Treat an empty
      // or truncated response as unavailable data instead of marking every stock
      // as ineligible for cash day trading.
      twseDayTrade: freshTwseDayTrade && freshTwseDayTrade.size >= 100 ? freshTwseDayTrade : cache?.twseDayTrade ?? null,
      tpexDayTrade,
      futures: settled[2].status === "fulfilled" ? parseTaifexStockFutures(settled[2].value) : cache?.futures ?? null,
      dispositions: settled[3].status === "fulfilled" ? new Map((settled[3].value.dispositions ?? []).flatMap((row) => {
        const code = String(row.code ?? "").trim().toUpperCase();
        return row.status && row.status !== "已結束" ? [[code, row.status] as const] : [];
      })) : cache?.dispositions ?? null,
    };
    const complete = !tpexIncomplete && Boolean(next.twse && next.tpex && next.twseDayTrade && next.tpexDayTrade && next.futures && next.dispositions);
    next.complete = complete;
    next.expiresAt = Date.now() + (complete ? SOURCE_CACHE_MS : PARTIAL_SOURCE_CACHE_MS);
    cache = next;
    return next;
  })();
  try {
    return await sourceLoadPromise;
  } finally {
    sourceLoadPromise = null;
  }
}

function dayTradeAvailability(code: string, sources: CachedSources): StockTradingStatus["dayTrade"] {
  if (sources.twseDayTrade?.has(code) || sources.tpexDayTrade?.has(code)) return "available";
  if (sources.twseDayTrade && sources.tpexDayTrade) return "unavailable";
  if (sources.twse?.has(code) && sources.twseDayTrade) return "unavailable";
  if (sources.tpex?.has(code) && sources.tpexDayTrade) return "unavailable";
  return "unknown";
}

export async function GET(request: NextRequest) {
  const requested = (request.nextUrl.searchParams.get("tickers") ?? "").split(",").map((code) => code.trim().toUpperCase()).filter(isIndividualStockCode).slice(0, 400);
  const sources = await loadSources(request.nextUrl.searchParams.get("refresh") === "1");
  const codes = requested.length ? requested : [...new Set([...(sources.twse?.keys() ?? []), ...(sources.tpex?.keys() ?? []), ...(sources.futures?.keys() ?? [])])];
  const rows = codes.map((code): StockTradingStatus => {
    const margin = sources.twse?.get(code) ?? sources.tpex?.get(code);
    const derivatives = sources.futures?.get(code);
    const marginSourceReady = Boolean(sources.twse && sources.tpex);
    return {
      code,
      margin: margin?.margin ?? (marginSourceReady ? "unavailable" : "unknown"),
      short: margin?.short ?? (marginSourceReady ? "unavailable" : "unknown"),
      dayTrade: dayTradeAvailability(code, sources),
      disposition: sources.dispositions?.get(code) ?? null,
      stockFuture: derivatives?.stockFuture ?? false,
      miniStockFuture: derivatives?.miniStockFuture ?? false,
      futuresReady: Boolean(sources.futures),
      dispositionReady: Boolean(sources.dispositions),
      note: margin?.note ?? "",
    };
  });
  const sourceState = { twseMargin: Boolean(sources.twse), tpexMargin: Boolean(sources.tpex), twseDayTrade: Boolean(sources.twseDayTrade), tpexDayTrade: Boolean(sources.tpexDayTrade), taifex: Boolean(sources.futures), disposition: Boolean(sources.dispositions) };
  const complete = sources.complete;
  return Response.json({
    ok: Object.values(sourceState).some(Boolean),
    complete,
    updatedAt: sources.updatedAt,
    sourceState,
    rows,
  }, { headers: { "Cache-Control": complete ? "public, max-age=300, s-maxage=1800, stale-while-revalidate=86400" : "no-store" } });
}
