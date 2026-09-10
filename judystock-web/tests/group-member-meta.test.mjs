import assert from "node:assert/strict";
import test from "node:test";
import { formatGroupDisposition, formatGroupNetFundingRate } from "../lib/group-member-meta.ts";

test("處置中顯示距出關天數並標記星號條件", () => {
  assert.deepEqual(formatGroupDisposition({ status: "處置中", period: "2026/08/20～2026/08/24", releaseDate: "2026/08/25" }, "2026-08-22"), {
    label: "處置中｜距出關 3 天",
    isActive: true,
    tone: "active",
  });
});

test("已結束顯示出關第幾天", () => {
  assert.deepEqual(formatGroupDisposition({ status: "已結束", period: "2026/08/10～2026/08/19", releaseDate: "2026/08/20" }, "2026-08-22"), {
    label: "已出關｜出關第 3 天",
    isActive: false,
    tone: "released",
  });
});

test("大單淨額資金占比使用淨額除以成交金額", () => {
  assert.deepEqual(formatGroupNetFundingRate(30_000_000, 200_000_000, true), { label: "+15.00%", value: 15 });
  assert.deepEqual(formatGroupNetFundingRate(-20_000_000, 200_000_000, true), { label: "-10.00%", value: -10 });
  assert.deepEqual(formatGroupNetFundingRate(undefined, undefined, false), { label: "待資料", value: null });
});
