import assert from "node:assert/strict";
import test from "node:test";
import {
  calculateFourGateSourceSignalsFromMinuteBars,
  calculateImmediateSignalsFromMinuteBars,
} from "../lib/live-immediate-signals.ts";

const ts = (time) => Date.parse(`2026-08-31T${time}:00+08:00`);

test("rebuilds first 200% buy and sell crossings from current minute bars", () => {
  const result = calculateImmediateSignalsFromMinuteBars("2026-08-31", [{
    ticker: "2330",
    name: "台積電",
    estimatedNextDaySellAmount: 200_000_000,
    mainForceDataAvailable: true,
  }], new Map([["2330", [
    { ts: ts("13:13"), close: 1380, main_buy_amount: 210_000_000, main_sell_amount: 100_000_000 },
    { ts: ts("13:14"), close: 1385, main_buy_amount: 200_000_000, main_sell_amount: 310_000_000 },
    { ts: ts("13:15"), close: 1390, main_buy_amount: 500_000_000, main_sell_amount: 500_000_000 },
  ]]]));

  assert.equal(result.latestBarAt, ts("13:15"));
  assert.deepEqual(result.signals.map((signal) => [signal.kind, signal.barTs]), [
    ["daytradeEarlyBuy50", ts("13:14")],
    ["daytradeEarlySell50", ts("13:14")],
  ]);
  assert.match(result.signals[0].note, /1 分 K 即時備援補算/);
});

test("ignores unavailable, too-small and out-of-session baselines", () => {
  const result = calculateImmediateSignalsFromMinuteBars("2026-08-31", [
    { ticker: "1111", name: "小額", estimatedNextDaySellAmount: 100_000_000 },
    { ticker: "2222", name: "無主力", estimatedNextDaySellAmount: 200_000_000, mainForceDataAvailable: false },
  ], new Map([
    ["1111", [{ ts: ts("13:15"), close: 10, main_buy_amount: 1_000_000_000 }]],
    ["2222", [{ ts: ts("08:59"), close: 20, main_buy_amount: 1_000_000_000 }]],
  ]));
  assert.deepEqual(result.signals, []);
  assert.equal(result.latestBarAt, 0);
});

test("rebuilds four-gate candidates with the intraday 50 70 90 and 120 percent thresholds", () => {
  const result = calculateFourGateSourceSignalsFromMinuteBars("2026-08-31", [{
    ticker: "2330",
    name: "台積電",
    estimatedNextDaySellAmount: 200_000_000,
    mainForceDataAvailable: true,
  }], new Map([["2330", [
    { ts: ts("09:29"), close: 100, main_buy_amount: 100_000_000, main_sell_amount: 0 },
    { ts: ts("09:30"), close: 101, main_buy_amount: 30_000_000, main_sell_amount: 140_000_000 },
    { ts: ts("10:00"), close: 102, main_buy_amount: 50_000_000, main_sell_amount: 40_000_000 },
    { ts: ts("11:00"), close: 103, main_buy_amount: 60_000_000, main_sell_amount: 60_000_000 },
  ]]]));

  assert.deepEqual(result.map((signal) => [signal.kind, signal.barTs]), [
    ["daytradeEarlyBuy50", ts("09:29")],
    ["daytradeEarlySell50", ts("09:30")],
    ["daytradeEarlyBuy50", ts("10:00")],
    ["daytradeEarlySell50", ts("10:00")],
    ["daytradeEarlyBuy50", ts("11:00")],
    ["daytradeEarlySell50", ts("11:00")],
  ]);
  assert.match(result[0].note, /1 分 K 四項備援補算/);
});
