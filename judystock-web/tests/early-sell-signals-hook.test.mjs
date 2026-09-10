import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';

const snapshotUrl = '/api/daytrade-early-sell?limit=5000&snapshot=1';
const collectorUrl = '/api/daytrade-early-sell?collector=1';
const tradeDate = '2026-09-07';
const now = Date.parse(`${tradeDate}T10:00:00+08:00`);
const response = (body, ok = true) => ({ ok, json: async () => body });
const snapshot = (overrides = {}) => ({ ok: true, tradeDate, signals: [], dates: [tradeDate], ...overrides });
const signal = (overrides = {}) => ({ tradeDate, ticker: '2330', name: '台積電', kind: 'daytradeEarlyBuy50', label: '盤中大單買進', barTs: now - 60_000, price: 100, note: '', ...overrides });
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const compiled = new Map();

// Deterministic hook lifecycle and browser boundaries; all business functions,
// including the visibility utility and imported filters, execute from source.
// No production API, wall-clock timer, or DOM renderer is involved.
function harness({ initialNow = now, storage = {}, fetcher, props = {}, search = '' } = {}) {
  let clock = initialNow, cursor = 0, dirty = true, mounted = true, nextTimer = 0, output;
  let lateStateWrites = 0;
  const slots = [], pendingEffects = [], intervals = new Map(), timeouts = new Map(), listeners = new Set();
  const requests = [], localStorage = new Map(Object.entries(storage)), positions = [], sizes = [];
  let options = {
    centerOpen: false, centerMode: 'today',
    setPopupPosition: value => positions.push(value), setPopupSize: value => sizes.push(value),
    formatTwd: value => String(value), formatWatchlistPrice: (value, fallback) => value == null ? fallback : String(value),
    ...props,
  };
  const sameDeps = (a, b) => a !== undefined && b !== undefined && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) {
        const slot = { value: typeof initial === 'function' ? initial() : initial };
        slot.set = next => {
          if (!mounted) { lateStateWrites++; return; }
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (!Object.is(value, slot.value)) { slot.value = value; dirty = true; }
        };
        slots[index] = slot;
      }
      return [slots[index].value, slots[index].set];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !sameDeps(deps, slots[index].deps)) slots[index] = { value: callback(), deps };
      return slots[index].value;
    },
    useEffect(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !sameDeps(deps, slots[index].deps)) {
        const previous = slots[index];
        const slot = slots[index] = { deps };
        pendingEffects.push(() => { previous?.cleanup?.(); slot.cleanup = callback(); });
      }
    },
  };
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  const document = {
    visibilityState: 'visible',
    addEventListener(name, callback) { assert.equal(name, 'visibilitychange'); listeners.add(callback); },
    removeEventListener(name, callback) { assert.equal(name, 'visibilitychange'); listeners.delete(callback); },
  };
  const window = {
    location: { search },
    localStorage: { getItem: key => localStorage.get(key) ?? null, setItem: (key, value) => localStorage.set(key, value), removeItem: key => localStorage.delete(key) },
    setInterval(callback, ms) { intervals.set(++nextTimer, { callback, ms, next: clock + ms }); return nextTimer; },
    clearInterval: id => intervals.delete(id),
    setTimeout(callback, ms) { timeouts.set(++nextTimer, { callback, ms, next: clock + ms }); return nextTimer; },
    clearTimeout: id => timeouts.delete(id),
  };
  const context = vm.createContext({
    window, document, Date: ClockDate, URLSearchParams, AbortController, AbortSignal, console,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (fetcher) { const result = await fetcher(url, options); if (result !== undefined) return result; }
      if (url === snapshotUrl) return response(snapshot());
      return response({ ok: false, signals: [], rows: [], stocks: [] }, false);
    },
  });
  const modules = new Map();
  function load(url) {
    const filename = fileURLToPath(url);
    if (modules.has(filename)) return modules.get(filename);
    if (!compiled.has(filename)) compiled.set(filename, ts.transpileModule(readFileSync(filename, 'utf8'), {
      fileName: filename.replace(/\.mjs$/, '.ts'),
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText);
    const exports = {}; modules.set(filename, exports);
    vm.runInContext(`(function(exports, require) { ${compiled.get(filename)}\n})`, context, { filename })(exports, specifier => {
      if (specifier === 'react') return react;
      assert.ok(specifier.startsWith('.'), `Unexpected dependency: ${specifier}`);
      let dependency = new URL(specifier, url);
      if (!existsSync(dependency)) dependency = new URL(`${specifier}.ts`, url);
      return load(dependency);
    });
    return exports;
  }
  const { useEarlySellSignals } = load(new URL('../app/hooks/useEarlySellSignals.ts', import.meta.url));
  const keys = load(new URL('../lib/early-sell-signals.ts', import.meta.url));
  function render() {
    cursor = 0; dirty = false; output = useEarlySellSignals(options);
    for (const effect of pendingEffects.splice(0)) effect();
  }
  async function flush() {
    for (let pass = 0; pass < 40; pass++) {
      if (dirty && mounted) render();
      // Drain async fetch/json/Promise.allSettled continuations between renders.
      for (let i = 0; i < 12; i++) await Promise.resolve();
      if (!dirty) return;
    }
    throw new Error('Hook failed to settle');
  }
  return {
    get value() { return output; }, get lateStateWrites() { return lateStateWrites; },
    requests, intervals, timeouts, listeners, localStorage, positions, sizes, keys, flush,
    async update(props) { options = { ...options, ...props }; dirty = true; await flush(); },
    async fire(ms) { for (const timer of [...intervals.values()]) if (timer.ms === ms) timer.callback(); await flush(); },
    async visibility(state) { document.visibilityState = state; for (const listener of [...listeners]) listener(); await flush(); },
    async advance(ms) {
      const end = clock + ms;
      while (true) {
        const due = [...[...intervals].map(([id, timer]) => ({ id, timer, interval: true })), ...[...timeouts].map(([id, timer]) => ({ id, timer, interval: false }))]
          .filter(({ timer }) => timer.next <= end).sort((a, b) => a.timer.next - b.timer.next)[0];
        if (!due) break;
        clock = due.timer.next;
        if (due.interval) due.timer.next += due.timer.ms; else timeouts.delete(due.id);
        due.timer.callback(); await flush();
      }
      clock = end; await flush();
    },
    unmount() { mounted = false; for (const slot of slots) slot?.cleanup?.(); },
  };
}

test('hook merges four-gate snapshots into today/popups, deduplicates and applies corrected rows', async t => {
  const ordinary = signal(), fourGate = signal({ kind: 'fourGateBullish' });
  let payload = snapshot({ signals: [ordinary, ordinary, signal({ ticker: '9999', kind: 'riverBull' })], fourGateSignals: [fourGate] });
  const env = harness({ fetcher: url => url === snapshotUrl ? response(payload) : undefined }); t.after(() => env.unmount());
  await env.flush();
  assert.equal(env.value.signalSnapshotReady, true);
  assert.deepEqual(Array.from(env.value.combinedTodaySignals, s => s.kind).sort(), ['daytradeEarlyBuy50', 'fourGateBullish']);
  assert.equal(env.value.popupSignals.length, 2);
  payload = snapshot({ signals: [{ ...ordinary, price: 105 }], fourGateSignals: [fourGate] });
  await env.fire(3000);
  assert.equal(env.value.popupSignals.length, 2);
  assert.equal(env.value.todaySignals[0].price, 105);
  env.value.dismissPopupSignals(); await env.flush(); await env.fire(3000);
  assert.equal(env.value.popupSignals.length, 0, 'dismissed keys stay hidden when the snapshot repeats');
  env.value.changeSignalAlerts(false); await env.flush(); await env.fire(3000);
  assert.equal(env.value.queue.length, 0);
  env.value.changeSignalAlerts(true); await env.flush(); await env.fire(3000);
  assert.equal(env.value.popupSignals.length, 2, 're-enabling clears dismissals');
});

test('hook keeps successful data during empty, invalid and failed snapshot refreshes', async t => {
  let current = response(snapshot({ signals: [signal()], signalFeed: { mode: 'minute-bars-fallback', fallbackAt: now }, instantLargeCollector: { candidateCount: 19 } }));
  const env = harness({ fetcher: url => url === snapshotUrl ? current : undefined }); t.after(() => env.unmount());
  await env.flush();
  current = response(snapshot({ signalFeed: { mode: 'minute-bars-fallback-pending' } }));
  await env.fire(3000);
  assert.equal(env.value.todaySignals.length, 1); assert.equal(env.value.popupSignals.length, 1);
  assert.equal(env.value.signalFeed.mode, 'minute-bars-fallback');
  assert.equal(env.value.signalFeed.fallbackAt, now);
  for (const failed of [response({}, false), response({ ok: true })]) {
    current = failed; await env.fire(3000);
    assert.equal(env.value.signalSyncError, true);
    assert.equal(env.value.todaySignals.length, 1);
    assert.equal(env.value.signalSnapshotReady, true);
    assert.equal(env.value.instantLargeCollector.candidateCount, 19);
  }
});

test('manual history date survives polling and existing ranking props reach the collector via its private ref', async t => {
  const ranking = { strong: [{ group: '半導體', rank: 1 }], weak: [] };
  let payload = snapshot({ groupRankings: ranking });
  const env = harness({ fetcher: url => url === snapshotUrl ? response(payload) : undefined }); t.after(() => env.unmount());
  await env.flush();
  assert.deepEqual(plain(env.value.effectiveGroupRankings), ranking);
  env.value.selectHistoryDate('2026-08-14'); await env.flush();
  const newerRanking = { strong: [{ group: '記憶體', rank: 1 }], weak: [] };
  await env.update({ groupRankings: newerRanking });
  payload = snapshot({ tradeDate: '2026-09-08' }); await env.fire(3000);
  assert.equal(env.value.selectedDate, '2026-08-14');
  assert.deepEqual(plain(env.value.effectiveGroupRankings), newerRanking);
  const latest = env.requests.filter(r => r.url === snapshotUrl).at(-1);
  assert.deepEqual(JSON.parse(decodeURIComponent(latest.options.headers['X-HanStock-Group-Rankings'])), newerRanking);
  assert.equal('seenKeys' in env.value, false); assert.equal('load' in env.value, false);
});

test('pinned restore retains the ten-day boundary, excludes expired/demo/retired rows, and restores UI through setters', async t => {
  const clock = Date.parse('2026-08-25T10:00:00+08:00'), boundary = clock - 10 * 86_400_000;
  const rows = [
    signal({ ticker: '1001', barTs: boundary }), signal({ ticker: '1002', barTs: boundary - 1 }),
    signal({ ticker: '1003', barTs: clock - 1000, kind: 'riverBull' }),
    signal({ ticker: '1004', tradeDate: '2026-08-20', barTs: clock - 5 * 86_400_000, kind: 'fourGateBullish', label: '四項通過', demo: true }),
  ];
  const env = harness({ initialNow: clock, fetcher: url => url === snapshotUrl ? response({}, false) : undefined, storage: {
    'hanstock-battle-early-sell-pinned-queue-v1': JSON.stringify(rows),
    'hanstock-battle-early-sell-alerts-enabled-v1': 'false',
    'hanstock-battle-early-sell-toast-position-v1': '{"x":30,"y":40}',
    'hanstock-battle-early-sell-toast-size-v1': '{"width":600,"height":400}',
  } }); t.after(() => env.unmount());
  await env.flush();
  assert.deepEqual(Array.from(env.value.popupSignals, s => s.ticker), ['1001']);
  assert.equal(env.value.signalAlertsEnabled, true);
  assert.equal(env.localStorage.has(env.keys.EARLY_SELL_ALERTS_ENABLED_KEY), false);
  assert.deepEqual(plain(env.positions), [{ x: 30, y: 40 }]);
  assert.deepEqual(plain(env.sizes), [{ width: 600, height: 400 }]);
});

test('snapshot queues and persisted seen keys keep their existing bounds', async t => {
  const rows = Array.from({ length: 2003 }, (_, i) => signal({ ticker: String(1000 + i), barTs: now - 60_000 + i }));
  const env = harness({ fetcher: url => url === snapshotUrl ? response(snapshot({ signals: rows })) : undefined }); t.after(() => env.unmount());
  await env.flush();
  assert.equal(env.value.queue.length, 2000);
  assert.equal(JSON.parse(env.localStorage.get(env.keys.EARLY_SELL_SEEN_KEY)).length, 500);
  assert.equal(JSON.parse(env.localStorage.get(env.keys.EARLY_SELL_PINNED_QUEUE_KEY)).length, 2000);
  await env.fire(3000); assert.equal(env.value.queue.length, 2000);
});

test('history uses the 220 ms debounce, demo fallback and ignores a superseded response', async t => {
  const pending = deferred();
  const env = harness({ fetcher: url => {
    if (url.includes('date=2026-08-14')) return response({ ok: true, signals: [], dates: [] });
    if (url.includes('date=2026-09-04')) return pending.promise;
    if (url.includes('date=2026-09-03')) return response({ ok: true, signals: [signal({ tradeDate: '2026-09-03', ticker: '2303' })] });
  } }); t.after(() => env.unmount());
  await env.flush(); env.value.selectHistoryDate('2026-08-14'); await env.flush();
  await env.update({ centerOpen: true, centerMode: 'history' });
  await env.advance(219); assert.equal(env.requests.some(r => r.url.includes('date=')), false);
  await env.advance(1);
  assert.deepEqual(Array.from(env.value.visibleSignals, s => [s.ticker, s.demo]), [
    ['2330', true], ['2303', true], ['2603', true], ['3231', true], ['2615', true], ['3006', true],
  ]);
  assert.ok(env.requests.filter(r => r.url.includes('date=')).every(r => !new URL(r.url, 'https://site.test').searchParams.has('q')));
  assert.ok(env.value.availableDates.includes('2026-08-14'));
  env.value.selectHistoryDate('2026-09-04'); await env.flush(); await env.advance(220);
  assert.equal(env.value.historyLoading, true);
  env.value.selectHistoryDate('2026-09-03'); await env.flush(); await env.advance(220);
  pending.resolve(response({ ok: true, signals: [signal({ ticker: '9999' })] })); await env.flush();
  assert.deepEqual(Array.from(env.value.visibleSignals, s => s.ticker), ['2303']);
  assert.equal(env.value.historyLoading, false);
});

test('AJ and large-force values retain same-session snapshots on refresh failure', async t => {
  const force = signal({ kind: 'intradayLargeForceBuy', note: '盤中大戶力 +20%｜成交額 4 億' });
  let failed = false;
  const transition = { ticker: '2330', bullishQualifyingGroup: '半導體', bullishCurrentRank: 1, worstRecentRank: 50 };
  const env = harness({ props: { centerMode: 'largeForce' }, fetcher: url => {
    if (url === snapshotUrl) return response(snapshot({ signals: [signal()], largeForceSignals: [force] }));
    if (url.startsWith('/api/intraday-large-force-aj')) return response({ ok: !failed, rows: [transition] }, !failed);
    if (url.startsWith('/api/intraday-large-force-values')) return response({ rows: [{ ticker: '2330', tradeDate, forcePct: 20, barTs: now }] }, !failed);
  } }); t.after(() => env.unmount());
  await env.flush();
  assert.equal(env.value.largeForceAjStatus, 'ready'); assert.equal(env.value.largeForceAjSignals.length, 1);
  assert.equal(env.intervals.size, 11, 'all conditional polling streams are exercised');
  failed = true; await env.fire(60000); await env.fire(30000);
  assert.equal(env.value.largeForceAjStatus, 'ready'); assert.equal(env.value.largeForceAjSignals.length, 1);
  assert.equal(env.value.signalLargeForceValues[`${tradeDate}:2330`].forcePct, 20);
  assert.equal(JSON.parse(env.localStorage.get(env.keys.EARLY_SELL_LARGE_FORCE_VALUES_KEY))[`${tradeDate}:2330`].forcePct, 20);
  await env.visibility('hidden'); assert.equal(env.intervals.size, 0);
  const before = env.requests.length; await env.fire(3000); assert.equal(env.requests.length, before);
  await env.visibility('visible'); assert.equal(env.intervals.size, 11);
  env.unmount();
  assert.equal(env.intervals.size, 0); assert.equal(env.timeouts.size, 0); assert.equal(env.listeners.size, 0);
  assert.ok(env.requests.filter(r => r.url.startsWith('/api/intraday-large-force-aj')).every(r => r.options.signal.aborted));
});

test('unmount cancels initial/history timeouts and ignores a pending snapshot', async () => {
  const pending = deferred();
  const env = harness({ props: { centerOpen: true, centerMode: 'history' }, fetcher: url => url === snapshotUrl ? pending.promise : undefined });
  await env.flush();
  assert.equal(env.timeouts.size, 2); assert.equal(env.intervals.size, 8);
  env.unmount(); pending.resolve(response(snapshot({ signals: [signal()] }))); await env.flush();
  assert.equal(env.lateStateWrites, 0);
  assert.equal(env.timeouts.size, 0); assert.equal(env.intervals.size, 0); assert.equal(env.listeners.size, 0);
});

test('opening shows saved session once; subsequent late backfill stays in history', async t => {
 const old=signal({ticker:'1111',barTs:now-20*60_000});
 const fresh=signal({ticker:'2222'});
 let payload=snapshot();
 const env=harness({fetcher:url=>url===snapshotUrl?response(payload):undefined});t.after(()=>env.unmount());
 await env.flush();
 assert.equal(env.value.popupSignals.length,0,'an empty loading snapshot must not consume initial display');
 payload=snapshot({signals:[old]}); await env.fire(3000);
 assert.deepEqual(Array.from(env.value.popupSignals,s=>s.ticker),['1111'],'opening shows saved signals even when older than two minutes');
 env.value.dismissPopupSignals(); await env.flush();
 const late=signal({ticker:'3333',barTs:now-15*60_000});
 payload=snapshot({signals:[old,late,fresh]}); await env.fire(3000);
 assert.equal(env.value.combinedTodaySignals.length,3);
 assert.deepEqual(Array.from(env.value.popupSignals,s=>s.ticker),['2222'],'later backfills do not reopen or flood the live popup');
});
