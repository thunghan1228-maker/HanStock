import assert from "node:assert/strict";
import test from "node:test";

import { calculateTdccWeeklyChangePp } from "../lib/tdcc-radar-calculation.ts";

test("calculates TDCC weekly changes from current minus previous holder percentage", () => {
  assert.equal(calculateTdccWeeklyChangePp(33.53, 35.42), -1.89);
  assert.equal(calculateTdccWeeklyChangePp(22.92, 22.92), 0);
  assert.equal(calculateTdccWeeklyChangePp(19.87, 12.1), 7.77);
});
