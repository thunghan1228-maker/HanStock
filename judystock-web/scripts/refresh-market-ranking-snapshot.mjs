import { readFile, writeFile } from "node:fs/promises";

const outputPaths = process.argv.slice(2);
if (outputPaths.length === 0) throw new Error("Pass at least one market-ranking-snapshot.json path");

const flowKeys = ["foreign", "trust", "dealer", "hedge"];

function number(value) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function lots(value) {
  return Math.round((number(value) / 1000) * 10) / 10;
}

function classify(code, exchange) {
  if (/^00[0-9A-Z]{2,4}$/.test(code)) return "etf";
  if (/^[1-9][0-9]{3}[A-Z]?$/.test(code) || /^91[0-9]{4}$/.test(code)) return exchange;
  return null;
}

function key(row) {
  return `${row.exchange}:${row.code}`;
}

function percentileMap(rows, field) {
  const sorted = [...rows].sort((a, b) => a[field] - b[field]);
  const result = new Map();
  let index = 0;
  while (index < sorted.length) {
    let end = index;
    while (end + 1 < sorted.length && sorted[end + 1][field] === sorted[index][field]) end += 1;
    const score = sorted.length <= 1 ? 0 : (((index + end) / 2) / (sorted.length - 1)) * 200 - 100;
    for (let cursor = index; cursor <= end; cursor += 1) result.set(key(sorted[cursor]), Math.round(score * 10) / 10);
    index = end + 1;
  }
  return result;
}

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());
const compactDate = today.replaceAll("-", "");
const displayDate = today.replaceAll("-", "/");

const [twseResponse, tpexResponse] = await Promise.all([
  fetch(`https://www.twse.com.tw/rwd/zh/fund/T86?date=${compactDate}&selectType=ALLBUT0999&response=json`),
  fetch("https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"),
]);
if (!twseResponse.ok || !tpexResponse.ok) throw new Error(`Official fetch failed: TWSE ${twseResponse.status}, TPEx ${tpexResponse.status}`);

const twse = await twseResponse.json();
const tpex = await tpexResponse.json();
if (twse.stat !== "OK" || !Array.isArray(twse.fields) || !Array.isArray(twse.data)) throw new Error("TWSE latest data unavailable");
if (!Array.isArray(tpex) || tpex.length === 0) throw new Error("TPEx latest data unavailable");

const fieldIndex = (pattern) => twse.fields.findIndex((field) => pattern.test(String(field)));
const indexes = {
  code: fieldIndex(/證券代號/),
  name: fieldIndex(/證券名稱/),
  foreign: fieldIndex(/外陸資買賣超股數\(不含外資自營商\)/),
  trust: fieldIndex(/投信買賣超股數/),
  dealer: fieldIndex(/自營商買賣超股數\(自行買賣\)/),
  hedge: fieldIndex(/自營商買賣超股數\(避險\)/),
};

const rawRows = twse.data.flatMap((row) => {
  const code = String(row[indexes.code] ?? "").trim().toUpperCase();
  const market = classify(code, "twse");
  if (!market) return [];
  return [{
    code,
    name: String(row[indexes.name] ?? "").trim(),
    market,
    exchange: "twse",
    foreign: lots(row[indexes.foreign]),
    trust: lots(row[indexes.trust]),
    dealer: lots(row[indexes.dealer]),
    hedge: lots(row[indexes.hedge]),
  }];
});

const rocDate = `${Number(today.slice(0, 4)) - 1911}${today.slice(5, 7)}${today.slice(8, 10)}`;
for (const row of tpex) {
  if (String(row.Date ?? "") !== rocDate) continue;
  const code = String(row.SecuritiesCompanyCode ?? "").trim().toUpperCase();
  const market = classify(code, "tpex");
  if (!market) continue;
  rawRows.push({
    code,
    name: String(row.CompanyName ?? "").trim(),
    market,
    exchange: "tpex",
    foreign: lots(row["Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"]),
    trust: lots(row["SecuritiesInvestmentTrustCompanies-Difference"]),
    dealer: lots(row["Dealers-Difference"]),
    hedge: 0,
  });
}

const twseCount = rawRows.filter((row) => row.exchange === "twse").length;
const tpexCount = rawRows.filter((row) => row.exchange === "tpex").length;
if (twseCount < 700 || tpexCount < 600) throw new Error(`Incomplete official data: TWSE ${twseCount}, TPEx ${tpexCount}`);

const percentile = Object.fromEntries(flowKeys.map((field) => [field, percentileMap(rawRows, field)]));
const latestByKey = new Map(rawRows.map((row) => [key(row), {
  ...row,
  date: displayDate,
  foreign: percentile.foreign.get(key(row)) ?? 0,
  trust: percentile.trust.get(key(row)) ?? 0,
  dealer: percentile.dealer.get(key(row)) ?? 0,
  hedge: percentile.hedge.get(key(row)) ?? 0,
}]));

const template = JSON.parse(await readFile(outputPaths[0], "utf8"));
const previousByKey = new Map(template.rows.map((row) => [`${row.exchange}:${row.code}`, row]));
const rows = [];
for (const [rowKey, latest] of latestByKey) {
  const previous = previousByKey.get(rowKey);
  if (!previous?.series?.length) continue;
  const series = [latest, ...previous.series.filter((point) => point.date !== displayDate)].slice(0, 6)
    .map(({ date, foreign, trust, dealer, hedge }) => ({ date, foreign, trust, dealer, hedge }));
  if (series.length < 6) continue;
  rows.push({
    code: latest.code,
    name: latest.name,
    market: latest.market,
    exchange: latest.exchange,
    series,
  });
}

const coverage = rows.reduce((result, row) => {
  result[row.market] += 1;
  return result;
}, { twse: 0, tpex: 0, etf: 0 });
if (coverage.twse < 700 || coverage.tpex < 600) throw new Error(`Incomplete merged coverage: ${JSON.stringify(coverage)}`);

const fetchedAt = new Date().toISOString();
const snapshot = {
  ok: true,
  fetchedAt,
  dataDate: displayDate,
  rows,
  coverage: {
    total: rows.length,
    stocks: coverage.twse + coverage.tpex,
    twse: coverage.twse,
    tpex: coverage.tpex,
    etf: coverage.etf,
    tradingDays: 6,
    completeMarkets: true,
  },
  sources: ["TWSE 三大法人買賣超日報", "TPEx OpenAPI 最新日", "最近驗證快照（補齊舊交易日）"],
  snapshotGeneratedAt: fetchedAt,
};

const serialized = `${JSON.stringify(snapshot)}\n`;
await Promise.all(outputPaths.map((path) => writeFile(path, serialized)));
console.log(JSON.stringify({ dataDate: displayDate, rows: rows.length, coverage, fetchedAt }));
