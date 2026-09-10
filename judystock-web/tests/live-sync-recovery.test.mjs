import test from 'node:test';
import {withMarketRequestScope} from '../lib/market-request-scope.ts';
import assert from 'node:assert/strict';
import { timedSingleFlight } from '../lib/timed-single-flight.ts';
import { fetchBoundedMarketJson, mapMarketBatches } from '../lib/bounded-market-data.ts';
import { liveSyncState } from '../lib/live-sync-status.ts';

test('slow scans remain single-flight beyond their cache TTL and recover after failure', async () => {
  let resolve, calls = 0;
  const load = timedSingleFlight(1, async () => { calls++; return new Promise(r => { resolve = r; }); });
  const first = load();
  await new Promise(r => setTimeout(r, 10));
  const second = load();
  assert.equal(first, second);
  resolve(42);
  assert.deepEqual(await Promise.all([first, second]), [42, 42]);
  assert.equal(calls, 1);
  let attempts = 0;
  const retry = timedSingleFlight(1000, async () => { if (++attempts === 1) throw Error('upstream'); return 9; });
  await assert.rejects(retry(), /upstream/);
  assert.equal(await retry(), 9);
  assert.equal(await retry(), 9);
  assert.equal(attempts, 2);
});

test('market batches cap concurrency, preserve every symbol and continue after a failed batch', async () => {
  let running = 0, maximum = 0;
  const result = await mapMarketBatches(Array.from({length: 21}, (_, i) => i), async i => {
    maximum = Math.max(maximum, ++running);
    await new Promise(r => setTimeout(r, 1));
    running--;
    if (i === 7) throw Error('failed batch');
    return i;
  });
  assert.equal(maximum, 2);
  assert.equal(result.length, 21);
  assert.equal(result[7].status, 'rejected');
  assert.equal(result[20].value, 20);
});

test('oversized or invalid candle responses release the shared fetch slots for later updates', async t => {
  let running = 0, maximum = 0;
  t.mock.method(globalThis, 'fetch', async url => {
    maximum = Math.max(maximum, ++running);
    await new Promise(r => setTimeout(r, 2));
    running--;
    if (url === 'oversized') return new Response('x'.repeat(4 * 1024 * 1024 + 1));
    if (url === 'invalid') return new Response('bad-json');
    return Response.json({ok:true});
  });
  const result = await withMarketRequestScope(() => Promise.allSettled(['oversized','invalid','valid','valid','valid'].map(url => fetchBoundedMarketJson(url, {}))));
  assert.equal(maximum, 2);
  assert.match(result[0].reason.message, /too-large/);
  assert.equal(result[1].status, 'rejected');
  assert.deepEqual(result.slice(2).map(row => row.value), [{ok:true},{ok:true},{ok:true}]);
});

test('a fresh snapshot cannot disguise missing, previous-session or delayed market data', () => {
  const now = Date.parse('2026-09-07T09:15:00+08:00');
  const status = extra => liveSyncState({now, polledAt: now, checkedAt: now, sourceAt: 0, ...extra});
  assert.equal(status({}).label, '行情來源延遲，持續重試');
  assert.equal(status({sourceAt: Date.parse('2026-09-04T13:30:00+08:00')}).warning, true);
  assert.equal(status({sourceAt: now - 60_000}).label, '持續同步');
  assert.equal(status({sourceAt: now, error:true}).label, '同步延遲，正在重試');
  assert.equal(status({polledAt: now - 31_000, sourceAt: now}).warning, true);
});
