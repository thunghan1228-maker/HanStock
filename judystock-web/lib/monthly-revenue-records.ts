import baselineJson from "../app/data/monthly-revenue-baseline.json" with { type: "json" };

export type RevenueMarket = "twse" | "tpex";
export type RevenueSignalKind = "all-time-high" | "rolling-12-high" | "all-time-low" | "rolling-12-low";

export type MonthlyRevenueRecord = {
  revenueMonth: string;
  sourcePublishedDate: string;
  stockCode: string;
  name: string;
  market: RevenueMarket;
  revenue: number;
  previousMonthRevenue: number | null;
  previousYearRevenue: number | null;
  momPct: number | null;
  yoyPct: number | null;
};

export type StoredRevenueObservation = {
  revenueMonth: string;
  stockCode: string;
  revenue: number;
};

export type RevenueSignalDraft = MonthlyRevenueRecord & {
  signalKind: RevenueSignalKind;
  comparisonRevenue: number;
  comparisonMonth: string;
  historyMonths: number;
};

type BaselineStock = {
  name: string;
  market: RevenueMarket;
  historyCount: number;
  maxRevenue: number;
  maxMonth: string;
  minRevenue: number;
  minMonth: string;
  recent: [string, number][];
};

type RevenueBaseline = {
  generatedAt: string;
  historyStart: string;
  historyEnd: string;
  stockCount: number;
  sourceRows: number;
  sources: string[];
  stocks: Record<string, BaselineStock>;
};

export const monthlyRevenueBaseline = baselineJson as unknown as RevenueBaseline;
export const REVENUE_ALERT_HISTORY_START = "2026-08-26";

function stripHtml(value: string) {
  return value
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&nbsp;|&#160;/gi, " ")
    .trim();
}

function toNumber(value: string) {
  const normalized = stripHtml(value).replace(/[,\s]/g, "").trim();
  if (!normalized || normalized === "-") return null;
  const parenthesized = /^\(.*\)$/.test(normalized);
  const parsed = Number(normalized.replace(/^\(|\)$/g, ""));
  if (!Number.isFinite(parsed)) return null;
  return parenthesized ? -Math.abs(parsed) : parsed;
}

export function taipeiYearMonth(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(now);
  const year = Number(parts.find((part) => part.type === "year")?.value);
  const month = Number(parts.find((part) => part.type === "month")?.value);
  return { year, month };
}

export function currentRevenueTarget(now = new Date()) {
  const current = taipeiYearMonth(now);
  const date = new Date(Date.UTC(current.year, current.month - 2, 1));
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth() + 1;
  return {
    revenueMonth: `${year}-${String(month).padStart(2, "0")}`,
    rocYear: year - 1911,
    month,
  };
}

function parsePublishedDate(html: string) {
  const match = html.match(/出表日期[：:]\s*(\d{2,3})\/(\d{1,2})\/(\d{1,2})/);
  if (!match) return null;
  return `${Number(match[1]) + 1911}-${String(Number(match[2])).padStart(2, "0")}-${String(Number(match[3])).padStart(2, "0")}`;
}

export function parseMopsMonthlyRevenueHtml(
  html: string,
  market: RevenueMarket,
  revenueMonth: string,
  fallbackPublishedDate: string,
) {
  const sourcePublishedDate = parsePublishedDate(html) ?? fallbackPublishedDate;
  const rows: MonthlyRevenueRecord[] = [];
  for (const rowMatch of html.matchAll(/<tr\s+align=right>([\s\S]*?)<\/tr>/gi)) {
    const cells = [...rowMatch[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)].map((match) => stripHtml(match[1]));
    if (cells.length < 7) continue;
    const stockCode = cells[0].replace(/\s/g, "");
    if (!/^[0-9]{4,6}[A-Z]?$/.test(stockCode)) continue;
    const revenue = toNumber(cells[2]);
    if (revenue === null) continue;
    rows.push({
      revenueMonth,
      sourcePublishedDate,
      stockCode,
      name: cells[1],
      market,
      revenue: Math.round(revenue),
      previousMonthRevenue: toNumber(cells[3]),
      previousYearRevenue: toNumber(cells[4]),
      momPct: toNumber(cells[5]),
      yoyPct: toNumber(cells[6]),
    });
  }
  return { sourcePublishedDate, rows };
}

export async function fetchCurrentOfficialMonthlyRevenue(now = new Date()) {
  const target = currentRevenueTarget(now);
  const fallbackPublishedDate = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);

  const markets: { market: RevenueMarket; path: string }[] = [
    { market: "twse", path: "sii" },
    { market: "tpex", path: "otc" },
  ];
  const responses = await Promise.all(markets.map(async ({ market, path }) => {
    const url = `https://mopsov.twse.com.tw/nas/t21/${path}/t21sc03_${target.rocYear}_${target.month}_0.html`;
    const response = await fetch(url, {
      headers: {
        Accept: "text/html,application/xhtml+xml",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "User-Agent": "Mozilla/5.0 HanStock monthly revenue monitor/1.0",
      },
      signal: AbortSignal.timeout(45_000),
    });
    if (!response.ok) throw new Error(`mops_${market}_http_${response.status}`);
    const bytes = await response.arrayBuffer();
    const html = new TextDecoder("big5").decode(bytes);
    const parsed = parseMopsMonthlyRevenueHtml(html, market, target.revenueMonth, fallbackPublishedDate);
    return { ...parsed, market, source: url };
  }));

  const rows = responses.flatMap((result) => result.rows);
  const sourcePublishedDate = responses.map((result) => result.sourcePublishedDate).sort().at(-1) ?? fallbackPublishedDate;
  return {
    revenueMonth: target.revenueMonth,
    sourcePublishedDate,
    rows,
    sources: responses.map((result) => result.source),
    coverage: {
      total: rows.length,
      twse: rows.filter((row) => row.market === "twse").length,
      tpex: rows.filter((row) => row.market === "tpex").length,
    },
  };
}

function observationMap(stockCode: string, stored: StoredRevenueObservation[]) {
  const points = new Map<string, number>(monthlyRevenueBaseline.stocks[stockCode]?.recent ?? []);
  for (const row of stored) {
    if (row.stockCode === stockCode) points.set(row.revenueMonth, row.revenue);
  }
  return points;
}

export function classifyMonthlyRevenueRecord(
  record: MonthlyRevenueRecord,
  stored: StoredRevenueObservation[],
): RevenueSignalDraft[] {
  const baseline = monthlyRevenueBaseline.stocks[record.stockCode];
  const storedPrior = stored.filter((row) => row.stockCode === record.stockCode && row.revenueMonth < record.revenueMonth);
  if (!baseline && !storedPrior.length) return [];

  let allTimeMax = baseline?.maxRevenue ?? Number.NEGATIVE_INFINITY;
  let allTimeMaxMonth = baseline?.maxMonth ?? "";
  let allTimeMin = baseline?.minRevenue ?? Number.POSITIVE_INFINITY;
  let allTimeMinMonth = baseline?.minMonth ?? "";
  for (const row of storedPrior) {
    if (row.revenue > allTimeMax) {
      allTimeMax = row.revenue;
      allTimeMaxMonth = row.revenueMonth;
    }
    if (row.revenue < allTimeMin) {
      allTimeMin = row.revenue;
      allTimeMinMonth = row.revenueMonth;
    }
  }

  const points = [...observationMap(record.stockCode, storedPrior).entries()]
    .filter(([month]) => month < record.revenueMonth)
    .sort(([left], [right]) => right.localeCompare(left));
  const rolling = points.slice(0, 12);
  const rollingMax = rolling.reduce((best, point) => point[1] > best[1] ? point : best, rolling[0] ?? ["", Number.NEGATIVE_INFINITY]);
  const rollingMin = rolling.reduce((best, point) => point[1] < best[1] ? point : best, rolling[0] ?? ["", Number.POSITIVE_INFINITY]);
  const historyMonths = (baseline?.historyCount ?? 0) + storedPrior.length;
  const signals: RevenueSignalDraft[] = [];

  if (Number.isFinite(allTimeMax) && record.revenue > allTimeMax) {
    signals.push({ ...record, signalKind: "all-time-high", comparisonRevenue: allTimeMax, comparisonMonth: allTimeMaxMonth, historyMonths });
  }
  if (rolling.length >= 12 && record.revenue > rollingMax[1]) {
    signals.push({ ...record, signalKind: "rolling-12-high", comparisonRevenue: rollingMax[1], comparisonMonth: rollingMax[0], historyMonths: rolling.length });
  }
  if (Number.isFinite(allTimeMin) && record.revenue < allTimeMin) {
    signals.push({ ...record, signalKind: "all-time-low", comparisonRevenue: allTimeMin, comparisonMonth: allTimeMinMonth, historyMonths });
  }
  if (rolling.length >= 12 && record.revenue < rollingMin[1]) {
    signals.push({ ...record, signalKind: "rolling-12-low", comparisonRevenue: rollingMin[1], comparisonMonth: rollingMin[0], historyMonths: rolling.length });
  }
  return signals;
}

export function signalKindLabel(kind: RevenueSignalKind) {
  if (kind === "all-time-high") return "歷史單月新高";
  if (kind === "rolling-12-high") return "近 12 月新高";
  if (kind === "all-time-low") return "歷史單月新低";
  return "近 12 月新低";
}
