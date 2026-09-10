import test from "node:test";
import assert from "node:assert/strict";
import { parseManualObservationText, mergeManualObservationRecords, weeklyObservationSelection, CLOSE_OBSERVATION_HOMEWORK, PREOPEN_SHORT_HOMEWORK } from "../lib/manual-observation-homework.ts";

test("parses pasted HiStock homework into release stocks and sector groups", () => {
  const record = parseManualObservationText({
    kind: "preopen-short",
    tradeDate: "2026-09-07",
    text: `**9/7盤前空觀察
**策略：直接打905低，損抓2-3%
出獄股
2455全新(8/31)
鏡頭
3441聯一光
3362先進光
散熱
8996
3017`,
  });

  assert.equal(record.title, "9/7 盤前空觀察");
  assert.deepEqual(record.rules, ["執行可參考 09:05 首根低點，不必先等跌破昨日低；風險控制約 2～3%。"]);
  assert.deepEqual(record.releaseStocks[0], { ticker: "2455", name: "全新", note: "8/31" });
  assert.deepEqual(record.groups.map((group) => [group.name, group.stocks.length]), [["鏡頭", 2], ["散熱", 2]]);
  assert.equal(record.groups[1].stocks[0].name, "高力");
});

test("rejects pasted text with no stock ticker", () => {
  assert.throws(
    () => parseManualObservationText({ kind: "close-observation", tradeDate: "2026-09-06", text: "只有说明，没有股票" }),
    /沒有辨識到股票代號/,
  );
});

test('an empty manual archive still exposes both original lists without changing their dates', () => {
  const records = mergeManualObservationRecords([]);
  assert.deepEqual(records.map(row => [row.kind, row.tradeDate]), [['preopen-short', '2026-09-07'], ['close-observation', '2026-09-06']]);
  assert.equal(records.find(row => row.kind === 'close-observation').groups.flatMap(group => group.stocks).length, 61);
  assert.equal(records.find(row => row.kind === 'preopen-short').groups.flatMap(group => group.stocks).length, 46);
});

test('saved corrections replace the matching original list while other lists and older dates remain', () => {
  const edited = { ...CLOSE_OBSERVATION_HOMEWORK, title: '人工更新', updatedAt: 20, groups: [{name:'測試',stocks:[{ticker:'2313',name:'華通'}]}] };
  const older = { ...edited, tradeDate: '2026-08-30', title: '歷史名單' };
  const stale = { ...edited, title: '舊版修正', updatedAt: 10 };
  const records = mergeManualObservationRecords([edited, older, stale]);
  assert.equal(records.length, 3);
  assert.equal(records.find(row => row.tradeDate === '2026-09-06').title, '人工更新');
  assert.equal(records.find(row => row.tradeDate === '2026-09-07'), PREOPEN_SHORT_HOMEWORK);
  assert.equal(mergeManualObservationRecords(records).length, 3);
});

test('September 4 weekly observations pair September 6 close with September 7 preopen', () => {
  const records = mergeManualObservationRecords([]);
  const close = weeklyObservationSelection(records, 'close-observation', '2026/09/04');
  const preopen = weeklyObservationSelection(records, 'preopen-short', '2026/09/04');
  assert.equal(close.date, '2026-09-06'); assert.equal(close.record, CLOSE_OBSERVATION_HOMEWORK);
  assert.equal(preopen.date, '2026-09-07'); assert.equal(preopen.record, PREOPEN_SHORT_HOMEWORK);
  assert.equal(close.afterWeek, true); assert.equal(preopen.afterWeek, true);
});

test('historical weeks do not silently select later September lists, but explicit dated review is allowed', () => {
  const records = mergeManualObservationRecords([]);
  assert.equal(weeklyObservationSelection(records, 'preopen-short', '2026/08/28').record, undefined);
  const explicit = weeklyObservationSelection(records, 'preopen-short', '2026/08/28', '2026-09-07');
  assert.equal(explicit.date, '2026-09-07'); assert.equal(explicit.afterWeek, true);
  assert.equal(weeklyObservationSelection(records, 'close-observation', '2026/09/04', '2026-09-07').date, '2026-09-06');
});

test('each kind chooses its own closest list and a later week cannot displace the selected week by default', () => {
  const records = mergeManualObservationRecords([
    {...CLOSE_OBSERVATION_HOMEWORK, tradeDate:'2026-09-04'},
    {...CLOSE_OBSERVATION_HOMEWORK, tradeDate:'2026-09-13'},
    {...PREOPEN_SHORT_HOMEWORK, tradeDate:'2026-09-14'},
  ]);
  assert.equal(weeklyObservationSelection(records, 'close-observation', '2026/09/04').date, '2026-09-06');
  assert.equal(weeklyObservationSelection(records, 'close-observation', '2026/09/04', '2026-09-04').date, '2026-09-04');
  assert.equal(weeklyObservationSelection(records, 'preopen-short', '2026/09/04').date, '2026-09-07');
  assert.equal(weeklyObservationSelection(records, 'preopen-short', '2026/09/11').date, '2026-09-14');
});
