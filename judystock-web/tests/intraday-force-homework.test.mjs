import test from "node:test";
import assert from "node:assert/strict";
import { rankIntradayForceHomework } from "../lib/intraday-force-homework.ts";

const row = (ticker, forcePct, group = "半導體", barTs = 100) => ({
  ticker, name: `股票${ticker}`, group, tradeDate: "2026-09-04", forcePct, barTs, price: 100,
});

test("daily homework returns the strongest 20 and weakest 20 across all configured groups", () => {
  const rows = Array.from({ length: 67 }, (_, index) => row(String(1000 + index), index - 33, `族群${index % 67}`));
  const ranked = rankIntradayForceHomework(rows);
  assert.equal(ranked.availableCount, 67);
  assert.equal(ranked.bullish.length, 20);
  assert.equal(ranked.bearish.length, 20);
  assert.equal(ranked.bullish[0].forcePct, 33);
  assert.equal(ranked.bearish[0].forcePct, -33);
});

test("invalid, unclassified and duplicate stale rows never enter the homework ranking", () => {
  const ranked = rankIntradayForceHomework([
    row("2330", 10, "半導體", 200), row("2330", 99, "半導體", 100),
    row("2317", -20, "電子代工", 200), row("2454", 50, "未分類", 200),
    { ...row("3008", 30), forcePct: null },
  ]);
  assert.deepEqual(ranked.bullish.map(item => item.ticker), ["2330", "2317"]);
  assert.deepEqual(ranked.bearish.map(item => item.ticker), ["2317", "2330"]);
});

import { homeworkPriceChange } from '../lib/intraday-force-homework.ts';
test('price changes use the same displayed price and previous close, independently of force direction', () => {
  assert.deepEqual(homeworkPriceChange(105, 100), { change: 5, changePct: 5 });
  assert.deepEqual(homeworkPriceChange(95, 100), { change: -5, changePct: -5 });
  assert.deepEqual(homeworkPriceChange(100, 100), { change: 0, changePct: 0 });
  for (const invalid of [null, undefined, 0, NaN]) assert.deepEqual(homeworkPriceChange(100, invalid), { change: null, changePct: null });
  const ranked = rankIntradayForceHomework([{ ...row('2330', -10), ...homeworkPriceChange(105, 100) }]);
  assert.equal(ranked.bearish[0].changePct, 5);
});
