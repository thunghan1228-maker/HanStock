import test from "node:test";
import assert from "node:assert/strict";

import { AFTER_HOURS_INTELLIGENCE } from "../lib/after-hours-intelligence.ts";

test("盤後情報包含隔日沖、處置補充與申購資料", () => {
  assert.equal(AFTER_HOURS_INTELLIGENCE.dataDate, "2026-09-04");
  assert.equal(AFTER_HOURS_INTELLIGENCE.daytradeBuy.length, 22);
  assert.equal(AFTER_HOURS_INTELLIGENCE.dispositionAlerts.length, 12);
  assert.equal(AFTER_HOURS_INTELLIGENCE.subscriptions.length, 10);
  assert.deepEqual(AFTER_HOURS_INTELLIGENCE.daytradeBuy[0], {
    ticker: "6672",
    name: "騰輝電子-KY",
    buyRate: 42,
    lots: 4978,
  });
  assert.equal(AFTER_HOURS_INTELLIGENCE.dispositionAlerts.find((row) => row.ticker === "2455")?.detail, "9/7～9/15 採每 2 分鐘撮合");
  assert.equal(AFTER_HOURS_INTELLIGENCE.subscriptions.find((row) => row.ticker === "7856")?.spreadPct, 114);
});

test("各盤後名單的股票代號不重複", () => {
  for (const rows of [
    AFTER_HOURS_INTELLIGENCE.daytradeBuy,
    AFTER_HOURS_INTELLIGENCE.dispositionAlerts,
    AFTER_HOURS_INTELLIGENCE.subscriptions,
  ]) {
    assert.equal(new Set(rows.map((row) => row.ticker)).size, rows.length);
  }
});
