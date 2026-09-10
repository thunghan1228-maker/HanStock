import assert from "node:assert/strict";
import test from "node:test";
import { calculateMaArrangement, labelMaArrangement } from "../lib/ma-arrangement.ts";

test("15/15 but price below falling short MAs is a pullback, not confirmed strong bull", () => {
  const result = calculateMaArrangement({
    close: 90.9,
    maValues: { 5: 93.12, 10: 92.76, 20: 91.5, 60: 88, 120: 82, 240: 75 },
    previousMaValues: { 5: 93.64, 10: 93.25, 20: 91.2, 60: 87.8, 120: 81.8, 240: 74.8 },
  });
  assert.equal(result?.score, 15);
  assert.equal(result?.bullConfirmed, false);
  assert.equal(result?.label, "多頭排列／短線回檔");
});

test("legacy cached 15/15 rows below MA5 are labelled as pullbacks during v2 migration", () => {
  assert.equal(labelMaArrangement(15, false, false), "多頭排列／短線回檔");
});

test("15/15 with price above rising short MAs remains confirmed complete bull", () => {
  const result = calculateMaArrangement({
    close: 105,
    maValues: { 5: 103, 10: 101, 20: 98, 60: 90, 120: 84, 240: 76 },
    previousMaValues: { 5: 102, 10: 100, 20: 97, 60: 89.8, 120: 83.8, 240: 75.8 },
  });
  assert.equal(result?.score, 15);
  assert.equal(result?.bullConfirmed, true);
  assert.equal(result?.label, "完整多頭");
});
