import assert from "node:assert/strict";
import test from "node:test";
import {
  calculateMainForceZeroSignals,
  hasPotentialMainForceABCDSignal,
  MAIN_FORCE_FILTER_MARKER,
} from "../lib/main-force-zero-signals.ts";

const ts = (hour, minute) => Date.UTC(2026, 7, 21, hour - 8, minute);
const bar = (hour, minute, close, netVolume, netAmount, volume = 100) => ({
  ts: ts(hour, minute),
  open: close,
  high: close + 0.05,
  low: close - 0.05,
  close,
  volume,
  main_net_volume: netVolume,
  main_net_amount: netAmount,
  main_force_available: true,
});

function fiveDayVolumes(value = 100) {
  return new Map(["2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"].map((date) => [
    date,
    new Map(Array.from({ length: 271 }, (_, index) => [9 * 60 + index, value])),
  ]));
}

const bullishBars = (triggerVolume = 150, amount = 600_000) => [
  bar(9, 0, 100, -20, -100_000),
  bar(9, 4, 100, -10, -100_000),
  bar(9, 5, 100.4, 40, amount),
  bar(9, 6, 100.6, 5, amount, triggerVolume),
];

test("emits only the filtered strong-bullish signal after all A-D conditions pass", () => {
  const bars = bullishBars();
  assert.equal(hasPotentialMainForceABCDSignal(bars), true);
  const results = calculateMainForceZeroSignals("2330", "台積電", bars, fiveDayVolumes());
  assert.deepEqual(results.map((signal) => signal.kind), ["mainForceStrongBullish"]);
  assert.equal(results[0].barTs, ts(9, 6));
  assert.match(results[0].note, new RegExp(MAIN_FORCE_FILTER_MARKER));
  assert.match(results[0].note, /主力淨額率 \+/);
  assert.match(results[0].note, /量比 1\.50×/);
});

test("emits a green strong-bearish signal when the same strict conditions pass downward", () => {
  const results = calculateMainForceZeroSignals("2303", "聯電", [
    bar(9, 0, 100, 20, 100_000),
    bar(9, 4, 100, 10, 100_000),
    bar(9, 5, 99.6, -40, -600_000),
    bar(9, 6, 99.4, -5, -600_000, 150),
  ], fiveDayVolumes());
  assert.deepEqual(results.map((signal) => signal.kind), ["mainForceStrongBearish"]);
  assert.equal(results[0].label, "主力累計強勢翻空");
  assert.match(results[0].note, /距VWAP -/);
});

test("keeps 09:00-09:04 as observation only and upgrades a valid opening move at 09:05", () => {
  const results = calculateMainForceZeroSignals("2603", "長榮", [
    bar(9, 0, 100, -20, -100_000),
    bar(9, 3, 100.4, 30, 600_000),
    bar(9, 4, 100.6, 5, 600_000),
    bar(9, 5, 100.8, 5, 600_000, 150),
  ], fiveDayVolumes());
  assert.equal(results.length, 1);
  assert.equal(results[0].barTs, ts(9, 5));
});

test("rejects a candidate when the volume does not reach 1.5 times its baseline", () => {
  const results = calculateMainForceZeroSignals("2881", "富邦金", bullishBars(149), fiveDayVolumes());
  assert.equal(results.length, 0);
});

test("rejects candidates without five prior trading days or the one-percent amount-rate threshold", () => {
  assert.equal(calculateMainForceZeroSignals("2330", "台積電", bullishBars(), new Map()).length, 0);
  assert.equal(calculateMainForceZeroSignals("2330", "台積電", bullishBars(150, 10_000), fiveDayVolumes()).length, 0);
});

test("resets the cumulative line each trading day and never treats the first nonzero bar as a cross", () => {
  const nextDay = { ...bar(9, 0, 103, 50, 500_000), ts: Date.UTC(2026, 7, 22, 1, 0) };
  const results = calculateMainForceZeroSignals("2603", "長榮", [bar(9, 0, 100, -20, -100_000), nextDay], fiveDayVolumes());
  assert.equal(results.length, 0);
});
