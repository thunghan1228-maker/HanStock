import test from "node:test";
import assert from "node:assert/strict";
import {
  buildWeeklyGroupComparisons,
  buildWeeklyStockComparisons,
  calculateCompletedWeeklyChipScores,
  selectWeeklyTop,
  summarizeWeeklyPerformance,
  weeklyScoreChangePercent,
} from "../lib/weekly-chip-ranking.ts";

const weights = { main: 0, foreign: 100, trust: 0, etf: 0, dealer: 0, hedge: 0 };
const dates = [
  "2026/08/21", "2026/08/20", "2026/08/19", "2026/08/18", "2026/08/17",
  "2026/08/14", "2026/08/13", "2026/08/12", "2026/08/11", "2026/08/10",
];

function series(current, previous) {
  return dates.map((date, index) => ({ date, foreign: index < 5 ? current : previous, trust: 0, dealer: 0, hedge: 0 }));
}

test("uses the latest completed Friday windows for current and previous weekly chip scores", () => {
  const result = calculateCompletedWeeklyChipScores(series(95, 90), weights);
  assert.deepEqual(result, {
    currentEndDate: "2026/08/21",
    previousEndDate: "2026/08/14",
    currentScore: 95,
    previousScore: 90,
    twoWeeksAgoScore: null,
    scoreChange: 5,
    previousScoreChange: null,
  });
  assert.equal(weeklyScoreChangePercent(95, 90), 5.56);
  assert.equal(weeklyScoreChangePercent(5, 0), null);
});

test("calculates the third completed week when fifteen trading days are available", () => {
  const threeWeekDates = [...dates, "2026/08/07", "2026/08/06", "2026/08/05", "2026/08/04", "2026/08/03"];
  const result = calculateCompletedWeeklyChipScores(threeWeekDates.map((date, index) => ({ date, foreign: index < 5 ? 95 : index < 10 ? 90 : 70, trust: 0, dealer: 0, hedge: 0 })), weights);
  assert.equal(result?.twoWeeksAgoScore, 70);
  assert.equal(result?.previousScoreChange, 20);
});

test("summarizes previous-week selections through the current Friday close", () => {
  const strong = summarizeWeeklyPerformance([
    { ticker: "1111", returnPct: 10 },
    { ticker: "2222", returnPct: -2 },
    { ticker: "3333", returnPct: null },
  ], "increase");
  assert.equal(strong.count, 2);
  assert.equal(strong.averageReturnPct, 4);
  assert.equal(strong.hitRate, 50);
  assert.equal(strong.best?.ticker, "1111");

  const weak = summarizeWeeklyPerformance([
    { ticker: "1111", returnPct: -8 },
    { ticker: "2222", returnPct: 2 },
  ], "decrease");
  assert.equal(weak.averageReturnPct, -3);
  assert.equal(weak.hitRate, 50);
});

test("builds separate stock and group top twenty increase and decrease rankings", () => {
  const stocks = buildWeeklyStockComparisons([
    { code: "1111", name: "強甲", market: "上市", groupName: "半導體", series: series(80, 50) },
    { code: "2222", name: "強乙", market: "上櫃", groupName: "半導體", series: series(60, 70) },
    { code: "3333", name: "弱甲", market: "上市", groupName: "航運", series: series(-85, -40) },
  ], weights);
  assert.deepEqual(selectWeeklyTop(stocks, "current", "increase").map((row) => row.code), ["1111", "2222"]);
  assert.deepEqual(selectWeeklyTop(stocks, "current", "decrease").map((row) => row.code), ["3333"]);
  const groups = buildWeeklyGroupComparisons(stocks, [
    { name: "半導體", codes: ["1111", "2222"] },
    { name: "航運", codes: ["3333"] },
  ]);
  assert.equal(selectWeeklyTop(groups, "current", "increase")[0].name, "半導體");
  assert.equal(selectWeeklyTop(groups, "current", "decrease")[0].name, "航運");
  assert.equal(groups.find((row) => row.name === "半導體")?.currentScore, 70);
});
