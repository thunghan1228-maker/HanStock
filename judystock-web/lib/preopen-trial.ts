import marketRankingSnapshot from "../app/data/market-ranking-snapshot.json";

type Exchange = "twse" | "tpex";
type MisQuote = { ch?: string; z?: string; y?: string; pz?: string; t?: string; d?: string };

export type PreopenTrialQuote = {
  code: string;
  exchange: Exchange;
  price: number;
  prevClose: number;
  change: number;
  changePct: number;
  quoteTime: string | null;
  session: "preopen-trial";
  hasTrialPrice: boolean;
};

const exchangeByCode = new Map(
  (marketRankingSnapshot.rows as Array<{ code: string; exchange: Exchange }>).map((row) => [row.code, row.exchange]),
);

type PreopenTrialResult = { key: string; expiresAt: number; quotes: PreopenTrialQuote[]; trialCount: number };

let cached: PreopenTrialResult | null = null;
let pending: { key: string; promise: Promise<PreopenTrialResult> } | null = null;

function numberValue(value: string | undefined) {
  if (!value || value === "-") return null;
  const parsed = Number(value.replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

export function taipeiMarketClock(now = new Date()) {
  const shifted = new Date(now.getTime() + 8 * 60 * 60 * 1_000);
  return {
    date: `${shifted.getUTCFullYear()}${String(shifted.getUTCMonth() + 1).padStart(2, "0")}${String(shifted.getUTCDate()).padStart(2, "0")}`,
    weekday: shifted.getUTCDay(),
    minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
  };
}

export function isPreopenTrialWindow(now = new Date()) {
  const clock = taipeiMarketClock(now);
  return clock.weekday >= 1 && clock.weekday <= 5 && clock.minutes >= 8 * 60 + 30 && clock.minutes < 9 * 60;
}

async function sessionCookie() {
  const response = await fetch("https://mis.twse.com.tw/stock/index.jsp", {
    cache: "no-store",
    headers: {
      Accept: "text/html,application/xhtml+xml",
      "Accept-Language": "zh-TW,zh;q=0.9",
      "User-Agent": "Mozilla/5.0 HanStock-Battle-Preopen/1.0",
    },
    signal: AbortSignal.timeout(6_000),
  });
  return response.headers.get("set-cookie")?.split(";")[0] ?? "";
}

async function loadBatch(codes: string[], cookie: string) {
  const channels = codes.flatMap((code) => {
    const exchange = exchangeByCode.get(code);
    if (!exchange) return [];
    return [`${exchange === "tpex" ? "otc" : "tse"}_${code}.tw`];
  });
  const endpoint = new URL("https://mis.twse.com.tw/stock/api/getStockInfo.jsp");
  endpoint.searchParams.set("ex_ch", channels.join("|"));
  endpoint.searchParams.set("json", "1");
  endpoint.searchParams.set("delay", "0");
  endpoint.searchParams.set("_", Date.now().toString());
  const response = await fetch(endpoint, {
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Accept-Language": "zh-TW,zh;q=0.9",
      ...(cookie ? { Cookie: cookie } : {}),
      Referer: "https://mis.twse.com.tw/stock/fibest.jsp",
      "User-Agent": "Mozilla/5.0 HanStock-Battle-Preopen/1.0",
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`preopen-mis-${response.status}`);
  const payload = await response.json() as { msgArray?: MisQuote[] };
  return payload.msgArray ?? [];
}

export async function loadPreopenTrialQuotes(requestedCodes: string[]) {
  if (!isPreopenTrialWindow()) throw new Error("outside-preopen-trial-window");
  const codes = [...new Set(requestedCodes.filter((code) => /^\d{4}$/.test(code) && exchangeByCode.has(code)))];
  const clock = taipeiMarketClock();
  const cacheKey = `${clock.date}:${codes.length}:${codes[0] ?? ""}:${codes.at(-1) ?? ""}`;
  if (cached?.key === cacheKey && cached.expiresAt > Date.now()) return cached;
  if (pending?.key === cacheKey) return pending.promise;

  const promise = (async () => {
    const cookie = await sessionCookie();
    // MIS 可承載百檔頻道；減少全市場請求數，避免盤前多裝置輪詢造成限流。
    const batches = Array.from({ length: Math.ceil(codes.length / 100) }, (_, index) => codes.slice(index * 100, index * 100 + 100));
    const settled = await Promise.allSettled(batches.map((batch) => loadBatch(batch, cookie)));
    const requested = new Set(codes);
    let trialCount = 0;
    const quotes = settled.flatMap((result) => result.status === "fulfilled" ? result.value : []).flatMap((row) => {
      const code = row.ch?.split(".")[0] ?? "";
      const exchange = exchangeByCode.get(code);
      if (!requested.has(code) || !exchange) return [];
      const prevClose = numberValue(row.y);
      if (prevClose === null || prevClose <= 0) return [];
      const trialPrice = row.d === clock.date ? numberValue(row.pz) : null;
      const price = trialPrice ?? prevClose;
      const change = price - prevClose;
      if (trialPrice !== null) trialCount += 1;
      return [{
        code,
        exchange,
        price,
        prevClose,
        change,
        changePct: change / prevClose * 100,
        quoteTime: row.d && row.t ? `${row.d} ${row.t}` : row.t ?? null,
        session: "preopen-trial" as const,
        hasTrialPrice: trialPrice !== null,
      }];
    });
    if (quotes.length < Math.min(300, Math.floor(codes.length * 0.55)) || trialCount === 0) {
      throw new Error(`preopen-coverage-${quotes.length}-${trialCount}-${codes.length}`);
    }
    cached = { key: cacheKey, expiresAt: Date.now() + 8_000, quotes, trialCount };
    return cached;
  })();
  pending = { key: cacheKey, promise };
  try {
    return await promise;
  } finally {
    if (pending?.promise === promise) pending = null;
  }
}
