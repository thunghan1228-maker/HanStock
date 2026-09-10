import assert from "node:assert/strict";
import test from "node:test";
import { calculateFiveMinutePatternSignals } from "../lib/five-minute-pattern-signals.ts";

const tradeDate = "2026-09-04";
const context = {
  previousTradeDate: "2026-09-03",
  previousHigh: 103,
  previousCloses: Array(19).fill(100),
  openingBar: { ts: Date.parse(`${tradeDate}T09:00:00+08:00`), open: 101, high: 102, low: 100, close: 101, volume: 100 },
};

function ts(time) {
  return Date.parse(`${tradeDate}T${time}:00+08:00`);
}

function bar(time, open, high, low, close) {
  return { ts: ts(time), open, high, low, close, volume: 100 };
}

test("1+2多 only emits after a five-minute close clears yesterday high and 905 high", () => {
  const signals = calculateFiveMinutePatternSignals("2330", "台積電", [
    bar("09:00", 101, 102, 100, 101),
    bar("09:05", 101, 102.8, 100.8, 102.5),
    bar("09:10", 102.5, 103.6, 102.2, 103.2),
    bar("11:00", 104, 105, 103.5, 104.5),
  ], context, tradeDate);

  const signal = signals.find((item) => item.kind === "fiveMinuteOnePlusTwoLong");
  assert.ok(signal);
  assert.equal(signal.barTs, ts("09:10"));
  assert.match(signal.note, /昨日高 103/);
  assert.match(signal.note, /905高 102/);
  assert.match(signal.note, /905來源 09:00完整五分K/);
});

test("1+2多 rejects a batch that has no real 09:00 opening five-minute bar", () => {
  const signals = calculateFiveMinutePatternSignals("3006", "晶豪科", [
    bar("12:20", 296, 296, 296, 296),
    bar("13:10", 295.5, 297, 294.5, 297),
  ], { ...context, openingBar: undefined }, tradeDate);
  assert.equal(signals.some((item) => item.kind === "fiveMinuteOnePlusTwoLong"), false);
});

test("晶豪科 is rejected when 297 clears yesterday high 289 but not the real 905 high 302.5", () => {
  const openingBar = bar("09:00", 288, 302.5, 288, 297.5);
  const signals = calculateFiveMinutePatternSignals("3006", "晶豪科", [
    bar("13:10", 295.5, 297, 294.5, 297),
  ], {
    ...context,
    previousHigh: 289,
    openingBar,
  }, tradeDate);
  assert.equal(signals.some((item) => item.kind === "fiveMinuteOnePlusTwoLong"), false);
});

test("1+2多 does not use an unfinished five-minute candle", () => {
  const signals = calculateFiveMinutePatternSignals("2330", "台積電", [
    bar("09:05", 101, 102.8, 100.8, 102.5),
    bar("09:10", 102.5, 103.6, 102.2, 103.2),
  ], context, tradeDate, ts("09:12"));
  assert.equal(signals.some((item) => item.kind === "fiveMinuteOnePlusTwoLong"), false);
});

test("crossing only 905 high is not 1+2多", () => {
  const signals = calculateFiveMinutePatternSignals("2330", "台積電", [
    bar("09:00", 101, 102, 100, 101),
    bar("09:05", 101, 102.8, 100.8, 102.5),
  ], context, tradeDate);
  assert.equal(signals.some((item) => item.kind === "fiveMinuteOnePlusTwoLong"), false);
});

test("12空 emits after 905 low breaks, 1 high forms, MA20 breaks and bends down, then 2 stays below 1", () => {
  const signals = calculateFiveMinutePatternSignals("2303", "聯電", [
    bar("09:00", 101, 105, 99, 102),
    bar("09:05", 100, 101, 98, 100.5),
    bar("09:10", 100.5, 104, 100, 102),
    bar("09:15", 101, 101, 98.5, 99),
    bar("09:20", 99, 102.5, 99, 99.8),
    bar("09:25", 99.5, 101, 98, 98.5),
    bar("11:00", 97, 98, 96, 96.5),
  ], context, tradeDate);

  const signal = signals.find((item) => item.kind === "fiveMinuteTwelveShort");
  assert.ok(signal);
  assert.equal(signal.barTs, ts("09:25"));
  assert.match(signal.note, /1高 104/);
  assert.match(signal.note, /2高 102\.5 不過 1高/);
});

test("12空 is rejected when the second rebound passes 1 high", () => {
  const signals = calculateFiveMinutePatternSignals("2303", "聯電", [
    bar("09:00", 101, 105, 99, 102),
    bar("09:05", 100, 101, 98, 100.5),
    bar("09:10", 100.5, 104, 100, 102),
    bar("09:15", 101, 101, 98.5, 99),
    bar("09:20", 99, 104.5, 99, 99.8),
    bar("09:25", 99.5, 101, 98, 98.5),
  ], context, tradeDate);
  assert.equal(signals.some((item) => item.kind === "fiveMinuteTwelveShort"), false);
});

test("five-minute patterns require 19 preceding closes for a real MA20", () => {
  const signals = calculateFiveMinutePatternSignals("2330", "台積電", [
    bar("09:00", 101, 102, 100, 101),
    bar("09:05", 101, 104, 100.8, 103.5),
  ], { ...context, previousCloses: Array(18).fill(100) }, tradeDate);
  assert.deepEqual(signals, []);
});
