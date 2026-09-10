import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [page, panel, route, engine, styles] = await Promise.all([
  readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8"),
  readFile(new URL("../app/stock-screener/MaScoreRankingPanel.tsx", import.meta.url), "utf8"),
  readFile(new URL("../app/api/ma-score-ranking/route.ts", import.meta.url), "utf8"),
  readFile(new URL("../lib/ma-score-ranking.ts", import.meta.url), "utf8"),
  readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
]);

test("the strategy screener exposes the complete MA score ranking workspace", () => {
  assert.match(page, /pageTab === "maScore"/);
  assert.match(page, /<strong>均線分數排行<\/strong>/);
  assert.match(page, /<MaScoreRankingPanel groups=\{groups\}/);
  assert.match(panel, /個股分數排行/);
  assert.match(panel, /前十名常客/);
  assert.match(panel, /族群分數前十/);
  assert.match(panel, /每天取/);
  assert.match(panel, /輸出/);
  assert.match(panel, /輸入代號、名稱或族群/);
  assert.match(panel, /查詢均線分數/);
  assert.match(panel, /所屬族群/);
  assert.match(panel, /同族群.*依均線總分由高到低重新排名/);
  assert.match(panel, /group-drilldown/);
  assert.match(panel, /<StockTradingBadges ticker=\{row\.code\} compact detailed \/>/);
  assert.match(panel, /<StockTradingBadges ticker=\{row\.leader\.code\} compact detailed \/>/);
  assert.match(styles, /\.ma-score-panel \.stock-trading-badges\.compact/);
});

test("the 15 point score keeps the reference breakdown and all comparison columns", () => {
  assert.match(engine, /const baseScore = aboveMaPeriods\.length \+ newHighPeriods\.length/);
  assert.match(engine, /if \(score === 15\) return 3/);
  assert.match(engine, /if \(score >= 12\) return 2/);
  assert.match(engine, /if \(score >= 9\) return 1/);
  for (const label of ["總分", "分數", "排列", "均線", "創新高", "收盤價", "族群", "昨日名次"]) assert.match(panel, new RegExp(label));
  assert.match(panel, /\[5, 10, 20, 60, 120, 240\]/);
  assert.match(panel, /\[5, 10, 20, 60, 120, 360\]/);
});

test("history and group ranking use official stored daily technical data", () => {
  assert.match(route, /readTechnicalMarketSnapshots\(390\)/);
  assert.match(route, /buildMaScoreRegulars/);
  assert.match(route, /previousScores/);
  assert.match(styles, /\.ma-score-stock-table\{min-width:1430px\}/);
  assert.match(styles, /@media\(max-width:900px\).*\.ma-score-table-scroll\{max-height:70vh\}/s);
});

test("popup rows show price position and MA score beside the stock name", () => {
  assert.match(styles, /\.early-signal-technical-badges\{grid-column:3;grid-row:1/);
});

test("the heavy historical ranking response is cached at the edge", () => {
  assert.match(route, /payloadCache/);
  assert.match(route, /readLatestMarketRanking/);
  assert.match(route, /saveLatestMarketRanking/);
  assert.match(route, /sourceFingerprint/);
  assert.doesNotMatch(route, /payloadBytes/);
  assert.match(route, /expiresAt: Date\.now\(\) \+ 30 \* 60_000/);
  assert.match(panel, /readBrowserCache/);
  assert.match(panel, /已顯示快取，背景確認最新資料/);
  assert.match(panel, /cache: "default"/);
  assert.doesNotMatch(panel, /setSelectedDate\(next\.dataDate\)/);
});

test("regular ranking prepares full-market history once per request", () => {
  assert.match(engine, /const \{ orderedSnapshots, byCode \} = prepareHistory\(snapshots\)/);
  assert.match(engine, /dailyRankings = dates\.map\(\(date\) => rankingFromHistory\(byCode, date\)/);
  assert.match(engine, /bars\.slice\(firstUsable, firstUsable \+ 360\)/);
});
