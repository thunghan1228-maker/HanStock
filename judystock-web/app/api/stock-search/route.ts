import stockGroupsSource from "../../../data/stock_groups.py?raw";
import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";

type StockMember = [string, string];
type GroupMap = Record<string, StockMember[]>;
type SearchStock = {
  ticker: string;
  name: string;
  group: string;
  groups: string[];
  price: string;
  change: string;
  exchange?: "twse" | "tpex";
  changePct?: number | null;
};
const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF", "其他"]);

function parseStockGroups(source: string): GroupMap {
  const normalized = source.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return {};

  try {
    return JSON.parse(
      normalized
        .slice(start, end + 2)
        .replace(/\(/g, "[")
        .replace(/\)/g, "]")
        .replace(/'/g, '"')
        .replace(/,\s*([}\]])/g, "$1"),
    ) as GroupMap;
  } catch {
    return {};
  }
}

const extraSymbols: Array<[string, string, string]> = [
  ["0056", "元大高股息", "ETF"],
  ["00403A", "主動統一升級50", "ETF"],
  ["00981A", "主動統一台股增長", "ETF"],
  ["00982A", "主動群益台灣強棒", "ETF"],
  ["00991A", "主動復華未來50", "ETF"],
  ["00992A", "主動群益科技創新", "ETF"],
];

const exchangeByTicker = new Map(
  (marketRankingSnapshot.rows as Array<{ code: string; exchange: "twse" | "tpex" }>).map((row) => [row.code, row.exchange]),
);

const stockCatalog = (() => {
  const stocks = new Map<string, SearchStock>();
  Object.entries(parseStockGroups(stockGroupsSource)).forEach(([group, members]) => {
    if (EXCLUDED_GROUPS.has(group)) return;
    members.forEach(([ticker, rawName]) => {
      const name = rawName.replace(/\*$/, "");
      const current = stocks.get(ticker);
      if (current) {
        if (!current.groups.includes(group)) current.groups.push(group);
      } else {
        stocks.set(ticker, { ticker, name, group, groups: [group], price: "—", change: "—", exchange: exchangeByTicker.get(ticker) });
      }
    });
  });
  (marketRankingSnapshot.rows as Array<{ code: string; name?: string; market?: string; exchange: "twse" | "tpex" }>).forEach((row) => {
    const ticker = row.code.trim().toUpperCase();
    if (!ticker || stocks.has(ticker)) return;
    const group = row.market === "etf" ? "ETF" : "未分類";
    stocks.set(ticker, {
      ticker,
      name: row.name?.trim() || ticker,
      group,
      groups: group === "未分類" ? [] : [group],
      price: "—",
      change: "—",
      exchange: row.exchange,
    });
  });
  extraSymbols.forEach(([ticker, name, group]) => stocks.set(ticker, { ticker, name, group, groups: [group], price: "—", change: "—", exchange: exchangeByTicker.get(ticker) }));
  return [...stocks.values()];
})();
const stockCatalogByTicker = new Map(stockCatalog.map((stock) => [stock.ticker, stock]));

type LiveChangeRow = { code?: string; changePct?: number | null };
const quoteRequests = new Map<string, { expiresAt: number; pending: Promise<LiveChangeRow[]> }>();
async function fetchLiveChanges(tickers: string[]) {
  const codes = [...new Set(tickers)].sort();
  const batches = Array.from({ length: Math.ceil(codes.length / 12) }, (_, index) => codes.slice(index * 12, index * 12 + 12));
  const settled = await Promise.allSettled(batches.map((batch) => {
    const key = batch.join(",");
    const cached = quoteRequests.get(key);
    if (cached && cached.expiresAt > Date.now()) return cached.pending;
    const pending = (async () => {
      const input = encodeURIComponent(JSON.stringify({ json: { tickers: batch } }));
      const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.liveQuotes?input=${input}`, {
        cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.1" }, signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) throw new Error("live-quotes-unavailable");
      const payload = await response.json() as { result?: { data?: { json?: { rows?: LiveChangeRow[] } } } };
      return payload.result?.data?.json?.rows ?? [];
    })();
    quoteRequests.set(key, { expiresAt: Date.now() + 5_000, pending });
    if (quoteRequests.size > 256) quoteRequests.delete(quoteRequests.keys().next().value!);
    void pending.catch(() => { if (quoteRequests.get(key)?.pending === pending) quoteRequests.delete(key); });
    return pending;
  }));
  return new Map(settled.flatMap((result) => result.status === "fulfilled" ? result.value : []).flatMap((row) => {
    const ticker = String(row.code ?? "").trim().toUpperCase();
    const changePct = row.changePct == null ? NaN : Number(row.changePct);
    return ticker && Number.isFinite(changePct) ? [[ticker, changePct] as const] : [];
  }));
}

function matchRank(stock: SearchStock, keyword: string) {
  const ticker = stock.ticker.toLowerCase();
  const name = stock.name.toLowerCase();
  if (ticker === keyword || name === keyword) return 0;
  if (ticker.startsWith(keyword) || name.startsWith(keyword)) return 1;
  return 2;
}

export async function GET(request: Request) {
  const searchParams = new URL(request.url).searchParams;
  const requestedTickers = [...new Set((searchParams.get("tickers") ?? "").split(",").map((ticker) => ticker.trim().toUpperCase()).filter((ticker) => /^[0-9A-Z]{2,12}$/.test(ticker)))].slice(0, 50);
  if (requestedTickers.length > 0) {
    const catalogStocks = requestedTickers.flatMap((ticker) => {
      const stock = stockCatalogByTicker.get(ticker);
      return stock ? [stock] : [];
    });
    // Signal rows already contain the trigger-time price and change percentage.
    // Return stable names/groups immediately instead of holding the whole signal
    // center behind the much slower live-quote service.
    if (searchParams.get("metadataOnly") === "1") {
      return Response.json({
        stocks: catalogStocks.map((stock) => ({ ...stock, changePct: null })),
      }, { headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800" } });
    }
    const changes = await fetchLiveChanges(requestedTickers).catch(() => new Map<string, number>());
    const stocks = catalogStocks.map((stock) => ({ ...stock, changePct: changes.get(stock.ticker) ?? null }));
    return Response.json({ stocks }, { headers: { "Cache-Control": "public, max-age=5, stale-while-revalidate=20" } });
  }
  const keyword = searchParams.get("q")?.trim().toLowerCase() ?? "";
  if (!keyword) return Response.json({ stocks: [] }, { headers: { "Cache-Control": "no-store" } });

  const stocks = stockCatalog
    .filter((stock) => stock.ticker.toLowerCase().includes(keyword) || stock.name.toLowerCase().includes(keyword))
    .sort((a, b) => matchRank(a, keyword) - matchRank(b, keyword) || a.ticker.localeCompare(b.ticker))
    .slice(0, 8);

  return Response.json({ stocks }, { headers: { "Cache-Control": "no-store" } });
}
