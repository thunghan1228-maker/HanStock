import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  BLACK_DRAGON_INTRADAY_MODEL_VERSION,
  findFirstIntradayBlackDragonSignal,
} from "../lib/black-dragon-intraday.ts";

const tradeDate = "2026-09-04";
const base = {
  modelVersion: BLACK_DRAGON_INTRADAY_MODEL_VERSION,
  code: "2330",
  name: "測試股",
  market: "twse",
  targetDate: tradeDate,
  completedThrough: "2026-09-03",
  previousClose: 100,
  maLiveBaseSums: { 5: 502, 10: 1052, 20: 2102, 60: 5702, 120: 10202, 240: 17402 },
  previousMaValues: { 5: 120, 10: 115, 20: 110, 60: 100, 120: 90, 240: 80 },
  referenceHighs: { 5: 101, 10: 101, 20: 101, 60: 101, 120: 101, 360: null },
  averageVolume20d: 1_000_000,
};

test("創高黑龍從 11:00 第一根符合五分 K 成立，吃量仍累計全日", () => {
  const bars = [
    { ts: Date.parse(`${tradeDate}T09:00:00+08:00`), open: 100, high: 100.8, low: 99.8, close: 100.5, volume: 500_000 },
    { ts: Date.parse(`${tradeDate}T09:05:00+08:00`), open: 100.5, high: 102, low: 97.5, close: 98, volume: 1_500_000 },
    { ts: Date.parse(`${tradeDate}T10:55:00+08:00`), open: 98, high: 100, low: 97, close: 98, volume: 100_000 },
    { ts: Date.parse(`${tradeDate}T11:00:00+08:00`), open: 98, high: 102, low: 97, close: 98, volume: 200_000 },
    { ts: Date.parse(`${tradeDate}T11:05:00+08:00`), open: 98, high: 106, low: 97, close: 105, volume: 800_000 },
  ];
  const signal = findFirstIntradayBlackDragonSignal(base, bars, tradeDate);
  assert.equal(signal?.barTs, bars[3].ts);
  assert.equal(signal?.price, 98);
  assert.equal(signal?.cumulativeVolume, 2_300_000);
  assert.ok(Number(signal?.projectedVolumeRatio) > 1.5);
  assert.equal(signal?.tier, "surge");
  assert.deepEqual(signal?.newHighPeriods, [5, 10, 20, 60, 120]);
});

test("只有 11:00 前成立的黑龍不回補；11:00 後首次成立可保留", () => {
  const bars = [
    { ts: Date.parse(`${tradeDate}T09:00:00+08:00`), open: 100, high: 100.8, low: 99.8, close: 100.5, volume: 500_000 },
    { ts: Date.parse(`${tradeDate}T09:05:00+08:00`), open: 100.5, high: 102, low: 97.5, close: 98, volume: 1_500_000 },
    { ts: Date.parse(`${tradeDate}T13:25:00+08:00`), open: 98, high: 110, low: 97, close: 108, volume: 5_000_000 },
  ];
  assert.equal(findFirstIntradayBlackDragonSignal(base, bars, tradeDate), null);
  const eligible = { ...bars[1], ts: Date.parse(`${tradeDate}T11:05:00+08:00`) };
  assert.equal(findFirstIntradayBlackDragonSignal(base, [...bars, eligible], tradeDate)?.barTs, eligible.ts);
});

test("盤中創高黑龍由即時端點掃描、永久保存並以真正時間顯示", () => {
  const route = readFileSync(new URL("../app/api/black-dragon-intraday/route.ts", import.meta.url), "utf8");
  const technical = readFileSync(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8");
  const page = readEarlySellSources();
  assert.match(route, /findFirstIntradayBlackDragonSignal/);
  assert.match(route, /saveRiverStrategySignals/);
  assert.match(route, /SCAN_BATCH_SIZE = 80/);
  assert.match(page, /const endpoint = isIntradaySignalCollectionWindow/);
  assert.match(page, /\/api\/black-dragon-intraday\?backfill=1/);
  assert.match(route, /after-hours-backfill-complete/);
  assert.match(technical, /blackDragonIntradayBases/);
  assert.match(technical, /bar\.date < targetDate/);
  assert.match(route, /base\.targetDate === signalDate/);
  assert.match(route, /isListedOrOtcStockCode\(base\.code\)/);
  assert.match(route, /parseHanStockOfficialPrimaryGroupMap\(stockGroupsSource\)/);
  assert.match(route, /officialPrimaryGroupByCode\.has\(signal\.code\)/);
  assert.match(route, /officialPrimaryGroupByCode\.has\(indicator\.code\)/);
  assert.match(route, /groupName: officialPrimaryGroupByCode\.get\(signal\.code\)/);
  assert.match(page, /barTs: Number\(row\.barTs\)/);
  assert.match(page, /當時累計成交量/);
});
