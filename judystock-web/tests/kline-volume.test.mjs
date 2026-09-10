import assert from "node:assert/strict";
import test from "node:test";
import {
  hasSparseMinuteVolume,
  hubLotsToShares,
  preferCompleteCumulativeVolume,
  sumMinuteVolumesToShares,
} from "../lib/kline-volume.ts";
import { latestTradingDateLabel } from "../lib/kline-daily.ts";

test("normalizes Hub minute volume from lots to shares", () => {
  assert.equal(hubLotsToShares(0.3), 300);
  assert.equal(hubLotsToShares(2258), 2_258_000);
  assert.equal(hubLotsToShares(undefined), 0);
});

test("never lets a partial repaired bucket reduce cumulative volume", () => {
  assert.equal(preferCompleteCumulativeVolume(2_258_000, 300), 2_258_000);
  assert.equal(preferCompleteCumulativeVolume(0, 126_200), 126_200);
  assert.equal(preferCompleteCumulativeVolume(undefined, Number.NaN), 0);
});

test("keeps Yahoo daily volume in shares while converting Hub lots exactly once", () => {
  assert.equal(sumMinuteVolumesToShares([1.2, 2.3, 0], "lots"), 3_500);
  assert.equal(sumMinuteVolumesToShares([1_200, 2_300, 0], "shares"), 3_500);
  assert.notEqual(sumMinuteVolumesToShares([1_200, 2_300], "shares"), 3_500_000);
});

test("detects a live minute session whose volume coverage is missing", () => {
  assert.equal(hasSparseMinuteVolume([1200, 0, 0, 0, 800]), true);
  assert.equal(hasSparseMinuteVolume([1200, 900, 0, 700, 800]), false);
  assert.equal(hasSparseMinuteVolume([0, 0]), false);
});

test("uses Friday's latest trading session when daily K is opened on Saturday", () => {
  const taipeiTime = (iso) => new Date(iso).getTime();
  const bars = [
    { ts: taipeiTime("2026-08-27T13:30:00+08:00") },
    { ts: taipeiTime("2026-08-28T09:00:00+08:00") },
    { ts: taipeiTime("2026-08-28T13:30:00+08:00") },
  ];

  assert.equal(
    latestTradingDateLabel(bars, taipeiTime("2026-08-29T10:00:00+08:00")),
    "2026/08/28",
  );
});

test("ignores a future-dated bar when selecting the latest daily session", () => {
  const now = new Date("2026-08-29T10:00:00+08:00").getTime();
  assert.equal(latestTradingDateLabel([
    { ts: new Date("2026-08-28T13:30:00+08:00").getTime() },
    { ts: new Date("2026-08-31T09:00:00+08:00").getTime() },
  ], now), "2026/08/28");
});
