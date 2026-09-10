import assert from "node:assert/strict";
import test from "node:test";
import { calculateChipRankMovements } from "../lib/chip-scoring.ts";

test("compares today's combined rank with yesterday and uses positive change for improvement", () => {
  const movements = calculateChipRankMovements([
    { ticker: "1402", currentScore: 92.8, previousScore: 70 },
    { ticker: "2609", currentScore: 89.9, previousScore: 95 },
    { ticker: "1815", currentScore: 88.1, previousScore: 88 },
  ]);
  assert.deepEqual(movements.get("1402"), { currentRank: 1, previousRank: 3, change: 2 });
  assert.deepEqual(movements.get("2609"), { currentRank: 2, previousRank: 1, change: -1 });
  assert.deepEqual(movements.get("1815"), { currentRank: 3, previousRank: 2, change: -1 });
});

test("shows zero when today's and yesterday's combined rank are unchanged", () => {
  const movements = calculateChipRankMovements([
    { ticker: "1402", currentScore: 92.8, previousScore: 90 },
    { ticker: "2609", currentScore: 89.9, previousScore: 80 },
  ]);
  assert.equal(movements.get("1402")?.change, 0);
  assert.equal(movements.get("2609")?.change, 0);
});
