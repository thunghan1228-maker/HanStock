import test from "node:test";
import assert from "node:assert/strict";
import { assessWeeklyMainForce, buildWeeklyMainForceRows } from "../lib/weekly-main-force-score.ts";
import { rankBrokerBranchWeekly } from "../lib/broker-branch-weekly-score.ts";

test("does not mix incomplete main-force inputs into the institutional score", () => {
  const result = assessWeeklyMainForce({ institutional: 72, brokerBranch: null, tdccLargeHolder: null });
  assert.equal(result.score, null);
  assert.equal(result.label, "待接入");
});

test("identifies three-way weekly main-force concentration", () => {
  const result = assessWeeklyMainForce({ institutional: 60, brokerBranch: 48, tdccLargeHolder: 24 });
  assert.equal(result.score, 46.2);
  assert.equal(result.label, "主力同步集中");
});

test("keeps institutional and TDCC scores separate while the branch source is absent", () => {
  const rows = buildWeeklyMainForceRows(new Map([["2330", 42]]), new Map([["2330", 67]]));
  assert.deepEqual(rows[0], { ticker: "2330", institutional: 42, brokerBranch: null, tdccLargeHolder: 67, score: null, availableComponentCount: 2, positiveComponentCount: 2, negativeComponentCount: 0, label: "待接入" });
});

test("ranks net selling with concentrated branches below net buying", () => {
  const scores = rankBrokerBranchWeekly([
    { ticker: "1111", weekEndDate: "2026/08/21", netAmount: -500, concentration: 22, activeBranches: 3 },
    { ticker: "2222", weekEndDate: "2026/08/21", netAmount: 0, concentration: 8, activeBranches: 6 },
    { ticker: "3333", weekEndDate: "2026/08/21", netAmount: 900, concentration: 18, activeBranches: 4 },
  ]);
  assert.ok((scores.get("1111") ?? 0) < 0);
  assert.equal(scores.get("2222"), 0);
  assert.ok((scores.get("3333") ?? 0) > 0);
});

test("keeps exact-zero branch imbalance neutral even when zero rows dominate", () => {
  const scores = rankBrokerBranchWeekly([
    { ticker: "1111", weekEndDate: "2026/08/21", netAmount: -500, concentration: 40, activeBranches: 3 },
    { ticker: "2222", weekEndDate: "2026/08/21", netAmount: 800, concentration: 35, activeBranches: 4 },
    ...Array.from({ length: 20 }, (_, index) => ({ ticker: `Z${index}`, weekEndDate: "2026/08/21", netAmount: 0, concentration: 99, activeBranches: 20 })),
  ]);
  assert.equal(scores.get("Z0"), 0);
  assert.ok((scores.get("1111") ?? 0) < 0);
  assert.ok((scores.get("2222") ?? 0) > 0);
});

test("calculates the combined score only after branch and TDCC scores arrive", () => {
  const rows = buildWeeklyMainForceRows(
    new Map([["2330", 60]]),
    new Map([["2330", 24]]),
    new Map([["2330", 48]]),
  );
  assert.equal(rows[0].score, 46.2);
  assert.equal(rows[0].label, "主力同步集中");
});
