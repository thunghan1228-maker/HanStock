import assert from "node:assert/strict";
import test from "node:test";

import { parseTdccCsv, parseTdccJson } from "../lib/tdcc-radar-source.ts";

test("parses the latest official TDCC CSV and keeps only the large-holder tier", () => {
  const csv = "\uFEFF資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20260821,0050,14,1,100,1.00\n20260821,0050,15,2,2500,25.25\n20260821,2330,15,3,3000,30.50\n";
  assert.deepEqual(parseTdccCsv(csv), [
    { ticker: "0050", dataDate: "2026/08/21", largeHolderPct: 25.25 },
    { ticker: "2330", dataDate: "2026/08/21", largeHolderPct: 30.5 },
  ]);
});

test("keeps the TDCC JSON endpoint as a fallback", () => {
  assert.deepEqual(parseTdccJson([{
    "﻿資料日期": "20260814",
    "證券代號": "2330",
    "持股分級": "15",
    "占集保庫存數比例%": "28.40",
  }]), [{ ticker: "2330", dataDate: "2026/08/14", largeHolderPct: 28.4 }]);
});
