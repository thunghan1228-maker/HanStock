import assert from "node:assert/strict";
import test from "node:test";
import { completedDailyStrategyDateAnchor, technicalMarketDateAnchor } from "../lib/technical-market-date.ts";

function taipeiTime(value) {
  return new Date(`${value}+08:00`).getTime();
}

test("keeps the latest Friday trading date through Monday 08:39 Taipei time", () => {
  assert.equal(technicalMarketDateAnchor(taipeiTime("2026-08-29T12:00:00")).toISOString().slice(0, 10), "2026-08-28");
  assert.equal(technicalMarketDateAnchor(taipeiTime("2026-08-30T18:00:00")).toISOString().slice(0, 10), "2026-08-28");
  assert.equal(technicalMarketDateAnchor(taipeiTime("2026-08-31T08:39:59")).toISOString().slice(0, 10), "2026-08-28");
  assert.equal(technicalMarketDateAnchor(taipeiTime("2026-08-31T08:40:00")).toISOString().slice(0, 10), "2026-08-31");
});

test("keeps after-hours daily strategies on the latest completed session", () => {
  assert.equal(completedDailyStrategyDateAnchor(taipeiTime("2026-08-31T09:00:00")).toISOString().slice(0, 10), "2026-08-28");
  assert.equal(completedDailyStrategyDateAnchor(taipeiTime("2026-08-31T13:59:59")).toISOString().slice(0, 10), "2026-08-28");
  assert.equal(completedDailyStrategyDateAnchor(taipeiTime("2026-08-31T14:00:00")).toISOString().slice(0, 10), "2026-08-31");
  assert.equal(completedDailyStrategyDateAnchor(taipeiTime("2026-09-05T18:00:00")).toISOString().slice(0, 10), "2026-09-04");
});
