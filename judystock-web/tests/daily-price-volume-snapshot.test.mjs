import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDailyPriceVolume, loadDailyPriceVolumeSnapshot } from '../lib/daily-price-volume-snapshot.ts';

test('daily close snapshots parse both official schemas and convert shares to lots', () => {
  const twse = parseDailyPriceVolume([{ Date: '1150907', Code: '2330', ClosingPrice: '2460.00', TradeVolume: '26,898,329' }], 'twse');
  const tpex = parseDailyPriceVolume([{ Date: '1150908', SecuritiesCompanyCode: '6187', Close: '1385.00', TradingShares: '3668072' }], 'tpex');
  assert.deepEqual(twse.get('2330'), { closePrice: 2460, volumeLots: 26898.329, priceDate: '2026-09-07' });
  assert.deepEqual(tpex.get('6187'), { closePrice: 1385, volumeLots: 3668.072, priceDate: '2026-09-08' });
});

test('missing price or volume stays unknown while an actual zero volume stays zero', () => {
  const rows = parseDailyPriceVolume([
    { Date: '1150908', Code: '2330', ClosingPrice: '--', TradeVolume: '0' },
    { Date: '1150908', Code: '2345', ClosingPrice: '', TradeVolume: null },
    { Date: '1159999', Code: '2346', ClosingPrice: '10', TradeVolume: '1' },
    { Date: '1150230', Code: '2347', ClosingPrice: '10', TradeVolume: '1' },
  ], 'twse');
  assert.equal(rows.get('2330').closePrice, null);
  assert.equal(rows.get('2330').volumeLots, 0);
  assert.equal(rows.get('2345').volumeLots, null);
  assert.equal(rows.size, 2);
});

test('duplicate daily rows keep the latest date regardless of source ordering', () => {
  const rows = parseDailyPriceVolume([
    { Date: '20260908', Code: '2330', ClosingPrice: '20', TradeVolume: '2000' },
    { Date: '1150907', Code: '2330', ClosingPrice: '10', TradeVolume: '1000' },
  ], 'twse');
  assert.equal(rows.get('2330').priceDate, '2026-09-08');
  assert.equal(rows.get('2330').closePrice, 20);
  assert.equal(parseDailyPriceVolume({ error: 'unavailable' }, 'twse').size, 0);
});

test('snapshot requests coalesce, cache per market, and retry partial failure without deleting prior data', async (t) => {
  let now = 1_000_000;
  let failTpex = false;
  const calls = { twse: 0, tpex: 0 };
  t.mock.method(Date, 'now', () => now);
  t.mock.method(console, 'warn', () => {});
  t.mock.method(globalThis, 'fetch', async (url) => {
    const market = url.includes('tpex.org.tw') ? 'tpex' : 'twse';
    calls[market]++;
    if (market === 'tpex' && failTpex) throw new Error('source unavailable');
    await Promise.resolve();
    return Response.json(Array.from({ length: 100 }, (_, i) => market === 'twse'
      ? { Date: '1150908', Code: String(2000 + i), ClosingPrice: '20', TradeVolume: '2000' }
      : { Date: '1150908', SecuritiesCompanyCode: String(6000 + i), Close: '30', TradingShares: '3000' }));
  });
  const [a, b] = await Promise.all([loadDailyPriceVolumeSnapshot(), loadDailyPriceVolumeSnapshot()]);
  assert.deepEqual(calls, { twse: 1, tpex: 1 });
  assert.strictEqual(a.tpex, b.tpex);
  now += 14 * 60_000;
  await loadDailyPriceVolumeSnapshot();
  assert.deepEqual(calls, { twse: 1, tpex: 1 });
  now += 60_001;
  failTpex = true;
  const partial = await loadDailyPriceVolumeSnapshot();
  assert.strictEqual(partial.tpex, a.tpex);
  assert.deepEqual(calls, { twse: 2, tpex: 2 });
  now += 59_000;
  await loadDailyPriceVolumeSnapshot();
  assert.deepEqual(calls, { twse: 2, tpex: 2 });
  now += 1_001;
  failTpex = false;
  await loadDailyPriceVolumeSnapshot();
  assert.deepEqual(calls, { twse: 2, tpex: 3 });
});
