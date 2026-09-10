import { NextRequest, NextResponse } from "next/server";
import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";
import { timedSingleFlight } from "../../../lib/timed-single-flight";

type Exchange = "twse" | "tpex";

type RequestedSecurity = {
  code: string;
  exchange: Exchange;
};

type MisQuote = {
  ch?: string;
  ex?: string;
  z?: string;
  y?: string;
  pz?: string;
  t?: string;
  d?: string;
};

type JsonRow = Record<string, unknown>;

type HanStockQuoteRow = {
  code?: string;
  price?: number;
  prevClose?: number;
  change?: number;
  changePct?: number;
  date?: string;
};

type HanStockQuotePayload = {
  result?: {
    data?: {
      json?: {
        fetchedAt?: string;
        priceType?: "即時價" | "收盤價";
        rows?: HanStockQuoteRow[];
      };
    };
  };
};

const MAX_SECURITIES = 50;
const LIVE_QUOTE_HEADERS = { "Cache-Control": "private, no-store, max-age=0" };
const liveQuoteBatches = new Map<string, () => Promise<NonNullable<NonNullable<NonNullable<HanStockQuotePayload['result']>['data']>['json']>>>();

function loadLiveQuoteBatch(tickers: string[]) {
  const sorted = [...new Set(tickers)].sort();
  const key = sorted.join(',');
  let load = liveQuoteBatches.get(key);
  if (!load) {
    load = timedSingleFlight(3_000, async () => {
      const input = encodeURIComponent(JSON.stringify({ json: { tickers: sorted } }));
      const response = await fetch(`https://hanstock-battle-minimal.thunghan8.chatgpt.site/api/trpc/stocks.liveQuotes?input=${input}`, {
        headers: { Accept: 'application/json', 'User-Agent': 'HanStock-Chip-Score/1.0' }, cache: 'no-store', signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) throw new Error(`HanStock quotes ${response.status}`);
      const payload = await response.json() as HanStockQuotePayload;
      const data = payload.result?.data?.json;
      if (!data || !Array.isArray(data.rows) || !data.rows.length) throw new Error('HanStock quotes unavailable');
      return data;
    });
    if (liveQuoteBatches.size >= 64) liveQuoteBatches.delete(liveQuoteBatches.keys().next().value!);
    liveQuoteBatches.set(key, load);
  }
  return load();
}

const allMarketSecurities = (marketRankingSnapshot.rows as Array<{ code: string; exchange: Exchange }>).map((row) => ({
  code: row.code,
  exchange: row.exchange,
}));

function parseNumber(value: string | undefined) {
  if (!value || value === "-") return null;
  const parsed = Number(value.replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function parseItems(value: string | null): RequestedSecurity[] {
  if (!value) return [];
  const unique = new Map<string, RequestedSecurity>();
  for (const item of value.split(",").slice(0, MAX_SECURITIES)) {
    const [exchange, code] = item.split(":");
    if ((exchange !== "twse" && exchange !== "tpex") || !/^[0-9A-Z]{4,7}$/.test(code ?? "")) continue;
    unique.set(`${exchange}:${code}`, { exchange, code });
  }
  return [...unique.values()];
}

function quoteKey(exchange: Exchange, code: string) {
  return `${exchange}:${code}`;
}

function readString(row: JsonRow, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" || typeof value === "number") return String(value).trim();
  }
  return "";
}

function taipeiSession() {
  const taipei = new Date(Date.now() + 8 * 60 * 60 * 1_000);
  return {
    date: `${taipei.getUTCFullYear()}${String(taipei.getUTCMonth() + 1).padStart(2, "0")}${String(taipei.getUTCDate()).padStart(2, "0")}`,
    weekday: taipei.getUTCDay(),
    minutes: taipei.getUTCHours() * 60 + taipei.getUTCMinutes(),
  };
}

function isPreopenTrialWindow() {
  const session = taipeiSession();
  return session.weekday >= 1 && session.weekday <= 5 && session.minutes >= 8 * 60 + 30 && session.minutes < 9 * 60;
}

function isRegularMarketWindow() {
  const session = taipeiSession();
  return session.weekday >= 1 && session.weekday <= 5 && session.minutes >= 9 * 60 && session.minutes <= 13 * 60 + 35;
}

/**
 * HanStock 主站已整合上市、上櫃的即時行情與收盤備援。籌碼頁優先讀取
 * 同一份正式行情，避免 TPEx 在 Cloudflare 執行環境封鎖 MIS／OpenAPI 時
 * 整張上櫃排行只剩破折號。拆成小批次可避免單一上游請求過長。
 */
async function fetchHanStockQuotes(securities: RequestedSecurity[]) {
  const securityByCode = new Map(securities.map((security) => [security.code, security]));
  const codes = [...securityByCode.keys()];
  const batches = Array.from({ length: Math.ceil(codes.length / 12) }, (_, index) =>
    codes.slice(index * 12, index * 12 + 12),
  );

  const settled = await Promise.allSettled(batches.map(loadLiveQuoteBatch));

  const quotes = settled.flatMap((result) => {
    if (result.status !== "fulfilled") return [];
    return (result.value.rows ?? []).flatMap((row) => {
      const code = String(row.code ?? "").trim().toUpperCase();
      const security = securityByCode.get(code);
      if (!security) return [];
      const price = typeof row.price === "number" && Number.isFinite(row.price) ? row.price : null;
      const referencePrice = typeof row.prevClose === "number" && Number.isFinite(row.prevClose) ? row.prevClose : null;
      const change = typeof row.change === "number" && Number.isFinite(row.change)
        ? row.change
        : price !== null && referencePrice !== null
          ? price - referencePrice
          : null;
      const changePct = typeof row.changePct === "number" && Number.isFinite(row.changePct)
        ? row.changePct
        : change !== null && referencePrice
          ? change / referencePrice * 100
          : null;
      return [{
        code,
        exchange: security.exchange,
        key: quoteKey(security.exchange, code),
        price,
        referencePrice,
        change,
        changePct,
        quoteTime: row.date ?? result.value.fetchedAt ?? null,
      }];
    });
  });

  const fulfilled = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const fetchedAt = fulfilled.map((data) => data.fetchedAt).filter((value): value is string => Boolean(value)).sort().at(-1)
    ?? new Date().toISOString();
  const mode = fulfilled.some((data) => data.priceType === "即時價") ? "live" as const : "close-fallback" as const;
  return { quotes, fetchedAt, mode };
}

async function fetchOfficialCloseFallback(securities: RequestedSecurity[]) {
  const requested = new Map(securities.map((security) => [quoteKey(security.exchange, security.code), security]));
  const tasks: Array<Promise<{ exchange: Exchange; rows: JsonRow[] }>> = [];

  if (securities.some((security) => security.exchange === "twse")) {
    tasks.push(fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", {
      headers: { Accept: "application/json", "User-Agent": "HanStock/1.0" },
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`TWSE close ${response.status}`);
      return { exchange: "twse" as const, rows: await response.json() as JsonRow[] };
    }));
  }

  if (securities.some((security) => security.exchange === "tpex")) {
    tasks.push(fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", {
      headers: { Accept: "application/json", "User-Agent": "HanStock/1.0" },
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`TPEx close ${response.status}`);
      return { exchange: "tpex" as const, rows: await response.json() as JsonRow[] };
    }));
  }

  const settled = await Promise.allSettled(tasks);
  return settled.flatMap((result) => {
    if (result.status !== "fulfilled" || !Array.isArray(result.value.rows)) return [];
    const { exchange, rows } = result.value;
    return rows.flatMap((row) => {
      const code = readString(row, "Code", "SecuritiesCompanyCode").toUpperCase();
      const security = requested.get(quoteKey(exchange, code));
      if (!security) return [];
      const price = parseNumber(readString(row, "ClosingPrice", "Close"));
      const change = parseNumber(readString(row, "Change"));
      const referencePrice = price !== null && change !== null ? price - change : null;
      const changePct = change !== null && referencePrice && referencePrice !== 0
        ? change / referencePrice * 100
        : null;
      return [{
        code,
        exchange,
        key: quoteKey(exchange, code),
        price,
        referencePrice,
        change,
        changePct,
        quoteTime: readString(row, "Date") || null,
      }];
    });
  });
}

export async function GET(request: NextRequest) {
  const allMarketScope = request.nextUrl.searchParams.get("scope") === "all";
  const securities = allMarketScope ? allMarketSecurities : parseItems(request.nextUrl.searchParams.get("items"));
  const preopenTrialWindow = isPreopenTrialWindow();
  const preferOfficialLive = request.nextUrl.searchParams.get("preferOfficialLive") === "1" && isRegularMarketWindow();
  if (securities.length === 0) {
    return NextResponse.json({ ok: false, error: "items is required", quotes: [] }, { status: 400 });
  }

  if (allMarketScope) {
    const quotes = await fetchOfficialCloseFallback(securities);
    return NextResponse.json(
      { ok: quotes.length > 0, mode: "close-fallback", fetchedAt: new Date().toISOString(), quotes },
      { headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=1800" } },
    );
  }

  if (!preopenTrialWindow && !preferOfficialLive) {
    try {
      const primary = await fetchHanStockQuotes(securities);
      if (primary.quotes.length > 0) {
        return NextResponse.json(
          { ok: true, ...primary },
          { headers: LIVE_QUOTE_HEADERS },
        );
      }
    } catch {
      // 主站行情短暫失敗時，繼續使用下方 TWSE MIS 與官方收盤價備援。
    }
  }

  const exCh = securities
    .map(({ code, exchange }) => `${exchange === "tpex" ? "otc" : "tse"}_${code}.tw`)
    .join("|");
  const upstreamUrl = new URL("https://mis.twse.com.tw/stock/api/getStockInfo.jsp");
  upstreamUrl.searchParams.set("ex_ch", exCh);
  upstreamUrl.searchParams.set("json", "1");
  upstreamUrl.searchParams.set("delay", "0");
  upstreamUrl.searchParams.set("_", Date.now().toString());

  try {
    const sessionResponse = await fetch("https://mis.twse.com.tw/stock/index.jsp", {
      headers: {
        Accept: "text/html,application/xhtml+xml",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "User-Agent": "Mozilla/5.0 HanStock/1.0",
      },
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    const cookie = sessionResponse.headers.get("set-cookie")?.split(";")[0];
    const response = await fetch(upstreamUrl, {
      headers: {
        Accept: "application/json",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        ...(cookie ? { Cookie: cookie } : {}),
        Referer: "https://mis.twse.com.tw/stock/fibest.jsp",
        "User-Agent": "Mozilla/5.0 HanStock/1.0",
      },
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) throw new Error(`TWSE MIS ${response.status}`);

    const payload = await response.json() as { msgArray?: MisQuote[] };
    const requestedByCode = new Map(securities.map((security) => [security.code, security]));
    const currentTaipeiDate = taipeiSession().date;
    const quotes = (payload.msgArray ?? []).flatMap((row) => {
      const responseCode = row.ch?.split(".")[0];
      const security = responseCode ? requestedByCode.get(responseCode) : undefined;
      if (!security) return [];

      const referencePrice = parseNumber(row.y);
      const trialPrice = preopenTrialWindow && row.d === currentTaipeiDate ? parseNumber(row.pz) : null;
      const price = trialPrice ?? parseNumber(row.z) ?? parseNumber(row.pz) ?? referencePrice;
      const change = price !== null && referencePrice !== null ? price - referencePrice : null;
      const changePct = change !== null && referencePrice && referencePrice !== 0
        ? change / referencePrice * 100
        : null;
      return [{
        code: security.code,
        exchange: security.exchange,
        key: quoteKey(security.exchange, security.code),
        price,
        referencePrice,
        change,
        changePct,
        quoteTime: row.d && row.t ? `${row.d} ${row.t}` : row.t ?? null,
        session: trialPrice !== null ? "preopen-trial" : "regular",
      }];
    });

    if (quotes.length === 0) throw new Error("TWSE MIS returned no requested quotes");
    return NextResponse.json(
      { ok: true, mode: quotes.some((quote) => quote.session === "preopen-trial") ? "preopen-trial" : "live", priceType: quotes.some((quote) => quote.session === "preopen-trial") ? "盤前試撮" : "即時價", fetchedAt: new Date().toISOString(), quotes },
      { headers: LIVE_QUOTE_HEADERS },
    );
  } catch (error) {
    if (preferOfficialLive) {
      try {
        const backup = await fetchHanStockQuotes(securities);
        if (backup.quotes.length > 0) {
          return NextResponse.json(
            { ok: true, ...backup, backup: "hanstock" },
            { headers: LIVE_QUOTE_HEADERS },
          );
        }
      } catch {
        // The official live feed and main-site feed are both unavailable; use
        // the completed official close below without pretending it is live.
      }
    }
    const fallbackQuotes = await fetchOfficialCloseFallback(securities);
    if (fallbackQuotes.length > 0) {
      return NextResponse.json(
        { ok: true, mode: "close-fallback", fetchedAt: new Date().toISOString(), quotes: fallbackQuotes },
        { headers: LIVE_QUOTE_HEADERS },
      );
    }
    return NextResponse.json({
      ok: false,
      error: error instanceof Error ? error.message : "quote unavailable",
      quotes: [],
    }, { status: 502 });
  }
}
