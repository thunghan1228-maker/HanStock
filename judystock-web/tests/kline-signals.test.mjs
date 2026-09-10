import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replayKlineSignals, mergeKlineSignals } from "../lib/kline-signals.ts";

const now = Date.parse("2026-09-05T08:00:00+08:00");
// Actual recovered OHLC history, retained to reproduce the September 4 outage.
const candles = JSON.parse(readFileSync(new URL("./fixtures/2317-5m-20260904.json", import.meta.url), "utf8"));

test("restores the missing September 3/4 chart signals using the original strategy engines", () => {
  const signals = replayKlineSignals("2317", candles, now);
  for (const date of ["2026-09-03", "2026-09-04"]) {
    const day = signals.filter(s => s.tradeDate === date);
    for (const kind of ["crossUp905", "firstCross905High", "ma520Up", "ma520Down", "ma20turn"]) assert.ok(day.some(s => s.kind === kind), `${date} ${kind}`);
  }
  const latest = signals.filter(s => s.tradeDate === "2026-09-04");
  assert.equal(latest.length, 79);
  assert.deepEqual(latest.filter(s => s.kind === "ma20turn").map(s => [s.label, new Date(s.barTs + 28800000).toISOString().slice(11, 16)]), [["20MA↓", "12:45"], ["20MA↑", "12:50"]]);
  assert.ok(latest.every(s => !s.notified && s.source === "kline-replay"));
  assert.deepEqual(replayKlineSignals("2317", [...candles, ...candles].reverse(), now), signals);
});

test("retains stored observations, scopes batched queries and does not invent opening references", () => {
  const replay = replayKlineSignals("2317", candles, now);
  const original = { ...replay.at(-1), id: 123, source: "original", note: "stored", notified: true };
  const since = Date.parse("2026-09-04T00:00:00+08:00");
  const merged = mergeKlineSignals([original], replay, "2317", since);
  assert.equal(merged.find(s => s.kind === original.kind && s.barTs === original.barTs).id, 123);
  assert.ok(merged.every(s => s.barTs >= since));
  assert.equal(mergeKlineSignals([], replay, "2330").length, 0);
  assert.equal(replayKlineSignals("2317", candles.filter(b => !b.date.endsWith("09:00")), now).length, 0);
});
