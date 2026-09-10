import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildValuationRiverPoints, valuationDistanceFilterMatches, valuationMedian, valuationRiverPosition } from "../lib/valuation-river.ts";

const technicalRoutePath = new URL("../app/api/technical-market/route.ts", import.meta.url);
const panelsPath = new URL("../app/StockResearchPanels.tsx", import.meta.url);

test("uses one 160-session valuation river in both the market table and stock detail", async () => {
  const [route, panels] = await Promise.all([
    readFile(technicalRoutePath, "utf8"),
    readFile(panelsPath, "utf8"),
  ]);

  assert.match(route, /VALUATION_RIVER_WINDOW, valuationMedian, valuationRiverPosition/);
  assert.match(route, /valuationMedian\(closes\.slice\(0, VALUATION_RIVER_WINDOW\)\)/);
  assert.match(route, /valuationModelVersion: VALUATION_RIVER_MODEL_VERSION/);
  assert.match(route, /valuationWindow: VALUATION_RIVER_WINDOW/);
  assert.match(route, /cachedValuationIsCurrent/);
  assert.match(route, /const snapshotValuationIsCurrent = snapshot\?\.valuationModelVersion === VALUATION_RIVER_MODEL_VERSION && snapshot\.valuationWindow === VALUATION_RIVER_WINDOW && snapshot\.riverBase !== null/);
  assert.match(route, /const valuationSource = cachedValuationIsCurrent \? row : snapshotValuationIsCurrent \? snapshot : null/);
  assert.match(route, /row\.payload\.valuationModelVersion === VALUATION_RIVER_MODEL_VERSION/);
  assert.match(route, /row\.payload\.valuationWindow === VALUATION_RIVER_WINDOW/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("fast"\) === "1"/);
  assert.match(route, /fastIndicatorCache/);
  assert.match(route, /snapshotCacheHit: true/);
  assert.match(panels, /至少低於分水嶺 10% 才列為便宜/);
  assert.match(panels, /正在建立近 160 個交易日價格位置區間/);
  assert.match(panels, /\/api\/technical-market\?fast=1&quoteRev=latest-trading-day-v1/);
  assert.match(panels, /TECHNICAL_SNAPSHOT_STORAGE_KEY/);
  assert.match(panels, /readTechnicalSnapshot/);
  assert.doesNotMatch(panels, /const backfillMarket = async/);
  assert.match(panels, /全市場歷史價位位置均線選股/);
  assert.match(panels, /歷史價位位置均線圖/);
  assert.match(panels, /不使用 EPS/);
  assert.doesNotMatch(route, /closes\.length >= 20 \? median\(closes\.slice\(0, 20\)\)/);
});

test("requires a ten-percent discount before assigning the cheap label", () => {
  assert.equal(valuationRiverPosition(89.99, 100), "便宜");
  assert.equal(valuationRiverPosition(90, 100), "偏貴");
  assert.equal(valuationRiverPosition(99.4, 100), "偏貴");
  assert.equal(valuationRiverPosition(120, 100), "昂貴");
});

test("splits valuation candidates into non-overlapping distance-strength filters", () => {
  assert.equal(valuationDistanceFilterMatches(5, "near"), true);
  assert.equal(valuationDistanceFilterMatches(-5.01, "clear"), true);
  assert.equal(valuationDistanceFilterMatches(15, "clear"), true);
  assert.equal(valuationDistanceFilterMatches(-15.01, "strong"), true);
  assert.equal(valuationDistanceFilterMatches(30, "strong"), true);
  assert.equal(valuationDistanceFilterMatches(30.01, "extreme"), true);
  assert.equal(valuationDistanceFilterMatches(null, "all"), false);
});

test("cascades valuation, distance, MA, and MA20-bias filters into rows, counts, and live alerts", async () => {
  const panels = await readFile(panelsPath, "utf8");
  assert.match(panels, /距分水嶺強度/);
  assert.match(panels, /valuationDistanceFilterMatches\(row\.riverDistancePct, distanceFilter\)/);
  assert.match(panels, /riverDistanceFilter: distanceFilter/);
  assert.match(panels, /valuationDistanceFilterMatches\(distance, config\.riverDistanceFilter \?\? "all"\)/);
  assert.match(panels, /距 20MA 乖離率/);
  assert.match(panels, /Math\.abs\(\(row\.close \/ row\.ma20 - 1\) \* 100\)/);
  assert.match(panels, /ma20BiasFilterMatches\(row, ma20BiasFilter\)/);
  assert.match(panels, /riverDistanceFilter: distanceFilter, ma20BiasFilter, maFilter/);
  assert.match(panels, /ma20BiasFilterMatches\(\{ close: price, ma20: row\.ma20 \}, config\.ma20BiasFilter \?\? "all"\)/);
});

test("uses one displayable universe for filter counts and table rows", async () => {
  const [route, panels, history] = await Promise.all([
    readFile(technicalRoutePath, "utf8"),
    readFile(panelsPath, "utf8"),
    readFile(new URL("../db/technical-market-history.ts", import.meta.url), "utf8"),
  ]);

  assert.match(history, /name\?: string/);
  assert.match(route, /const nameIndex = fields\.indexOf\("證券名稱"\)/);
  assert.match(route, /name: snapshot\?\.name \?\? row\.name/);
  assert.match(panels, /const displayRows = useMemo/);
  assert.match(panels, /stock\?\.name \?\? row\.name \?\? row\.code/);
  assert.match(panels, /const maCounts = useMemo\(\(\) => displayRows\.filter/);
  assert.doesNotMatch(panels, /return stock && row\.riverBase/);
});

test("opens every valuation result in the five-minute K-line workspace", async () => {
  const [page, panels] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(panelsPath, "utf8"),
  ]);

  assert.match(page, /url\.searchParams\.set\("interval", "5m"\)/);
  assert.match(page, /<ValuationRiverScreener[^>]+onOpenKline=\{openKlineByTicker\}/);
  assert.match(panels, /onOpenKline: \(ticker: string, name: string\) => void/);
  assert.match(panels, /onOpenKline\(row\.code, row\.name\)/);
  assert.match(panels, /可切換一分鐘與日線/);
});

test("shows price movement, traded price, and group columns in the valuation river table", async () => {
  const [page, panels] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(panelsPath, "utf8"),
  ]);

  for (const label of ["漲跌幅", "漲跌", "成交價", "族群"]) assert.match(panels, new RegExp(`>${label}<`));
  assert.match(panels, /priceChangeFromPct\(row\.close, row\.changePct\)/);
  assert.match(panels, /groupByTicker\.get\(row\.code\)/);
  assert.match(page, /groupByTicker=\{chipGroupByTicker\}/);
  assert.doesNotMatch(panels, /<span>收盤<\/span>/);
});

test("keeps latest-trading-day change data in the fast valuation response", async () => {
  const route = await readFile(technicalRoutePath, "utf8");
  assert.match(route, /changePct: row\.changePct/);
  assert.match(route, /candidateDates\(technicalMarketDateAnchor\(\)/);
});

test("calculates the full rolling 160-session river before sampling chart points", () => {
  const candles = Array.from({ length: 260 }, (_, index) => ({
    date: `day-${index + 1}`,
    close: index < 180 ? 10_000 + index * 5 : 14_000 + (index - 180) * 25,
  }));
  const expected = valuationMedian(candles.slice(-160).map((point) => point.close));
  const points = buildValuationRiverPoints(candles, 220);

  assert.equal(points.length, 131);
  assert.equal(points.at(-1)?.date, "day-260");
  assert.equal(points.at(-1)?.base, expected);
});
