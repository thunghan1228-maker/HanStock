import stockGroupsSource from "../../../data/stock_groups.py?raw";
import { isPreopenTrialWindow, loadPreopenTrialQuotes, type PreopenTrialQuote } from "../../../lib/preopen-trial";

type StockMember = [string, string];
type GroupMap = Record<string, StockMember[]>;
type UpstreamStock = {
  stock_code?: string;
  stock_name?: string;
  quote?: {
    close?: number | null;
    price_chg?: number | null;
    pct_chg?: number | null;
  } | null;
};
type FallbackQuote = { price: number | null; change: number | null; changePct: number | null };
type LiveQuotePayload = { result?: { data?: { json?: { rows?: Array<{ code?: string; price?: number; prevClose?: number; change?: number; changePct?: number }> } } } };
type GroupResponsePayload = { retrievedAt: string; groupName: string; memberCount: number; averageChange: string | null; priceType: string; stocks: Array<{ ticker: string; name: string; price: string; priceChange: string; change: string }> };

const GROUP_RESPONSE_TTL_MS = 10_000;
const groupResponseCache = new Map<string, { savedAt: number; payload: GroupResponsePayload }>();

const groupAliases: Record<string, string[]> = {
  "光通訊": ["矽光子"],
  "PCB 設備": ["小電組", "PCB", "設備股"],
  "AI 伺服器": ["AI"],
  "先進封裝": ["扇形封裝"],
  "半導體設備": ["設備股"],
  "網通": ["電通"],
  "IC 設計": ["IP"],
  "電源供應器": ["電零組"],
  "連接器": ["電零組"],
  "工業電腦": ["D電腦"],
  "雲端服務": ["資訊"],
  "安控": ["小光電"],
  "生技": ["生醫"],
  "塑化": ["塑膠", "台塑四寶"],
  "金融": ["金融股"],
  "電器電纜": ["電纜"],
};

function parseStockGroups(source: string): GroupMap {
  const normalized = source.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return {};

  const dictionary = normalized
    .slice(start, end + 2)
    .replace(/\(/g, "[")
    .replace(/\)/g, "]")
    .replace(/'/g, '"')
    .replace(/,\s*([}\]])/g, "$1");

  try {
    return JSON.parse(dictionary) as GroupMap;
  } catch {
    return {};
  }
}

const allGroups = parseStockGroups(stockGroupsSource);

function sourceGroupNames(displayName: string) {
  if (allGroups[displayName]) return [displayName];
  return groupAliases[displayName] ?? [];
}

function formatPrice(value: number | null | undefined) {
  if (typeof value !== "number") return "—";
  return value.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPriceChange(value: number | null | undefined) {
  if (typeof value !== "number") return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

function formatPercentValue(value: number | null) {
  if (value === null) return null;
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

async function loadRealtimeGroup(groupName: string) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4500);
  try {
    const response = await fetch(
      `https://hanstock.xyz/api/realtime/group/${encodeURIComponent(groupName)}?subscribe=true&sort=change_desc`,
      { cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } },
    );
    if (!response.ok) return [] as UpstreamStock[];
    const payload = await response.json() as { groups?: Array<{ stocks?: UpstreamStock[] }> };
    return payload.groups?.flatMap((group) => group.stocks ?? []) ?? [];
  } catch {
    return [] as UpstreamStock[];
  } finally {
    clearTimeout(timer);
  }
}

async function loadLatestQuotes(tickers: string[]) {
  const batches = Array.from({ length: Math.ceil(tickers.length / 50) }, (_, index) => tickers.slice(index * 50, index * 50 + 50));
  const settled = await Promise.allSettled(batches.map(async (batch) => {
    const input = encodeURIComponent(JSON.stringify({ json: { tickers: batch } }));
    const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.liveQuotes?input=${input}`, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/1.0" },
      signal: AbortSignal.timeout(4_500),
    });
    if (!response.ok) throw new Error(`latest-quotes-${response.status}`);
    const payload = await response.json() as LiveQuotePayload;
    return payload.result?.data?.json?.rows ?? [];
  }));

  const quotes = new Map<string, FallbackQuote>();
  settled.forEach((result) => {
    if (result.status !== "fulfilled") return;
    result.value.forEach((row) => {
      const code = String(row.code ?? "").trim();
      if (!code) return;
      const price = typeof row.price === "number" && Number.isFinite(row.price) ? row.price : null;
      const previousClose = typeof row.prevClose === "number" && Number.isFinite(row.prevClose) ? row.prevClose : null;
      const change = typeof row.change === "number" && Number.isFinite(row.change)
        ? row.change
        : price !== null && previousClose !== null ? price - previousClose : null;
      const changePct = typeof row.changePct === "number" && Number.isFinite(row.changePct)
        ? row.changePct
        : change !== null && previousClose ? change / previousClose * 100 : null;
      quotes.set(code, { price, change, changePct });
    });
  });
  return quotes;
}

export async function GET(request: Request) {
  const displayName = new URL(request.url).searchParams.get("name")?.trim() ?? "";
  const groupNames = sourceGroupNames(displayName);
  if (!displayName || groupNames.length === 0) {
    return Response.json({ groupName: displayName, stocks: [], error: "group-not-found" }, { status: 404 });
  }
  const cached = groupResponseCache.get(displayName);
  if (cached && Date.now() - cached.savedAt < GROUP_RESPONSE_TTL_MS) {
    return Response.json(cached.payload, { headers: { "Cache-Control": "public, max-age=3, s-maxage=8, stale-while-revalidate=30", "X-HanStock-Group-Cache": "hit" } });
  }

  const members = new Map<string, string>();
  groupNames.forEach((groupName) => {
    (allGroups[groupName] ?? []).forEach(([ticker, name]) => members.set(ticker, name.replace(/\*$/, "")));
  });

  let preopenQuotes = new Map<string, PreopenTrialQuote>();
  if (isPreopenTrialWindow()) {
    try {
      const trial = await loadPreopenTrialQuotes([...members.keys()]);
      preopenQuotes = new Map(trial.quotes.map((quote) => [quote.code, quote]));
    } catch {
      // A group without a matched trial price keeps the normal quote fallback.
    }
  }
  const [realtimeGroups, latestQuotes] = preopenQuotes.size > 0
    ? [[], new Map<string, FallbackQuote>()] as const
    : await Promise.all([
        Promise.race([
          Promise.all(groupNames.map(loadRealtimeGroup)),
          new Promise<UpstreamStock[][]>((resolve) => setTimeout(() => resolve([]), 1_200)),
        ]),
        loadLatestQuotes([...members.keys()]),
      ]);
  const quotes = new Map<string, UpstreamStock>();
  realtimeGroups.flat().forEach((stock) => {
    if (stock.stock_code) quotes.set(stock.stock_code, stock);
  });
  const stocks = [...members.entries()]
    .map(([ticker, name]) => {
      const stock = quotes.get(ticker);
      const trial = preopenQuotes.get(ticker);
      const fallback = latestQuotes.get(ticker);
      const realtimePrice = stock?.quote?.close;
      const realtimeChange = stock?.quote?.price_chg;
      const realtimeChangeRatio = stock?.quote?.pct_chg;
      const price = trial?.price ?? (typeof realtimePrice === "number" && Number.isFinite(realtimePrice) ? realtimePrice : fallback?.price);
      const priceChange = trial?.change ?? (typeof realtimeChange === "number" && Number.isFinite(realtimeChange) ? realtimeChange : fallback?.change);
      const changePercent = trial?.changePct ?? (typeof realtimeChangeRatio === "number" && Number.isFinite(realtimeChangeRatio)
        ? realtimeChangeRatio * 100
        : fallback?.changePct);
      return {
        ticker,
        name: stock?.stock_name ?? name,
        price: formatPrice(price),
        priceChange: formatPriceChange(priceChange),
        change: formatPercentValue(changePercent ?? null) ?? "—",
        changeValue: typeof changePercent === "number" ? changePercent : null,
      };
    })
    .sort((a, b) => {
      if (a.changeValue === null && b.changeValue === null) return a.ticker.localeCompare(b.ticker);
      if (a.changeValue === null) return 1;
      if (b.changeValue === null) return -1;
      return b.changeValue - a.changeValue;
    })
    // Omit the internal change value from this public stock payload.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    .map(({ changeValue: _changeValue, ...stock }) => stock);

  const validChanges = stocks
    .map((stock) => Number.parseFloat(stock.change))
    .filter((value) => Number.isFinite(value));
  const averageChange = validChanges.length > 0
    ? validChanges.reduce((sum, value) => sum + value, 0) / validChanges.length
    : null;

  const payload: GroupResponsePayload = { retrievedAt: new Date().toISOString(), groupName: displayName, memberCount: stocks.length, averageChange: formatPercentValue(averageChange), priceType: preopenQuotes.size > 0 ? "盤前試撮" : "即時價", stocks };
  groupResponseCache.set(displayName, { savedAt: Date.now(), payload });
  if (groupResponseCache.size > 80) groupResponseCache.delete(groupResponseCache.keys().next().value ?? "");
  return Response.json(payload, {
    headers: { "Cache-Control": "public, max-age=3, s-maxage=8, stale-while-revalidate=30", "X-HanStock-Group-Cache": "miss" },
  });
}
