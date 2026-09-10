import assert from "node:assert/strict";
import test from "node:test";
import { createForceChartScale } from "../lib/kline-force-scale.ts";
import { hasStoredForceObservation } from "../lib/force-observation.ts";

test("a large closing trade no longer flattens the other force bars", () => {
  const values = [null, 0, -1000, 20000, -35000, 65000, 28191000];
  const scale = createForceChartScale(values, 100, 208);
  assert.equal(scale.y(0), scale.zeroY);
  assert.ok(Math.abs(scale.y(1000) - scale.zeroY) >= 2.5);
  assert.ok(Math.abs(scale.y(20000) - scale.zeroY) > 6);
  assert.ok(scale.y(-1000) > scale.zeroY);
  assert.ok(scale.y(1000) < scale.zeroY);
  assert.ok(scale.y(28191000) >= 104);
  assert.equal(scale.y(-65000) - scale.zeroY, scale.zeroY - scale.y(65000));
  for (const pair of [[1000, 20000], [20000, 35000], [65000, 28191000]])
    assert.ok(scale.y(pair[0]) > scale.y(pair[1]));
});

test("scale is stable across share and lot units and a narrower viewport", () => {
  const values = [-25, 10, 90, 28191];
  const lots = createForceChartScale(values, 0, 188);
  const shares = createForceChartScale(values.map(value => value * 1000), 0, 188);
  for (const value of values) assert.equal(lots.y(value), shares.y(value * 1000));
  const empty = createForceChartScale([null, 0, NaN], 0, 188);
  assert.equal(empty.y(0), 94);
  assert.equal(empty.y(NaN), 94);
});

test("missing cached history stays missing while real zero-net activity is retained", () => {
  assert.equal(hasStoredForceObservation(undefined), false);
  assert.equal(hasStoredForceObservation({netVolume: 0, buyAmount: 0, sellAmount: 0, mainTickCount: 0}), false);
  assert.equal(hasStoredForceObservation({netVolume: 0, buyAmount: 1000, sellAmount: 1000, mainTickCount: 2}), true);
  assert.equal(hasStoredForceObservation({netVolume: -20}), true);
});
