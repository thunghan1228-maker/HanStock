import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateFourGateSignals,
  formatFourGateSignalNote,
  fourGateCandidateTickers,
  passesPreviousEstimatedSellPressureFilter,
  passesTodayImmediatePressureRatioFilter,
  pressureThresholdForTimestamp,
  previousEstimatedSellPressureAmountFromNote,
} from "../lib/four-gate-signals.ts";

const atTaipei = (hour, minute) => Date.UTC(2026, 7, 21, hour - 8, minute);

function bar(hour, minute, overrides = {}) {
  return {
    ts: atTaipei(hour, minute),
    high: 100,
    low: 99,
    close: 100,
    volume: 100,
    main_buy_amount: 2_000_000,
    main_sell_amount: 1_000_000,
    ...overrides,
  };
}

test("uses the four intraday pressure thresholds", () => {
  assert.equal(pressureThresholdForTimestamp(atTaipei(9, 29)), 50);
  assert.equal(pressureThresholdForTimestamp(atTaipei(9, 30)), 70);
  assert.equal(pressureThresholdForTimestamp(atTaipei(10, 0)), 90);
  assert.equal(pressureThresholdForTimestamp(atTaipei(11, 0)), 120);
});

test("requires previous estimated sell pressure to be strictly above one hundred million", () => {
  assert.equal(previousEstimatedSellPressureAmountFromNote("前日預估隔日賣壓 5,000 萬｜比例 80%"), 50_000_000);
  assert.equal(previousEstimatedSellPressureAmountFromNote("前日預估賣壓金額 0.51 億｜比例 80%"), 51_000_000);
  assert.equal(passesPreviousEstimatedSellPressureFilter({ kind: "daytradeEarlyBuy50", note: "前日預估隔日賣壓 1.00 億｜比例 80%" }), false);
  assert.equal(passesPreviousEstimatedSellPressureFilter({ kind: "daytradeEarlySell50", note: "前日預估隔日賣壓 1.01 億｜比例 80%" }), true);
  assert.equal(passesPreviousEstimatedSellPressureFilter({ kind: "daytradeEarlySell50", note: "比例 80%" }), false);
  assert.equal(passesPreviousEstimatedSellPressureFilter({ kind: "triangleNearBreakout", note: "接近突破" }), true);
});

test("requires today's immediate buy and sell signals to reach two hundred percent", () => {
  assert.equal(passesTodayImmediatePressureRatioFilter({ kind: "daytradeEarlyBuy50", note: "比例 199.9%" }), false);
  assert.equal(passesTodayImmediatePressureRatioFilter({ kind: "daytradeEarlyBuy50", note: "比例 200.0%" }), true);
  assert.equal(passesTodayImmediatePressureRatioFilter({ kind: "daytradeEarlySell50", note: "比例 200%" }), true);
  assert.equal(passesTodayImmediatePressureRatioFilter({ kind: "daytradeEarlySell50", note: "沒有比例" }), false);
  assert.equal(passesTodayImmediatePressureRatioFilter({ kind: "triangleNearBreakout", note: "接近突破" }), true);
});

test("emits a live four-gate signal only when all four checks pass", () => {
  const signal = {
    tradeDate: "2026-08-21",
    ticker: "1102",
    name: "亞泥",
    kind: "daytradeEarlyBuy50",
    label: "盤中大單買進達前日預估隔日賣壓 50%",
    barTs: atTaipei(9, 10),
    price: 102,
    note: "前日預估隔日賣壓 1.20 億｜盤中大單買進 9600 萬｜比例 80.0%",
  };
  const bars = Array.from({ length: 11 }, (_, index) => bar(9, index, index >= 9 ? {
    close: 101,
    main_buy_amount: 4_000_000,
    main_sell_amount: 1_000_000,
  } : {}));
  const barsByTicker = new Map([[signal.ticker, bars]]);

  assert.deepEqual(fourGateCandidateTickers([signal]), ["1102"]);
  const results = calculateFourGateSignals([signal], barsByTicker);
  assert.equal(results.length, 1);
  assert.equal(results[0].kind, "fourGateBullish");
  assert.equal(results[0].tradeDate, "2026-08-21");
  assert.match(results[0].note, /即時金額 80\.0% ≥ 前日預估賣壓金額的 50%/);
  assert.match(results[0].note, /多空淨額比 \+60\.0%/);
  assert.match(results[0].note, /VWAP/);
  assert.match(results[0].note, /突破首五高/);
  assert.match(results[0].note, /連續 2 次/);

  const reversedPreviousBar = bars.map((item) => item.ts === atTaipei(9, 9)
    ? { ...item, main_buy_amount: 1_000_000, main_sell_amount: 4_000_000 }
    : item);
  assert.equal(calculateFourGateSignals([signal], new Map([[signal.ticker, reversedPreviousBar]])).length, 0);

  const lowPressureSignal = { ...signal, note: "前日預估隔日賣壓 1.00 億｜盤中大單買進 8000 萬｜比例 80.0%" };
  assert.deepEqual(fourGateCandidateTickers([lowPressureSignal]), []);
  assert.equal(calculateFourGateSignals([lowPressureSignal], barsByTicker).length, 0);
});

test("upgrades persisted four-gate wording and splits the detail into two lines", () => {
  const note = formatFourGateSignalNote("分時 148.3%≥90%｜淨額比 -100.0%｜現價 217.50＜VWAP 221.61｜跌破首五低 218.50｜連續 2 次");
  assert.equal(note, "即時金額 148.3% ≥ 前日預估賣壓金額的 90% ｜ 多空淨額比 -100.0%\n現價 217.50＜VWAP 221.61 ｜ 跌破首五低 218.50 ｜ 連續 2 次");
});
