import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const startYear = Number(process.env.REVENUE_BASELINE_START_YEAR ?? 100);
const startMonth = Number(process.env.REVENUE_BASELINE_START_MONTH ?? 1);
const endYear = Number(process.env.REVENUE_BASELINE_END_YEAR ?? 115);
const endMonth = Number(process.env.REVENUE_BASELINE_END_MONTH ?? 7);
const concurrency = Math.max(1, Math.min(12, Number(process.env.REVENUE_BASELINE_CONCURRENCY ?? 8)));
const outputPath = resolve(process.env.REVENUE_BASELINE_OUTPUT ?? "app/data/monthly-revenue-baseline.json");

function monthKey(rocYear, month) {
  return `${rocYear + 1911}-${String(month).padStart(2, "0")}`;
}

function monthIndex(rocYear, month) {
  return (rocYear + 1911) * 12 + month - 1;
}

function decodeEntity(value) {
  return value
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&nbsp;|&#160;/gi, " ")
    .trim();
}

function parseRevenue(value) {
  const normalized = value.replace(/<[^>]+>/g, "").replace(/[,\s]/g, "").trim();
  if (!normalized || normalized === "-") return null;
  const parenthesized = /^\(.*\)$/.test(normalized);
  const parsed = Number(normalized.replace(/^\(|\)$/g, ""));
  if (!Number.isFinite(parsed)) return null;
  return Math.round(parenthesized ? -Math.abs(parsed) : parsed);
}

function parseRows(html, market, revenueMonth) {
  const rows = [];
  const pattern = /<tr\s+align=right><td\s+align=center>([^<]+)<\/td><td\s+align=left>([^<]+)<\/td><td\s+nowrap>([^<]+)<\/td>/gi;
  for (const match of html.matchAll(pattern)) {
    const code = decodeEntity(match[1]).replace(/\s/g, "");
    if (!/^[0-9]{4,6}[A-Z]?$/.test(code)) continue;
    const revenue = parseRevenue(match[3]);
    if (revenue === null) continue;
    rows.push({
      code,
      name: decodeEntity(match[2]),
      market,
      revenueMonth,
      revenue,
    });
  }
  return rows;
}

async function fetchMonth({ rocYear, month, market }) {
  const marketPath = market === "twse" ? "sii" : "otc";
  const url = `https://mopsov.twse.com.tw/nas/t21/${marketPath}/t21sc03_${rocYear}_${month}_0.html`;
  let lastError;
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: "text/html,application/xhtml+xml",
          "Accept-Language": "zh-TW,zh;q=0.9",
          "User-Agent": "HanStock monthly revenue baseline builder/1.0",
        },
        signal: AbortSignal.timeout(45_000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const bytes = await response.arrayBuffer();
      const html = new TextDecoder("big5").decode(bytes);
      return {
        url,
        rows: parseRows(html, market, monthKey(rocYear, month)),
      };
    } catch (error) {
      lastError = error;
      if (attempt < 4) await new Promise((resolveDelay) => setTimeout(resolveDelay, 500 * attempt));
    }
  }
  throw new Error(`${url}: ${lastError instanceof Error ? lastError.message : String(lastError)}`);
}

const jobs = [];
for (let year = startYear; year <= endYear; year += 1) {
  const firstMonth = year === startYear ? startMonth : 1;
  const lastMonth = year === endYear ? endMonth : 12;
  for (let month = firstMonth; month <= lastMonth; month += 1) {
    jobs.push({ rocYear: year, month, market: "twse" });
    jobs.push({ rocYear: year, month, market: "tpex" });
  }
}

const results = new Array(jobs.length);
let nextJob = 0;
let completed = 0;

async function worker() {
  while (nextJob < jobs.length) {
    const index = nextJob;
    nextJob += 1;
    const job = jobs[index];
    results[index] = await fetchMonth(job);
    completed += 1;
    if (completed % 20 === 0 || completed === jobs.length) {
      process.stdout.write(`Fetched ${completed}/${jobs.length} official monthly files\n`);
    }
  }
}

await Promise.all(Array.from({ length: concurrency }, () => worker()));

const lastIndex = monthIndex(endYear, endMonth);
const recentStartIndex = lastIndex - 17;
const stocks = new Map();
const seenStockMonths = new Set();
let sourceRows = 0;

for (const result of results) {
  for (const row of result.rows) {
    const stockMonthKey = `${row.revenueMonth}:${row.code}`;
    if (seenStockMonths.has(stockMonthKey)) continue;
    seenStockMonths.add(stockMonthKey);
    sourceRows += 1;
    const existing = stocks.get(row.code) ?? {
      name: row.name,
      market: row.market,
      historyCount: 0,
      maxRevenue: row.revenue,
      maxMonth: row.revenueMonth,
      minRevenue: row.revenue,
      minMonth: row.revenueMonth,
      recent: [],
    };
    existing.name = row.name || existing.name;
    existing.market = row.market;
    existing.historyCount += 1;
    if (row.revenue > existing.maxRevenue) {
      existing.maxRevenue = row.revenue;
      existing.maxMonth = row.revenueMonth;
    }
    if (row.revenue < existing.minRevenue) {
      existing.minRevenue = row.revenue;
      existing.minMonth = row.revenueMonth;
    }
    const [yearText, monthText] = row.revenueMonth.split("-");
    const index = Number(yearText) * 12 + Number(monthText) - 1;
    if (index >= recentStartIndex) existing.recent.push([row.revenueMonth, row.revenue]);
    stocks.set(row.code, existing);
  }
}

const sortedStocks = Object.fromEntries([...stocks.entries()]
  .sort(([left], [right]) => left.localeCompare(right))
  .map(([code, stock]) => [code, {
    ...stock,
    recent: stock.recent.sort((left, right) => left[0].localeCompare(right[0])),
  }]));

const output = {
  generatedAt: new Date().toISOString(),
  historyStart: monthKey(startYear, startMonth),
  historyEnd: monthKey(endYear, endMonth),
  stockCount: Object.keys(sortedStocks).length,
  sourceRows,
  sources: [
    "https://mopsov.twse.com.tw/nas/t21/sii/",
    "https://mopsov.twse.com.tw/nas/t21/otc/",
  ],
  stocks: sortedStocks,
};

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(output)}\n`, "utf8");
process.stdout.write(`Wrote ${outputPath} (${output.stockCount} stocks, ${sourceRows} rows)\n`);
