import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { DAILY_STRATEGY_CATALOG, detectCompletedDailyStrategies, detectIntradayMa20Cross, isDailyStrategyHistoryCoverageWindow, isDailyStrategySignalWindow, isListedOrOtcStockCode } from "../lib/daily-strategy-signals.ts";

test("daily strategies include listed and OTC stocks but exclude ETFs and funds", () => {
  ["2330", "6488", "2881A", "910322"].forEach((code) => assert.equal(isListedOrOtcStockCode(code), true, code));
  ["0050", "00878", "00919", "00631L", "00679B", "00980A", "01007T", "02001L"].forEach((code) => assert.equal(isListedOrOtcStockCode(code), false, code));
});

test("keeps the complete eleven-strategy catalog without the removed mother-child setup", () => {
  assert.equal(DAILY_STRATEGY_CATALOG.length, 11);
  assert.deepEqual(DAILY_STRATEGY_CATALOG.map((item) => item.name), [
    "紅劍", "三紅劍", "換手黑", "黑飛舞", "小黑飛舞", "劍飛舞",
    "神下影", "黑豹", "處置黑龍", "穿2", "破2",
  ]);
  assert.equal(DAILY_STRATEGY_CATALOG.some((item) => String(item.kind) === "motherChild"), false);
});

test("only allows intraday daily-strategy signals from 11:00 through 14:30 Taipei", () => {
  assert.equal(isDailyStrategySignalWindow(Date.parse("2026-09-01T10:59:00+08:00")), false);
  assert.equal(isDailyStrategySignalWindow(Date.parse("2026-09-01T11:00:00+08:00")), true);
  assert.equal(isDailyStrategySignalWindow(Date.parse("2026-09-01T14:30:00+08:00")), true);
  assert.equal(isDailyStrategySignalWindow(Date.parse("2026-09-01T14:31:00+08:00")), false);
});

test("keeps daily-strategy history coverage warm from market open without emitting signals before 11:00", () => {
  assert.equal(isDailyStrategyHistoryCoverageWindow(Date.parse("2026-09-03T08:59:00+08:00")), false);
  assert.equal(isDailyStrategyHistoryCoverageWindow(Date.parse("2026-09-03T09:00:00+08:00")), true);
  assert.equal(isDailyStrategyHistoryCoverageWindow(Date.parse("2026-09-03T10:59:00+08:00")), true);
  assert.equal(isDailyStrategySignalWindow(Date.parse("2026-09-03T10:59:00+08:00")), false);
  assert.equal(isDailyStrategyHistoryCoverageWindow(Date.parse("2026-09-05T10:00:00+08:00")), false);
});

test("emits 穿2 only from below MA20 and 破2 only from above MA20", () => {
  assert.equal(detectIntradayMa20Cross({ previousPrice: 99, previousMa20: 100, currentPrice: 101, currentMa20: 100.1 })?.name, "穿2");
  assert.equal(detectIntradayMa20Cross({ previousPrice: 101, previousMa20: 100, currentPrice: 99, currentMa20: 99.9 })?.name, "破2");
  assert.equal(detectIntradayMa20Cross({ previousPrice: 101, previousMa20: 100, currentPrice: 102, currentMa20: 100.1 }), null);
});

test("includes the completed daily MA20 cross in after-hours scans", () => {
  const candles = Array.from({ length: 19 }, (_, index) => ({
    date: `2026/07/${String(index + 1).padStart(2, "0")}`,
    open: 100,
    high: 101,
    low: 99,
    close: 100,
    volume: 1000,
  }));
  candles.push({ date: "2026/07/20", open: 100, high: 101, low: 98, close: 99, volume: 1000 });
  candles.push({ date: "2026/07/21", open: 100, high: 103, low: 99, close: 102, volume: 1000 });
  assert.ok(detectCompletedDailyStrategies({ candles }).some((match) => match.name === "穿2"));
});

test("retains daily strategy calculation for the separate stock screener", async () => {
  const [route] = await Promise.all([
    readFile(new URL("../app/api/river-radar/intraday/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /detectIntradayDailyStrategies/);
  assert.match(route, /detectIntradayMa20Cross/);
  assert.match(route, /saveRiverStrategySignals/);
  assert.match(route, /DAILY_STRATEGY_GROUP_SELECTION_VERSION = "focus-ranking-67-primary-group-daily-strategy-top10-change7-after1100-v4"/);
  assert.match(route, /focus-ranking-67-daily-strategy-top10-v1/);
  assert.match(route, /focus-ranking-67-daily-strategy-top10-change7-v2/);
  assert.match(route, /matched\.direction === "bull"[\s\S]*?strongStrategyGroupsByCode[\s\S]*?weakStrategyGroupsByCode/);
  assert.match(route, /isOfficialRankedStrategySignal/);
  assert.match(route, /isDailyStrategySignalWindow\(latestLive\.ts\)/);
  assert.match(route, /strategyHistoryCoverageRequired \? loadDailyHistories\(coveredCodes\)/);
  assert.match(route, /strategyHistoryCoverageRequired,/);
  assert.match(route, /isDailyStrategySignalWindow\(Number\(signal\.barTs\)\)/);
  assert.match(route, /passesDailyStrategyDirectionChange\(matched\.direction, candidate\.changePct\)/);
  assert.match(route, /accumulatedOfficialStrategySignals/);
  assert.match(route, /riverContext/);
  assert.match(route, /selectionRuleVersion: signal\.selectionRuleVersion \?\? "focus-ranking-67-daily-strategy-top10-v1"/);
  assert.match(route, /changePct: Number\.isFinite\(Number\(signal\.changePct\)\) \? signal\.changePct : companion\.changePct/);
  assert.match(route, /Number\(signal\.groupRank\) >= 1 && Number\(signal\.groupRank\) <= 10/);
  assert.match(route, /const officialPrimaryGroup = primaryGroups\.get\(signal\.code\)/);
  assert.match(route, /matchesPersistedOfficialPrimaryGroup\(officialPrimaryGroup, signal\.groupName\)/);
  assert.match(route, /primaryGroupCoverage: OFFICIAL_PRIMARY_GROUP_BY_CODE\.size/);
  assert.match(route, /riverRaw: storedRiverSignals\.length/);
  assert.match(route, /riverVisible: riverSignals\.length/);
});

test("keeps full OHLCV history while backfilling the after-hours screener", async () => {
  const source = await readFile(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8");
  assert.match(source, /\{ date, code, market, open, high, low, close, volume \}/);
  assert.match(source, /rows\.push\(\{ code, market, open, high, low, close, volume \}\)/);
  assert.match(source, /dailyCandles\.length >= 21/);
  assert.match(source, /bar\.date <= dailyStrategyCutoff/);
  assert.match(source, /dailyStrategyDataDate/);
});

test("exposes a separate after-hours daily strategy screener", async () => {
  const [page, panel, route, technicalRoute, intradayRoute] = await Promise.all([
    readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/stock-screener/DailyStrategyPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/daily-strategies/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/river-radar/intraday/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(page, /日線 11 策略/);
  assert.match(page, /DailyStrategyPanel/);
  assert.match(panel, /盤後：首頁 → 選股程式 → 日線 11 策略/);
  assert.doesNotMatch(panel, /母子組合 K/);
  assert.match(panel, /排除 ETF/);
  assert.match(route, /dailyStrategyVersion/);
  assert.match(route, /isListedOrOtcStockCode/);
  assert.match(technicalRoute, /!dailyStrategyBackfill \|\| isListedOrOtcStockCode\(code\)/);
  assert.match(intradayRoute, /accumulatedOfficialStrategySignals\(storedStrategySignals, OFFICIAL_PRIMARY_GROUP_BY_CODE, storedRiverSignals\)/);
  assert.match(route, /strategyCatalog/);
  assert.match(route, /String\(match\.kind\) !== "motherChild"/);
  assert.match(intradayRoute, /String\(signal\.strategyKind\) !== "motherChild"/);
});
