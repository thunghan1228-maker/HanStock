import test from 'node:test';
import assert from 'node:assert/strict';
import { readDailyCandleSlots, alignDailyForce, retainDailyForcePoints } from '../lib/kline-daily-force.ts';

function chartFixture() {
  const candles = [
    { date: '2026/08/12', left: 200, width: 8 },
    { date: '2026/08/13', left: 220, width: 8 },
    { date: '2026/08/14', left: 240, width: 8 },
    { date: '2026/08/17', left: 260, width: 8 },
  ];
  const nodes = candles.map(candle => ({
    getAttribute: () => candle.date,
    querySelector: () => ({ getBoundingClientRect: () => candle }),
  }));
  const frame = { clientLeft: 2, offsetWidth: 1000,
    getBoundingClientRect: () => ({ left: 40, width: 1000 }),
    contentDocument: { querySelectorAll: selector => selector === 'g[data-hanstock-bar-date]' ? nodes : [] },
    // No visible-candle global or intraday volume rectangles exist on this daily layout.
    contentWindow: {},
  };
  const plot = { clientLeft: 1, clientWidth: 800, offsetWidth: 802,
    getBoundingClientRect: () => ({ left: 10, width: 802 }) };
  return { candles, frame, plot };
}

test('daily price candles provide dates and x positions without intraday volume nodes', () => {
  const { frame, plot } = chartFixture();
  const slots = readDailyCandleSlots(frame, plot);
  assert.equal(slots.length, 4);
  assert.deepEqual(slots[1], { date: '2026-08-13', x: 318.75, width: 10 });
  assert.equal(slots[1].x / 1000 * plot.clientWidth + 11, 42 + 224);
});

test('sparse history leaves earlier/missing days empty and preserves zero net as real data', () => {
  const { frame, plot } = chartFixture();
  const slots = readDailyCandleSlots(frame, plot);
  const { aligned, total } = alignDailyForce(slots, [
    { date: '2026-08-13', net: 20 },
    { date: '2026/08/17', net: 0 },
  ]);
  assert.equal(aligned[0], null);
  assert.equal(aligned[2], null);
  assert.equal(aligned[1].x, slots[1].x);
  assert.equal(aligned[3].x, slots[3].x);
  assert.equal(aligned[3].net, 0);
  assert.equal(aligned[3].cumulative, 20);
  assert.equal(total, 20);
});

test('pan, zoom and resize move each force bar with its matching candle', () => {
  const { frame, plot, candles } = chartFixture();
  const before = readDailyCandleSlots(frame, plot);
  candles.forEach(candle => { candle.left -= 50; candle.width = 16; });
  const moved = readDailyCandleSlots(frame, plot);
  assert.equal(moved[1].width, 20);
  assert.equal(moved[1].x - before[1].x, -57.5);
  plot.clientWidth = 400;
  plot.offsetWidth = 402;
  plot.getBoundingClientRect = () => ({ left: 10, width: 402 });
  const resized = readDailyCandleSlots(frame, plot);
  assert.equal(resized[1].x, moved[1].x * 2);
  assert.equal(resized[1].width, moved[1].width * 2);
});

test('scaled iframe and plot coordinates still align in screen pixels', () => {
  const { frame, plot } = chartFixture();
  frame.getBoundingClientRect = () => ({ left: 50, width: 1500 });
  plot.getBoundingClientRect = () => ({ left: 10, width: 1203 });
  const slot = readDailyCandleSlots(frame, plot)[1];
  const screenX = 11.5 + slot.x / 1000 * 1200;
  assert.equal(screenX, 53 + 224 * 1.5);
  assert.equal(slot.width / 1000 * 1200, 12);
});

test('year boundaries match full dates, not another year with the same month/day', () => {
  const slots = ['2025-12-31', '2026-01-02', '2026-12-31'].map((date, i) => ({ date, x: i * 10, width: 5 }));
  const { aligned, total } = alignDailyForce(slots, [
    { date: '2025/12/31', net: 10 }, { date: '2026-01-02', net: -25 },
  ]);
  assert.equal(aligned[1].cumulative, -15);
  assert.ok(aligned[0].y < aligned[1].y);
  assert.equal(aligned[2], null);
  assert.equal(total, -15);
});

test('switching through intraday or a hidden iframe produces no misplaced daily bars', () => {
  const { frame, plot, candles } = chartFixture();
  candles.forEach(candle => { candle.date = '09/07 13:25'; });
  assert.deepEqual(readDailyCandleSlots(frame, plot), []);
  plot.clientWidth = 0;
  assert.deepEqual(readDailyCandleSlots(frame, plot), []);
});

test('missing days connect known cumulative endpoints without manufacturing bars', () => {
  const dates = ['2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07'];
  const slots = dates.map((date, i) => ({ date, x: 600 + i * 20, width: 7 }));
  const { aligned, segments, total } = alignDailyForce(slots, [
    { date: dates[6], net: 30 }, { date: dates[1], net: -20 }, { date: dates[5], net: 10 },
  ]);
  assert.equal(total, 20);
  assert.equal(segments.length, 2);
  assert.deepEqual(segments[0].missingDates, dates.slice(2, 5));
  assert.equal(segments[0].from.x, 620);
  assert.equal(segments[0].to.x, 700);
  assert.deepEqual(segments[1].missingDates, []);
  assert.ok(aligned.slice(2, 5).every(row => row === null));
  assert.equal(aligned[0], null);
});

test('daily refresh retains older dates during empty or partial responses', () => {
  const before = [{date:'2026-09-01',net:10},{date:'2026-09-02',net:-20}];
  assert.deepEqual(retainDailyForcePoints(before, []), before);
  assert.deepEqual(retainDailyForcePoints(before, [{date:'2026-09-02',net:0},{date:'2026-09-03',net:5}]),
    [before[0],{date:'2026-09-02',net:0},{date:'2026-09-03',net:5}]);
});
