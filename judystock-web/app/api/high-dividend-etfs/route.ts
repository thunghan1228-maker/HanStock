import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";
import tpexVerifiedSnapshot from "../../data/high-dividend-etf-tpex-snapshot.json";
import twseVerifiedSnapshot from "../../data/high-dividend-etf-twse-snapshot.json";

type Exchange = "twse" | "tpex";
type JsonRow = Record<string, unknown>;
type UniverseRow = { code: string; name: string; market: string; exchange: Exchange };
type DistributionRecord = {
  code: string;
  name: string;
  exchange: Exchange;
  exDate: string;
  paymentDate: string;
  amount: number | null;
};
type CloseRecord = { code: string; name: string; exchange: Exchange; price: number; date: string };
type EtfResultRow = {
  code: string;
  name: string;
  exchange: Exchange;
  category: string;
  annualYield: number;
  trailingDistributionAmount: number;
  distributionMonths: number[];
  nextDistributionDate: string | null;
  nextDistributionAmount: number | null;
  price: number;
  priceDate: string | null;
};

const CACHE_MS = 30 * 60 * 1000;
const FETCH_TIMEOUT_MS = 30_000;
let cache: { expiresAt: number; payload: Awaited<ReturnType<typeof loadPayload>> } | null = null;

function taipeiDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function isoDate(value: unknown) {
  const text = String(value ?? "").trim();
  const roc = text.match(/(\d{3})\s*(?:年|[/.\-])\s*(\d{1,2})\s*(?:月|[/.\-])\s*(\d{1,2})/);
  if (roc) return `${Number(roc[1]) + 1911}-${roc[2].padStart(2, "0")}-${roc[3].padStart(2, "0")}`;
  const compactRoc = text.match(/^(\d{3})(\d{2})(\d{2})$/);
  if (compactRoc) return `${Number(compactRoc[1]) + 1911}-${compactRoc[2]}-${compactRoc[3]}`;
  const gregorian = text.match(/(20\d{2})\D?(\d{2})\D?(\d{2})/);
  return gregorian ? `${gregorian[1]}-${gregorian[2]}-${gregorian[3]}` : "";
}

function numberValue(value: unknown) {
  const normalized = String(value ?? "").replaceAll(",", "").replace(/[^0-9.\-]/g, "");
  if (!normalized || normalized === "-") return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function category(name: string) {
  if (/非投資等級|非投資級|高收益債|非投資債|非投等/.test(name)) return "非投資級債";
  if (/美債|美國公債|美國政府債/.test(name)) return "美國公債";
  if (/投資級|公司債|金融債|新興債|債/.test(name)) return "投資級／其他債券";
  if (/美國|美股|S&P|NASDAQ|那斯達克|道瓊|費城半導體/.test(name)) return "美國股票";
  return "股票型 ETF";
}

async function fetchJson(url: string, init?: RequestInit) {
  let lastError: unknown = new Error("ETF upstream unavailable");
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(url, {
        ...init,
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Accept-Language": "zh-TW",
          "User-Agent": "Mozilla/5.0 HanStock-ETF/2.0",
          ...(init?.headers ?? {}),
        },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      if (!response.ok) throw new Error(`ETF upstream ${response.status}`);
      return await response.json() as unknown;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function previousYearDate(today: string) {
  const [year, month, day] = today.split("-").map(Number);
  const lastDay = new Date(Date.UTC(year - 1, month, 0)).getUTCDate();
  return `${year - 1}-${String(month).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
}

function splitCalendarYearRanges(start: string, end: string) {
  const startYear = Number(start.slice(0, 4));
  const endYear = Number(end.slice(0, 4));
  return Array.from({ length: endYear - startYear + 1 }, (_, offset) => {
    const year = startYear + offset;
    return {
      start: year === startYear ? start : `${year}-01-01`,
      end: year === endYear ? end : `${year}-12-31`,
    };
  });
}

function dedupeDistributions(records: DistributionRecord[]) {
  return [...new Map(records.map((record) => [
    `${record.exchange}:${record.code}:${record.exDate}:${record.paymentDate}:${record.amount ?? ""}`,
    record,
  ])).values()];
}

async function fetchTwseDistributionRange(start: string, end: string) {
  const url = new URL("https://www.twse.com.tw/rwd/zh/ETF/etfDiv");
  url.searchParams.set("response", "json");
  url.searchParams.set("startDate", start.replaceAll("-", ""));
  url.searchParams.set("endDate", end.replaceAll("-", ""));
  const payload = await fetchJson(url.toString()) as { data?: unknown[][] };
  return (Array.isArray(payload.data) ? payload.data : []).flatMap((row): DistributionRecord[] => {
    const code = String(row[0] ?? "").trim().toUpperCase();
    const exDate = isoDate(row[2]);
    const paymentDate = isoDate(row[4]);
    if (!/^[0-9A-Z]{4,7}$/.test(code) || !exDate) return [];
    return [{
      code,
      name: String(row[1] ?? code).trim(),
      exchange: "twse",
      exDate,
      paymentDate,
      amount: numberValue(row[5]),
    }];
  });
}

async function fetchTwseDistributions(today: string) {
  const ranges = splitCalendarYearRanges(previousYearDate(today), today);
  const records = await Promise.all(ranges.map(({ start, end }) => fetchTwseDistributionRange(start, end)));
  return dedupeDistributions(records.flat());
}

async function fetchTpexDistributionRange(start: string, end: string) {
  const body = new URLSearchParams({
    stkNo: "",
    startDate: start.replaceAll("-", ""),
    endDate: end.replaceAll("-", ""),
    lang: "zh-tw",
  });
  const payload = await fetchJson("https://info.tpex.org.tw/api/etfExDiv", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
    body,
  });
  return (Array.isArray(payload) ? payload : []).flatMap((row): DistributionRecord[] => {
    const item = row as JsonRow;
    const code = String(item.stockNo ?? "").trim().toUpperCase();
    const exDate = isoDate(item.divDate);
    const paymentDate = isoDate(item.inDate);
    if (!/^[0-9A-Z]{4,7}$/.test(code) || !exDate) return [];
    return [{
      code,
      name: String(item.stockName ?? code).trim(),
      exchange: "tpex",
      exDate,
      paymentDate,
      amount: numberValue(item.amount),
    }];
  });
}

async function fetchTpexDistributions(today: string) {
  const ranges = splitCalendarYearRanges(previousYearDate(today), today);
  const records = await Promise.all(ranges.map(({ start, end }) => fetchTpexDistributionRange(start, end)));
  return dedupeDistributions(records.flat());
}

async function fetchTwseCloses() {
  const payload = await fetchJson("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL");
  return (Array.isArray(payload) ? payload : []).flatMap((row): CloseRecord[] => {
    const item = row as JsonRow;
    const code = String(item.Code ?? "").trim().toUpperCase();
    const price = numberValue(item.ClosingPrice);
    if (!code || price === null || price <= 0) return [];
    return [{ code, name: String(item.Name ?? code).trim(), exchange: "twse", price, date: isoDate(item.Date) }];
  });
}

async function fetchTpexCloses() {
  const payload = await fetchJson("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes");
  return (Array.isArray(payload) ? payload : []).flatMap((row): CloseRecord[] => {
    const item = row as JsonRow;
    const code = String(item.SecuritiesCompanyCode ?? "").trim().toUpperCase();
    const price = numberValue(item.Close);
    if (!code || price === null || price <= 0) return [];
    return [{ code, name: String(item.CompanyName ?? code).trim(), exchange: "tpex", price, date: isoDate(item.Date) }];
  });
}

function settledRows<T>(result: PromiseSettledResult<T[]>) {
  return result.status === "fulfilled" ? result.value : [];
}

function coversVerifiedMarket(
  distributions: DistributionRecord[],
  closes: CloseRecord[],
  snapshot: { rows: Array<{ code: string }> },
) {
  const distributionCodes = new Set(distributions.map((row) => row.code));
  const closeCodes = new Set(closes.map((row) => row.code));
  return snapshot.rows.every((row) => distributionCodes.has(row.code) && closeCodes.has(row.code));
}

async function loadPayload() {
  const today = taipeiDate();
  const cutoff = previousYearDate(today);
  const [twseDivResult, tpexDivResult, twseCloseResult, tpexCloseResult] = await Promise.allSettled([
    fetchTwseDistributions(today),
    fetchTpexDistributions(today),
    fetchTwseCloses(),
    fetchTpexCloses(),
  ]);
  const distributions = [...settledRows(twseDivResult), ...settledRows(tpexDivResult)];
  const closes = [...settledRows(twseCloseResult), ...settledRows(tpexCloseResult)];
  const distributionByKey = new Map<string, DistributionRecord[]>();
  distributions.forEach((record) => {
    const key = `${record.exchange}:${record.code}`;
    distributionByKey.set(key, [...(distributionByKey.get(key) ?? []), record]);
  });
  const closeByKey = new Map(closes.map((record) => [`${record.exchange}:${record.code}`, record]));
  const knownUniverse = new Map(
    (marketRankingSnapshot.rows as UniverseRow[])
      .filter((row) => row.market === "etf")
      .map((row) => [`${row.exchange}:${row.code}`, { code: row.code, name: row.name, exchange: row.exchange }]),
  );
  distributionByKey.forEach((records, key) => {
    if (closeByKey.has(key)) {
      const latest = records.at(0)!;
      knownUniverse.set(key, { code: latest.code, name: latest.name, exchange: latest.exchange });
    }
  });

  const liveRows = [...knownUniverse.entries()].flatMap(([key, security]): EtfResultRow[] => {
    const close = closeByKey.get(key);
    const records = distributionByKey.get(key) ?? [];
    if (!close || records.length === 0) return [];
    const recent = records.filter((record) => record.exDate >= cutoff && record.exDate <= today && record.amount !== null && record.amount > 0);
    const trailingAmount = recent.reduce((sum, record) => sum + (record.amount ?? 0), 0);
    const annualYield = trailingAmount / close.price * 100;
    if (!Number.isFinite(annualYield) || annualYield < 7) return [];
    const nextDistribution = records
      .filter((record) => record.paymentDate >= today)
      .sort((a, b) => a.paymentDate.localeCompare(b.paymentDate))[0] ?? null;
    const distributionMonths = [...new Set(recent.map((record) => Number(record.exDate.slice(5, 7))).filter(Number.isFinite))].sort((a, b) => a - b);
    const name = security.name || close.name || records[0]?.name || security.code;
    return [{
      code: security.code,
      name,
      exchange: security.exchange,
      category: category(name),
      annualYield: Math.round(annualYield * 100) / 100,
      trailingDistributionAmount: Math.round(trailingAmount * 10_000) / 10_000,
      distributionMonths,
      nextDistributionDate: nextDistribution?.paymentDate || null,
      nextDistributionAmount: nextDistribution?.amount ?? null,
      price: close.price,
      priceDate: close.date || null,
    }];
  });

  const officialTwseReady = twseDivResult.status === "fulfilled" && twseCloseResult.status === "fulfilled"
    && coversVerifiedMarket(twseDivResult.value, twseCloseResult.value, twseVerifiedSnapshot);
  const officialTpexReady = tpexDivResult.status === "fulfilled" && tpexCloseResult.status === "fulfilled"
    && coversVerifiedMarket(tpexDivResult.value, tpexCloseResult.value, tpexVerifiedSnapshot);
  const rowByKey = new Map(liveRows.map((row) => [`${row.exchange}:${row.code}`, row]));
  const fallbackRows = [
    ...(officialTwseReady ? [] : (twseVerifiedSnapshot.rows as EtfResultRow[])),
    ...(officialTpexReady ? [] : (tpexVerifiedSnapshot.rows as EtfResultRow[])),
  ];
  fallbackRows.forEach((row) => {
    const key = `${row.exchange}:${row.code}`;
    if (!rowByKey.has(key)) rowByKey.set(key, row);
  });
  const rows = [...rowByKey.values()].sort((a, b) => b.annualYield - a.annualYield);
  const addedFallbackCount = rows.length - liveRows.length;
  const priceCount = [...knownUniverse.keys()].filter((key) => closeByKey.has(key)).length + addedFallbackCount;
  const distributionCount = [...knownUniverse.keys()].filter((key) => distributionByKey.has(key)).length + addedFallbackCount;
  const ok = priceCount > 0 && distributionCount > 0;
  return {
    ok,
    updatedAt: new Date().toISOString(),
    threshold: 7,
    universeCount: knownUniverse.size,
    quoteCount: priceCount,
    distributionCount,
    rows,
    sources: {
      twse: officialTwseReady ? "official" : "verified-snapshot",
      tpex: officialTpexReady ? "official" : "verified-snapshot",
    },
    fallbackGeneratedAt: {
      twse: officialTwseReady ? null : twseVerifiedSnapshot.generatedAt,
      tpex: officialTpexReady ? null : tpexVerifiedSnapshot.generatedAt,
    },
    message: ok ? null : "ETF 官方殖利率資料暫時無法更新，請稍後再試。",
  };
}

export async function GET() {
  if (cache && cache.expiresAt > Date.now()) {
    return Response.json(cache.payload, { headers: { "Cache-Control": "public, max-age=60, s-maxage=300" } });
  }
  const payload = await loadPayload();
  if (payload.ok) {
    cache = { expiresAt: Date.now() + CACHE_MS, payload };
    return Response.json(payload, { headers: { "Cache-Control": "public, max-age=60, s-maxage=900, stale-while-revalidate=3600" } });
  }
  if (cache?.payload.ok) {
    return Response.json({ ...cache.payload, stale: true }, { headers: { "Cache-Control": "public, max-age=30, s-maxage=120" } });
  }
  return Response.json(payload, { status: 503, headers: { "Cache-Control": "no-store" } });
}
