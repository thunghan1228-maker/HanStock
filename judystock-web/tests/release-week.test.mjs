import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  formatReleaseWeekWindow,
  isReleaseInWeek,
  mondayOfWeek,
  releaseWeekWindow,
} from "../lib/release-week.ts";

test("builds this week, next week, and the week after as Monday through Friday", () => {
  const today = "2026-09-03";
  assert.equal(mondayOfWeek(today), "2026-08-31");
  assert.deepEqual(releaseWeekWindow("本週出關", today), { start: "2026-08-31", end: "2026-09-04" });
  assert.deepEqual(releaseWeekWindow("下週出關", today), { start: "2026-09-07", end: "2026-09-11" });
  assert.deepEqual(releaseWeekWindow("下下週出關", today), { start: "2026-09-14", end: "2026-09-18" });
  assert.equal(formatReleaseWeekWindow("下下週出關", today), "09/14～09/18");
});

test("excludes weekends and keeps the boundary weekdays", () => {
  const today = "2026-09-03";
  assert.equal(isReleaseInWeek("2026/09/04", "本週出關", today), true);
  assert.equal(isReleaseInWeek("2026/09/05", "本週出關", today), false);
  assert.equal(isReleaseInWeek("2026/09/07", "下週出關", today), true);
  assert.equal(isReleaseInWeek("2026/09/11", "下週出關", today), true);
  assert.equal(isReleaseInWeek("2026/09/14", "下下週出關", today), true);
  assert.equal(isReleaseInWeek("2026/09/18", "下下週出關", today), true);
});

test("renders the three-week selector and grouped release sections", async () => {
  const panel = await readFile(new URL("../app/stock-screener/DispositionRiskPanel.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const helper = await readFile(new URL("../lib/release-week.ts", import.meta.url), "utf8");

  assert.match(panel, /release-week-switch/);
  assert.match(panel, /release-group-heading/);
  assert.match(panel, /group-chip-members/);
  assert.match(panel, /group\.members\.length/);
  assert.doesNotMatch(panel, /<select/);
  assert.match(helper, /"下下週出關"/);
  assert.match(css, /\.release-week-switch/);
  assert.match(css, /\.release-group-heading/);
});
