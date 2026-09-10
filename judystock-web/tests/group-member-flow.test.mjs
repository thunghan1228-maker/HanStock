import assert from "node:assert/strict";
import { test } from "node:test";

const source = await import("../lib/group-member-flow.ts");

test("converts official ROC dates to ISO dates", () => {
  assert.equal(source.rocDateToIso("1150828"), "2026-08-28");
  assert.equal(source.rocDateToIso("not-a-date"), "");
});

test("uses the same historical-tick buy minus sell definition as daytrade ranking", () => {
  assert.equal(source.readAuthoritativeGroupNetAmount({
    large_buy_amount: 3_307_335_000,
    large_sell_amount: 1_310_470_000,
    total_turnover_amount: 6_606_445_500,
    main_force_data_available: true,
  }), 1_996_865_000);
});

test("never turns a pending backfill into a real zero", () => {
  assert.equal(source.readAuthoritativeGroupNetAmount({
    large_buy_amount: 0,
    large_sell_amount: 0,
    total_turnover_amount: 1_000_000,
    main_force_data_available: false,
  }), null);
});

test("uses the turnover amount returned by the same authoritative historical-tick row", () => {
  assert.equal(source.readAuthoritativeGroupTurnoverAmount({ total_turnover_amount: 4_959_038_248 }), 4_959_038_248);
  assert.equal(source.readAuthoritativeGroupTurnoverAmount({ total_turnover_amount: 0 }), null);
});
