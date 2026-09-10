import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWeeklyInsights, weeklyGroupBreadth, shiftWeek, weeklyScreenerUrl, parseWeeklySelection } from '../lib/weekly-chip-insights.ts';

const date = '2026/09/04';
const weights = { foreign: 100, trust: 0 };
function history(scores, ranks = scores.map(() => 1)) {
  return scores.map((score, i) => ({ weekEndDate: shiftWeek(date, -i), weights, stocks: [{ code: '2330', name: '台積電', groupName: '半導體', market: '上市', score, increaseRank: ranks[i] }] }));
}
test('consecutive improvement requires four and seven adjacent score observations', () => {
  const full = buildWeeklyInsights(history([70, 60, 50, 40, 30, 20, 10]), date)[0];
  assert.equal(full.threeRising, true); assert.equal(full.fiveOfSix, true);
  assert.equal(buildWeeklyInsights(history([70, 60, 50]), date)[0].threeRising, false);
  const missing = history([70, 60, 50, 40, 30, 20, 10]).filter((_, i) => i !== 2);
  const row = buildWeeklyInsights(missing, date)[0];
  assert.equal(row.threeRising, false); assert.equal(row.fiveOfSix, false);
  assert.equal(row.points[2].score, null); assert.equal(row.points[1].delta, null);
});
test('five of six allows one non-improving week, never a missing week', () => {
  assert.equal(buildWeeklyInsights(history([60, 50, 40, 45, 30, 20, 10]), date)[0].fiveOfSix, true);
  assert.equal(buildWeeklyInsights(history([60, 65, 40, 45, 30, 20, 10]), date)[0].fiveOfSix, false);
});
test('top N transitions include ranks just outside threshold; missing observations are not new entries', () => {
  assert.equal(buildWeeklyInsights(history([20, 10], [10, 11]), date)[0].newEntry, true);
  assert.equal(buildWeeklyInsights(history([20, 10], [11, 10]), date)[0].dropped, true);
  assert.equal(buildWeeklyInsights(history([20]), date)[0].newEntry, false);
  assert.equal(buildWeeklyInsights(history([20, 10], [11, 10]), date, 12, 20)[0].dropped, false);
});
test('negative weeks count in full-window averages and totals', () => {
  const row = buildWeeklyInsights(history([20, -30, 10, 0], [1, null, 2, null]), date, 4)[0];
  assert.equal(row.available, 4); assert.equal(row.average, 0);
  assert.equal(row.positiveTotal, 30); assert.equal(row.negativeTotal, -30);
  assert.equal(row.appearances, 2); assert.equal(row.turnedPositive, true);
  assert.equal(buildWeeklyInsights(history([20, 0]), date)[0].turnedPositive, false);
});
test('historical selection excludes future weeks; incompatible or unknown weights break comparisons', () => {
  const archives = history([90, 20, -30]);
  const past = buildWeeklyInsights(archives, shiftWeek(date, -1))[0];
  assert.equal(past.points[0].score, 20); assert.equal(past.appearances, 1);
  archives[1].weights = { foreign: 50, trust: 50 };
  assert.equal(buildWeeklyInsights(archives, date)[0].points[1].score, null);
  assert.equal(buildWeeklyInsights(archives, date)[0].newEntry, false);
  delete archives[0].weights;
  assert.deepEqual(buildWeeklyInsights(archives, date), []);
});
test('group breadth denominator excludes incomplete pairs and deduplicates each ticker', () => {
  const archives = history([20, 10]);
  archives[0].stocks.push({ ...archives[0].stocks[0], code: '2317', score: -10 });
  const groups = weeklyGroupBreadth(buildWeeklyInsights(archives, date));
  assert.equal(groups[0].valid, 1); assert.equal(groups[0].rising, 1); assert.equal(groups[0].ratio, 1);
});
test('selection link roundtrips an explicit dated candidate universe, including empty restriction', () => {
  const url = weeklyScreenerUrl(['2330', '2330', '<script>', '2317'], date, '連三週升分');
  assert.deepEqual(parseWeeklySelection(new URL(url, 'https://example.test').searchParams), { codes: ['2330', '2317'], date, label: '連三週升分' });
  assert.equal(parseWeeklySelection(new URLSearchParams()), null);
  assert.deepEqual(parseWeeklySelection(new URLSearchParams('weeklyCodes=bad')).codes, []);
});

import { weeklyReportGroups, weeklyPage } from '../lib/weekly-chip-insights.ts';
const reportRow = (code, groupName, rank, delta, score = 50) => ({ code, name: code, groupName, market: 'twse', points: [{ date, score, rank, delta }] });
test('weekly report separates paired entries, singleton, unclassified and missing comparison data', () => {
  const rows = [reportRow('1001', 'A', 1, 5), reportRow('1002', 'A', 2, 3), reportRow('1003', 'B', 3, 2), reportRow('1004', '未分類', 4, 1), reportRow('1005', 'C', 5, null), reportRow('1006', 'A', 6, null, null)];
  const report = weeklyReportGroups(rows, 20);
  assert.deepEqual(report.entries.map(entry => entry.kind), ['resonant', 'resonant', 'single', 'unclassified', 'pending']);
  assert.equal(report.groups[0].delta, 4);
  assert.equal(report.groups[0].members.length, 2);
  assert.equal(report.groups.find(group => group.name === 'C').rank, null);
});
test('multiple entries outside the top ten groups remain outside, and top limit affects membership', () => {
  const rows = Array.from({ length: 12 }, (_, i) => reportRow(String(1100 + i), `group${i}`, i + 1, 12 - i));
  rows.push(reportRow('1200', 'group11', 13, 1));
  assert.deepEqual(weeklyReportGroups(rows).entries.slice(-2).map(entry => entry.kind), ['outside', 'outside']);
  assert.equal(weeklyReportGroups(rows, 10).entries.length, 10);
});
test('pagination shows all matching rows, clamps stale pages and handles empty results', () => {
  const rows = Array.from({ length: 51 }, (_, i) => i);
  const pages = [1, 2, 3].flatMap(page => weeklyPage(rows, page, 20).rows);
  assert.deepEqual(pages, rows);
  assert.equal(weeklyPage(rows, 99, 50).page, 2);
  assert.deepEqual(weeklyPage([], 5, 20), { page: 1, pages: 1, rows: [] });
});
