import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { futureStatusLabel, parseTaifexStockFutures, parseTpexDayTradeCsv, parseTpexDayTradeRows, parseTpexMarginCsv, parseTpexMarginRows, parseTwseDayTradeRows, parseTwseMarginRows } from "../lib/stock-trading-status.ts";

test("parses official margin quotas and O/X suspension markers", () => {
  const twse = parseTwseMarginRows([
    { 股票代號: "2330", 融資限額: "100,000", 融券限額: "100,000", 註記: "" },
    { 股票代號: "2317", 融資限額: "100,000", 融券限額: "100,000", 註記: "OX" },
  ]);
  const tpex = parseTpexMarginRows([
    { SecuritiesCompanyCode: "6488", MarginPurchaseQuota: "50000", ShortSaleQuota: "50000", Note: "X" },
  ]);
  assert.equal(twse.get("2330")?.margin, "available");
  assert.equal(twse.get("2330")?.short, "available");
  assert.equal(twse.get("2317")?.margin, "unavailable");
  assert.equal(twse.get("2317")?.short, "unavailable");
  assert.equal(tpex.get("6488")?.margin, "available");
  assert.equal(tpex.get("6488")?.short, "unavailable");
});

test("parses the official TPEx UTF-8 CSV fallback without losing quoted quotas", () => {
  const rows = parseTpexMarginCsv(`上櫃股票融資融券餘額\n資料日期:115/09/02\n"代號","名稱","前資餘額(張)","資買","資賣","現償","資餘額","資屬證金","資使用率(%)","資限額","前券餘額(張)","券賣","券買","券償","券餘額","券屬證金","券使用率(%)","券限額","資券相抵(張)","備註"\n"4157","太景*-KY","1,234","1","2","0","1,233","0","1.20","10,000","8","0","1","0","7","0","0.01","10,000","0",""\n"4971","IET-KY","0","0","0","0","0","0","0.0","20,000","0","0","0","0","0","0","0.0","20,000","0","OX"`);
  assert.deepEqual(rows.get("4157"), { margin: "available", short: "available", note: "" });
  assert.deepEqual(rows.get("4971"), { margin: "unavailable", short: "unavailable", note: "OX" });
});

test("merges normal and mini stock-future rows by underlying code", () => {
  const futures = parseTaifexStockFutures(`<table>
    <tr><td>CD</td><td>1</td><td>台積電</td><td>2330</td><td>○</td><td>○</td></tr>
    <tr><td>QF</td><td>1</td><td>台積電</td><td>2330</td><td>◎</td><td></td></tr>
    <tr><td>SC</td><td>3</td><td>聚陽</td><td>1477</td><td>◎</td><td></td></tr>
  </table>`);
  assert.equal(futureStatusLabel(futures.get("2330")), "（有股期、有小型期貨）");
  assert.equal(futureStatusLabel(futures.get("1477")), "（有小型期貨）");
});

test("parses official TWSE and TPEx cash day-trading target lists", () => {
  const twse = parseTwseDayTradeRows([
    { Code: "2330", Name: "台積電" },
    { 證券代號: "2317", 證券名稱: "鴻海" },
  ]);
  const tpex = parseTpexDayTradeRows([
    { SecuritiesCompanyCode: "6488", CompanyName: "環球晶" },
    { 證券代號: "3675", 證券名稱: "德微" },
  ]);
  assert.equal(twse.has("2330"), true);
  assert.equal(twse.has("2317"), true);
  assert.equal(tpex.has("6488"), true);
  assert.equal(tpex.has("3675"), true);
  const csv = parseTpexDayTradeCsv('資料日期,證券代號,證券名稱,註記\n1150901,"6488",環球晶,\n1150901,"3675",德微,');
  assert.equal(csv.has("6488"), true);
  assert.equal(csv.has("3675"), true);
});

test("adds official trading-status badges to screening, live-signal and K-line views", async () => {
  const [component, endpoint, home, screener, kline, styles] = await Promise.all([
    readFile(new URL("../app/StockTradingBadges.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/stock-trading-status/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(component, /可融資/);
  assert.match(component, /無融券/);
  assert.match(component, /status\.disposition/);
  assert.match(component, /status\.disposition === "處置中" \? " disposition-flash"/);
  assert.match(component, /可現股當沖/);
  assert.match(component, /不可現股當沖/);
  assert.match(component, /當沖待補/);
  assert.match(component, /有股期/);
  assert.match(component, /無股期/);
  assert.match(component, /有小型期貨/);
  assert.match(component, /無小型期貨/);
  assert.match(component, /融資查核中/);
  assert.doesNotMatch(component, />資\{/);
  assert.match(component, /stock-trading-badges compact dense/);
  assert.match(component, /hanstock:stock-trading-status:v3/);
  assert.match(component, /window\.localStorage\.setItem/);
  assert.match(component, /visibleStatus = resolved/);
  assert.match(component, /STATUS_RETRY_MS = 5_000/);
  assert.match(component, /STATUS_PARTIAL_REFRESH_MS = 10_000/);
  assert.match(component, /mergeStatus/);
  assert.match(component, /&refresh=1/);
  assert.match(component, /filter\(needsRetry\)/);
  assert.match(component, /小型期貨查核中/);
  assert.match(component, /cache: "no-store"/);
  assert.match(endpoint, /openapi\.twse\.com\.tw/);
  assert.match(endpoint, /TWTB4U/);
  assert.match(endpoint, /tpex_securities/);
  assert.match(endpoint, /margin\/balance\?response=csv-u8/);
  assert.match(endpoint, /intraday_trading_list_result/);
  assert.match(endpoint, /r\.jina\.ai/);
  assert.match(endpoint, /dayTradeAvailability/);
  assert.match(endpoint, /taifex\.com\.tw/);
  assert.match(endpoint, /SOURCE_CACHE_MS = 30 \* 60_000/);
  assert.match(endpoint, /PARTIAL_SOURCE_CACHE_MS = 10_000/);
  assert.match(endpoint, /complete \? "public, max-age=300, s-maxage=1800, stale-while-revalidate=86400" : "no-store"/);
  assert.match(endpoint, /sourceLoadPromise/);
  assert.match(endpoint, /s-maxage=1800/);
  assert.match(home, /<StockTradingBadges ticker=\{item\.ticker\}/);
  assert.match(home, /<StockTradingBadges ticker=\{row\.ticker\} compact dense trailingBadge=/);
  assert.match(home, /<StockTradingBadges ticker=\{ticker\} compact detailed \/>/);
  assert.match(home, /focus-stock-identity/);
  assert.match(screener, /<StockTradingBadges ticker=\{row\.code\}/);
  assert.match(kline, /<StockTradingBadges ticker=\{ticker\}/);
  assert.match(styles, /early-sell-toast-stock > \.stock-trading-badges\.compact/);
  assert.match(styles, /@keyframes disposition-flash-five/);
  assert.match(styles, /animation:disposition-flash-five \.52s ease-in-out 5/);
  assert.match(styles, /i\.disposition\.disposition-flash\{[^}]*color:#c31d28;[^}]*background:#fff/);
  assert.match(styles, /early-sell-toast-stock[^}]*i\.disposition\.disposition-flash\{[^}]*color:#c31d28;[^}]*background:#fff/);
  assert.match(styles, /prefers-reduced-motion:reduce[^}]*\.stock-trading-badges>i\.disposition\.disposition-flash/);
  assert.match(styles, /focus-stock-list \.stock-trading-badges\.compact > i/);
  assert.match(styles, /data-hanstock-device="ipad"\] \.focus-stock-list \.stock-trading-badges\.compact > i/);
  assert.match(styles, /data-hanstock-device="ipad"\] \.focus-stock-groups \{ grid-template-columns: repeat\(2/);
  assert.match(styles, /font-size:14px!important/);
  assert.match(component, /className=\{`margin \$\{/);
  assert.match(component, /className=\{`short \$\{/);
  assert.match(styles, /i\.margin\.available[^}]*color:#fff[^}]*background:#6936a6/);
  assert.match(styles, /i\.short\.available[^}]*color:#fff[^}]*background:#6936a6/);
  assert.match(styles, /i\.day-trade\.available[^}]*color:#fff[^}]*background:#9b7200/);
  assert.match(styles, /i\.margin\.pending[^}]*color:#fff[^}]*background:#c54673/);
  assert.match(styles, /fundamental-trading-cell \.stock-trading-badges\.dense/);
});
