import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const route = readFileSync(new URL("../app/api/high-dividend-etfs/route.ts", import.meta.url), "utf8");
const panel = readFileSync(new URL("../app/HighDividendEtfPanel.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

test("high-dividend ETF ranking uses official distribution history and closing prices", () => {
  assert.match(route, /\/rwd\/zh\/ETF\/etfDiv/);
  assert.match(route, /info\.tpex\.org\.tw\/api\/etfExDiv/);
  assert.match(route, /exchangeReport\/STOCK_DAY_ALL/);
  assert.match(route, /tpex_mainboard_daily_close_quotes/);
  assert.match(route, /trailingAmount \/ close\.price \* 100/);
  assert.match(route, /splitCalendarYearRanges\(previousYearDate\(today\), today\)/);
  assert.match(route, /fetchTwseDistributionRange\(start, end\)/);
  assert.match(route, /fetchTpexDistributionRange\(start, end\)/);
  assert.match(route, /dedupeDistributions\(records\.flat\(\)\)/);
  assert.match(route, /tpexVerifiedSnapshot/);
  assert.match(route, /twseVerifiedSnapshot/);
  assert.match(route, /verified-snapshot/);
  assert.match(route, /officialTwseReady \? \[\] : \(twseVerifiedSnapshot\.rows/);
  assert.match(route, /for \(let attempt = 0; attempt < 2; attempt \+= 1\)/);
  assert.match(route, /coversVerifiedMarket\(twseDivResult\.value, twseCloseResult\.value/);
  assert.match(route, /return await response\.json\(\) as unknown/);
  assert.doesNotMatch(route, /finance\.yahoo\.com/);
});

test("TWSE fallback keeps the requested qualifying ETFs when its upstream is unavailable", () => {
  const snapshot = JSON.parse(readFileSync(new URL("../app/data/high-dividend-etf-twse-snapshot.json", import.meta.url), "utf8"));
  const codes = new Set(snapshot.rows.map((row) => row.code));
  for (const code of ["00881", "00891", "00918", "00927", "00919", "00982A", "00961"]) assert.ok(codes.has(code), `missing ${code}`);
  assert.ok(snapshot.rows.every((row) => row.exchange === "twse" && row.annualYield >= 7));
});

test("high-dividend ETF ranking keeps the requested controls and fields", () => {
  assert.match(panel, /殖利率 \{sort === "desc"/);
  assert.match(panel, /配息月份/);
  assert.match(panel, /最近預定配息/);
  assert.match(panel, /編輯名單/);
  assert.match(panel, /onOpenKline\(row\.code, row\.name\)/);
  assert.match(panel, /fetch\("\/api\/high-dividend-etfs"/);
  assert.match(panel, /cache: "default"/);
  assert.match(panel, /完整符合 \$\{rows\.length\} 檔/);
  assert.match(panel, /此裝置顯示 \$\{visibleRows\.length\} 檔/);
  assert.ok(panel.indexOf("className=\"etf-price\"") < panel.indexOf("className=\"etf-type\""));
  assert.match(panel, /<span>成交價<\/span><span>ETF 類型<\/span>/);
});

test("desktop high-dividend ETF ranking spans both columns with readable fields", () => {
  assert.match(page, /className="desktop-high-dividend-wide"/);
  assert.match(panel, /className="etf-price"/);
  assert.match(styles, /\.desktop-high-dividend-wide \{[^}]*grid-column: 1 \/ -1/);
  assert.match(styles, /\.desktop-high-dividend-wide \.high-dividend-etf-row \{[^}]*grid-template-columns:[^}]*minmax\(120px, \.65fr\)/);
  assert.match(styles, /\.desktop-high-dividend-wide \.high-dividend-etf-row \.etf-price \{ font-size: 18px/);
});

test("mobile high-dividend ETF ranking pins rank and stock identity while details scroll", () => {
  assert.match(styles, /\.high-dividend-etf-table \{ position: relative; isolation: isolate; -webkit-overflow-scrolling: touch/);
  assert.match(styles, /\.high-dividend-etf-row > :nth-child\(-n \+ 2\) \{ position: sticky/);
  assert.match(styles, /\.high-dividend-etf-row > :nth-child\(1\) \{ left: 0/);
  assert.match(styles, /\.high-dividend-etf-row > :nth-child\(2\) \{ left: 48px/);
  assert.match(styles, /\.high-dividend-etf-row\.is-head > :nth-child\(-n \+ 2\)/);
});

test("all six focus group headings use full-row direction colors", () => {
  assert.match(styles, /battle-shell\.is-strong \.focus-stock-group-heading\{[^}]*background:#a82730/);
  assert.match(styles, /battle-shell\.is-weak \.focus-stock-group-heading\{[^}]*background:#147344/);
  assert.match(styles, /focus-stock-group-heading>span,[^}]*focus-stock-group-heading>strong,[^}]*focus-stock-group-heading>b\{color:#fff!important/);
});
