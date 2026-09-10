import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateIntradayExtraLargeSellSignals,
  calculateIntradayExtraLargeBuySignals,
  extraLargeBuyCandidateTickers,
  extraLargeSellCandidateTickers,
  extraLargeSellFilteredTickers,
  extraLargeSellNetFundingRate,
  mergePreviousLargeNetBaselines,
  extraLargeTriggerForce,
  hasQualifiedExtraLargeTriggerForce,
  qualifyExtraLargeSignalByTriggerBars,
} from "../lib/intraday-extra-large-sell.ts";
import { calculateIntradayLargeForceValue } from "../lib/intraday-large-force.ts";

const atTaipei = (hour, minute) => Date.UTC(2026, 7, 21, hour - 8, minute);

const bar = (hour, minute, sell, close = 50) => ({
  ts: atTaipei(hour, minute),
  high: close,
  low: close,
  close,
  volume: 10,
  main_buy_amount: 100_000,
  main_sell_amount: sell,
});

const sellBaseline = { ticker: "1727", name: "中華化", dataDate: "2026-08-20", netLargeAmount: 143_000_000, turnoverAmount: 757_000_000 };

test("rejects the reported positive-force sell conflict and the mirrored negative-force buy conflict", () => {
  const positive = { ...bar(13, 29, 177_000_000, 100), volume: 10000, main_buy_amount: 693_000_000 };
  assert.equal(calculateIntradayLargeForceValue([positive], "2026-08-21").forcePct, 51.6);
  assert.deepEqual(calculateIntradayExtraLargeSellSignals("2026-08-21", [sellBaseline], new Map([["1727", [positive]]])), []);
  const negative = { ...positive, main_buy_amount: 177_000_000, main_sell_amount: 693_000_000 };
  assert.deepEqual(calculateIntradayExtraLargeBuySignals("2026-08-21", [{ ...sellBaseline, netLargeAmount: -143_000_000 }], new Map([["1727", [negative]]])), []);
});

test("waits until both threshold and cumulative direction agree, retaining the trigger value after reversal", () => {
  const bars = [
    { ...bar(9, 0, 143_000_000, 100), volume: 5000, main_buy_amount: 200_000_000 },
    { ...bar(9, 1, 200_000_000, 100), volume: 5000, main_buy_amount: 0 },
    { ...bar(9, 2, 0, 100), volume: 5000, main_buy_amount: 600_000_000 },
  ];
  const [signal] = calculateIntradayExtraLargeSellSignals("2026-08-21", [sellBaseline], new Map([["1727", bars]]));
  assert.equal(signal.barTs, atTaipei(9, 1));
  assert.equal(extraLargeTriggerForce(signal).forcePct, -14.3);
  assert.equal(extraLargeTriggerForce(signal).netAmount, -143_000_000);
  assert.ok(Math.abs(calculateIntradayLargeForceValue(bars, signal.tradeDate, signal.barTs).forcePct + 14.3) < 1e-9);
  assert.ok(calculateIntradayLargeForceValue(bars, signal.tradeDate).forcePct > 0);
  assert.equal(hasQualifiedExtraLargeTriggerForce(signal), true);
  assert.deepEqual(qualifyExtraLargeSignalByTriggerBars(signal, bars), signal);
});

test("does not emit on zero net, unavailable classification, or missing opposite-side amounts", () => {
  for (const change of [
    { main_buy_amount: 200_000_000 },
    { main_buy_amount: null },
    { main_buy_amount: undefined },
    { main_force_available: false },
  ]) {
    assert.deepEqual(calculateIntradayExtraLargeSellSignals("2026-08-21", [sellBaseline], new Map([["1727", [{ ...bar(9, 0, 200_000_000), ...change }]]])), []);
  }
});

test("revalidates legacy records only at the trigger minute and never borrows a later force", () => {
  const old = { tradeDate: "2026-08-21", kind: "intradayExtraLargeSell", barTs: atTaipei(9, 1), note: "舊訊號｜族群同步 化學 跌幅第 10 名 0.00%" };
  const bars = [
    { ...bar(9, 0, 143_000_000), main_buy_amount: 200_000_000 },
    { ...bar(9, 1, 10_000_000), main_buy_amount: 0 },
    { ...bar(9, 2, 200_000_000), main_buy_amount: 0 },
  ];
  assert.equal(hasQualifiedExtraLargeTriggerForce(old), false);
  assert.equal(qualifyExtraLargeSignalByTriggerBars(old, bars), null);
  assert.equal(qualifyExtraLargeSignalByTriggerBars({ ...old, barTs: atTaipei(9, 3) }, bars), null);
  const corrected = qualifyExtraLargeSignalByTriggerBars({ ...old, barTs: atTaipei(9, 2) }, bars.map((row) => ({ ...row, volume: 0, close: 0 })));
  assert.equal(hasQualifiedExtraLargeTriggerForce(corrected), true);
  assert.equal(extraLargeTriggerForce(corrected).forcePct, null);
  assert.ok(extraLargeTriggerForce(corrected).netAmount < 0);
  assert.match(corrected.note, /族群同步 化學/);
  assert.equal(hasQualifiedExtraLargeTriggerForce({ ...corrected, note: corrected.note + "｜觸發當時盤中大戶力 +51.6%" }), false);
});

test("deduplicates and sorts minute bars, ignoring other sessions", () => {
  const first = { ...bar(9, 0, 80_000_000), volume: 2000 };
  const second = { ...bar(9, 1, 80_000_000), volume: 2000 };
  const outside = { ...bar(8, 59, 0), main_buy_amount: 900_000_000 };
  const [signal] = calculateIntradayExtraLargeSellSignals("2026-08-21", [sellBaseline], new Map([["1727", [second, outside, first, first]]]));
  assert.equal(signal.barTs, second.ts);
  assert.match(signal.note, /盤中大單賣出累計 1.60 億/);
});

test("emits once at the first minute cumulative selling reaches the previous positive net amount", () => {
  const baselines = [{ ticker: "2609", name: "陽明", dataDate: "2026-08-20", netLargeAmount: 200_000_000, turnoverAmount: 400_000_000 }];
  const bars = new Map([["2609", [bar(9, 0, 80_000_000, 60), bar(9, 1, 120_000_000, 59.8), bar(9, 2, 80_000_000, 59.5)]]]);
  assert.deepEqual(extraLargeSellCandidateTickers(baselines), ["2609"]);
  const signals = calculateIntradayExtraLargeSellSignals("2026-08-21", baselines, bars);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].kind, "intradayExtraLargeSell");
  assert.equal(signals[0].barTs, atTaipei(9, 1));
  assert.equal(signals[0].price, 59.8);
  assert.match(signals[0].note, /前日大單淨額 2\.00 億/);
  assert.match(signals[0].note, /大單淨額（隔日沖）資金占比 50\.00%/);
  assert.match(signals[0].note, /50\.00%\n盤中大單賣出累計/);
  assert.match(signals[0].note, /達成比例 100\.0%/);
});

test("ignores non-positive, same-day and not-yet-reached baselines", () => {
  const baselines = [
    { ticker: "1101", name: "台泥", dataDate: "2026-08-20", netLargeAmount: 0 },
    { ticker: "1102", name: "亞泥", dataDate: "2026-08-21", netLargeAmount: 1_000_000 },
    { ticker: "1103", name: "嘉泥", dataDate: "2026-08-20", netLargeAmount: 2_000_000 },
  ];
  const bars = new Map([
    ["1101", [bar(9, 0, 3_000_000)]],
    ["1102", [bar(9, 0, 3_000_000)]],
    ["1103", [bar(9, 0, 1_999_999)]],
  ]);
  assert.deepEqual(calculateIntradayExtraLargeSellSignals("2026-08-21", baselines, bars), []);
});

test("filters previous net amounts at or below fifty million", () => {
  const baselines = [
    { ticker: "1101", name: "台泥", dataDate: "2026-08-20", netLargeAmount: 50_000_000, turnoverAmount: 250_000_000 },
    { ticker: "1102", name: "亞泥", dataDate: "2026-08-20", netLargeAmount: 50_000_001, turnoverAmount: 250_000_000 },
  ];
  const bars = new Map([
    ["1101", [bar(9, 0, 110_000_000)]],
    ["1102", [bar(9, 0, 110_000_000)]],
  ]);
  assert.deepEqual(extraLargeSellCandidateTickers(baselines), ["1102"]);
  assert.deepEqual(extraLargeSellFilteredTickers(baselines), ["1101"]);
  assert.deepEqual(calculateIntradayExtraLargeSellSignals("2026-08-21", baselines, bars).map((signal) => signal.ticker), ["1102"]);
});

test("removes net funding rates at or below ten percent", () => {
  const baselines = [
    { ticker: "1101", name: "台泥", dataDate: "2026-08-20", netLargeAmount: 120_000_000, turnoverAmount: 1_200_000_000 },
    { ticker: "1102", name: "亞泥", dataDate: "2026-08-20", netLargeAmount: 120_000_000, turnoverAmount: 1_500_000_000 },
    { ticker: "1103", name: "嘉泥", dataDate: "2026-08-20", netLargeAmount: 120_000_000, turnoverAmount: 1_000_000_000 },
    { ticker: "1104", name: "環泥", dataDate: "2026-08-20", netLargeAmount: 120_000_000 },
  ];
  const bars = new Map(baselines.map((baseline) => [baseline.ticker, [bar(9, 0, 130_000_000)]]));
  assert.deepEqual(extraLargeSellCandidateTickers(baselines), ["1103"]);
  assert.deepEqual(extraLargeSellFilteredTickers(baselines), ["1101", "1102", "1104"]);
  assert.deepEqual(calculateIntradayExtraLargeSellSignals("2026-08-21", baselines, bars).map((signal) => signal.ticker), ["1103"]);
});

test("reads the persisted net funding rate for historical filtering", () => {
  assert.equal(extraLargeSellNetFundingRate("前日大單淨額 500 萬｜大單淨額（隔日沖）資金占比 10.01%"), 10.01);
  assert.equal(extraLargeSellNetFundingRate("舊版訊號沒有占比"), null);
});

test("merges permanent previous-day baselines into an incomplete full-market batch", () => {
  const merged = mergePreviousLargeNetBaselines(
    [{ ticker: "1101", name: "台泥", dataDate: "2026-08-20", netLargeAmount: 0, turnoverAmount: 1_000_000_000 }],
    [
      { ticker: "1101", name: "台泥", dataDate: "2026-08-20", netLargeAmount: 120_000_000, turnoverAmount: 600_000_000 },
      { ticker: "2603", name: "長榮", dataDate: "2026-08-20", netLargeAmount: -200_000_000, turnoverAmount: 800_000_000 },
    ],
  );
  assert.equal(merged.length, 2);
  assert.equal(merged.find((row) => row.ticker === "1101")?.netLargeAmount, 120_000_000);
  assert.deepEqual(extraLargeSellCandidateTickers(merged), ["1101"]);
  assert.deepEqual(extraLargeBuyCandidateTickers(merged), ["2603"]);
});

test("uses the saved reference price when a historical force bar has no OHLC price", () => {
  const baselines = [{ ticker: "1217", name: "愛之味", dataDate: "2026-08-20", netLargeAmount: 120_000_000, turnoverAmount: 600_000_000, fallbackPrice: 10 }];
  const bars = new Map([["1217", [bar(10, 20, 120_000_000, 0)]]]);
  const signals = calculateIntradayExtraLargeSellSignals("2026-08-21", baselines, bars);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].price, 10);
});

test("mirrors extra-large sell with previous net selling and intraday cumulative buying", () => {
  const baselines = [{ ticker: "2603", name: "長榮", dataDate: "2026-08-20", netLargeAmount: -200_000_000, turnoverAmount: 400_000_000 }];
  const bars = new Map([["2603", [
    { ...bar(9, 0, 0, 200), main_buy_amount: 80_000_000 },
    { ...bar(9, 1, 0, 199.5), main_buy_amount: 120_000_000 },
  ]]]);
  assert.deepEqual(extraLargeBuyCandidateTickers(baselines), ["2603"]);
  const signals = calculateIntradayExtraLargeBuySignals("2026-08-21", baselines, bars);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].kind, "intradayExtraLargeBuy");
  assert.equal(signals[0].barTs, atTaipei(9, 1));
  assert.match(signals[0].note, /前日大單淨額 -2\.00 億/);
  assert.match(signals[0].note, /盤中大單買進累計 2\.00 億/);
});
