import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  buildFundamentalRiverPoints,
  buildQuarterFundamentals,
  estimateFundamentalWaterline,
  fundamentalMedian,
  fundamentalRiverPosition,
  quarterEffectiveDate,
} from "../lib/fundamental-river.ts";

test("splits the peer valuation river into six unambiguous positions", () => {
  assert.equal(fundamentalRiverPosition(61.79, 100), "跌破特價");
  assert.equal(fundamentalRiverPosition(61.8, 100), "特價");
  assert.equal(fundamentalRiverPosition(80, 100), "便宜");
  assert.equal(fundamentalRiverPosition(100, 100), "貴");
  assert.equal(fundamentalRiverPosition(120, 100), "昂貴");
  assert.equal(fundamentalRiverPosition(138.2, 100), "突破昂貴");
});

test("uses robust peer medians and falls back from PE to PB for losses", () => {
  assert.equal(fundamentalMedian([9, 11, 13, 300]), 12);
  assert.deepEqual(estimateFundamentalWaterline({ close: 293, pe: 32.9, pb: 2.14, ttmEps: 8.9, groupMedianPe: 41.19, groupMedianPb: 2.5 }), { waterline: 366.591, basis: "pe" });
  assert.deepEqual(estimateFundamentalWaterline({ close: 80, pe: null, pb: 2, ttmEps: -1, groupMedianPe: 20, groupMedianPb: 1.5 }), { waterline: 60, basis: "pb" });
});

test("builds standalone quarterly EPS, TTM EPS, margin, and delayed effective dates", () => {
  const rows = [
    ["2025-09-30", "EPS", 2.18], ["2025-12-31", "EPS", 1.79], ["2026-03-31", "EPS", 1.69],
    ["2026-06-30", "EPS", 3.24], ["2026-06-30", "Revenue", 100], ["2026-06-30", "OperatingIncome", 10.3],
  ].map(([date, type, value]) => ({ date, type, value }));
  const quarters = buildQuarterFundamentals(rows);
  assert.equal(quarters.at(-1).label, "26Q2");
  assert.equal(quarters.at(-1).ttmEps, 8.9);
  assert.equal(quarters.at(-1).operatingMargin, 10.3);
  assert.equal(quarterEffectiveDate("2026-06-30"), "2026-08-29");
});

test("moves the river only after a report becomes public and never backfills future data", () => {
  const quarters = buildQuarterFundamentals([
    ["2025-03-31", "EPS", 1], ["2025-06-30", "EPS", 1], ["2025-09-30", "EPS", 1],
    ["2025-12-31", "EPS", 1], ["2026-03-31", "EPS", 2],
  ].map(([date, type, value]) => ({ date, type, value })));
  const points = buildFundamentalRiverPoints([
    { date: "2026/04/01", close: 80 },
    { date: "2026/05/14", close: 90 },
    { date: "2026/05/15", close: 95 },
  ], quarters, 20, null);
  assert.equal(points.length, 3);
  assert.equal(points[0].waterline, 80);
  assert.equal(points[1].waterline, 80);
  assert.equal(points[2].waterline, 100);
  assert.deepEqual(buildFundamentalRiverPoints([{ date: "2025/01/01", close: 50 }], quarters, 20, null), []);
});

test("wires the full reference feature set into the stock research center", async () => {
  const [page, panel, route, migration] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/FundamentalRiverPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/fundamental-river/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0019_lean_millenium_guard.sql", import.meta.url), "utf8"),
  ]);
  assert.match(page, /基本面河流圖/);
  assert.match(page, /<FundamentalRiverWorkspace/);
  for (const label of ["三個月", "六個月", "長時間", "本益比（近4季）", "股價淨值比", "單季 EPS", "近月營收", "營益率", "今日強勢前 20 大個股", "本週新公布財報", "股票代號", "股票名稱", "交易資訊", "均線分數 ≥10 × 基本面便宜區"]) assert.match(panel, new RegExp(label));
  assert.doesNotMatch(panel, /熱門查詢排行/);
  assert.match(panel, /\/api\/live-ranking/);
  assert.match(panel, /<StockTradingBadges ticker=\{row\.ticker\} dense \/>/);
  assert.match(route, /WEEKLY_REPORTS/);
  assert.match(route, /mode === "ranking"/);
  assert.match(route, /TaiwanStockFinancialStatements/);
  assert.match(route, /TaiwanStockMonthRevenue/);
  assert.match(route, /tpex_mainboard_peratio_analysis/);
  assert.match(route, /readTechnicalMarketIndicators/);
  assert.match(migration, /fundamental_river_queries/);
  assert.match(migration, /fundamental_river_snapshots/);
});
