import assert from "node:assert/strict";
import test from "node:test";

import {
  matchesCurrentDailyStrategyGroup,
  passesDailyStrategyDirectionChange,
} from "../lib/daily-strategy-universe.ts";

const focusOrder = {
  bull: Array.from({ length: 10 }, (_, index) => `强群${index + 1}`),
  bear: Array.from({ length: 10 }, (_, index) => `弱群${index + 1}`),
};

test("daily strategy bullish signals only allow zero through positive seven percent", () => {
  assert.equal(passesDailyStrategyDirectionChange("bull", 0), true);
  assert.equal(passesDailyStrategyDirectionChange("bull", 7), true);
  assert.equal(passesDailyStrategyDirectionChange("bull", -0.01), false);
  assert.equal(passesDailyStrategyDirectionChange("bull", 7.01), false);
});

test("daily strategy bearish signals only allow zero through negative seven percent", () => {
  assert.equal(passesDailyStrategyDirectionChange("bear", 0), true);
  assert.equal(passesDailyStrategyDirectionChange("bear", -7), true);
  assert.equal(passesDailyStrategyDirectionChange("bear", 0.01), false);
  assert.equal(passesDailyStrategyDirectionChange("bear", -7.01), false);
});

test("daily strategies must match the current directional top or bottom ten group and rank", () => {
  assert.equal(matchesCurrentDailyStrategyGroup("bull", "强群1", 1, focusOrder), true);
  assert.equal(matchesCurrentDailyStrategyGroup("bear", "弱群10", 10, focusOrder), true);
  assert.equal(matchesCurrentDailyStrategyGroup("bull", "弱群1", 1, focusOrder), false);
  assert.equal(matchesCurrentDailyStrategyGroup("bear", "强群1", 1, focusOrder), false);
  assert.equal(matchesCurrentDailyStrategyGroup("bull", "强群1", 11, focusOrder), false);
});
