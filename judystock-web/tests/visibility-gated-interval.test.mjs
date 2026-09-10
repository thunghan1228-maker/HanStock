import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../lib/useVisibilityGatedInterval.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function harness(initial = 'visible') {
  let now = 0, nextId = 0;
  const intervals = new Map(), listeners = new Set(), scheduledMs = [];
  const document = {
    visibilityState: initial,
    addEventListener: (name, listener) => { assert.equal(name, 'visibilitychange'); listeners.add(listener); },
    removeEventListener: (name, listener) => { assert.equal(name, 'visibilitychange'); listeners.delete(listener); },
  };
  const window = {
    setInterval: (callback, ms) => {
      scheduledMs.push(ms);
      intervals.set(++nextId, { callback, ms, next: now + ms });
      return nextId;
    },
    clearInterval: id => intervals.delete(id),
  };
  const exports = {};
  vm.runInNewContext(compiled, { exports, window, document });
  return {
    create: exports.createVisibilityGatedInterval, intervals, listeners, scheduledMs, document,
    visibility(state) { document.visibilityState = state; for (const listener of [...listeners]) listener(); },
    advance(ms) {
      const until = now + ms;
      while (true) {
        const due = [...intervals.values()].filter(timer => timer.next <= until).sort((a, b) => a.next - b.next)[0];
        if (!due) break;
        now = due.next;
        due.next += due.ms;
        due.callback();
      }
      now = until;
    },
  };
}

test('visible creation keeps the original first delay and interval frequency', () => {
  const env = harness(); let calls = 0;
  const timer = env.create(() => { calls++; }, 3000);
  assert.equal(calls, 0);
  env.advance(2999); assert.equal(calls, 0);
  env.advance(1); assert.equal(calls, 1);
  env.advance(6000); assert.equal(calls, 3);
  assert.deepEqual(env.scheduledMs, [3000]);
  timer.cancel();
});

test('hidden creation allocates no interval; return refreshes once and resumes normal cadence', () => {
  const env = harness('hidden'); let calls = 0;
  const timer = env.create(() => { calls++; }, 5000);
  assert.equal(env.intervals.size, 0);
  env.advance(60000); assert.equal(calls, 0);
  env.visibility('visible'); assert.equal(calls, 1);
  env.visibility('visible'); assert.equal(calls, 1, 'duplicate visible events do not refresh twice');
  env.advance(4999); assert.equal(calls, 1);
  env.advance(1); assert.equal(calls, 2);
  env.visibility('hidden'); assert.equal(env.intervals.size, 0);
  env.advance(60000); assert.equal(calls, 2);
  env.visibility('visible'); assert.equal(calls, 3, 'no catch-up burst for missed background ticks');
  assert.equal(env.intervals.size, 1);
  assert.deepEqual(env.scheduledMs, [5000, 5000]);
  timer.cancel();
});

test('queued ticks cannot run while hidden or after cancellation', () => {
  const env = harness(); let calls = 0;
  const timer = env.create(() => { calls++; }, 1000);
  const queued = [...env.intervals.values()][0].callback;
  env.visibility('hidden'); queued();
  assert.equal(calls, 0);
  env.visibility('visible'); assert.equal(calls, 1);
  timer.cancel(); queued();
  assert.equal(calls, 1);
});

test('all non-visible states pause, even when a tick runs before visibilitychange delivery', () => {
  const env = harness(); let calls = 0;
  const timer = env.create(() => { calls++; }, 1000);
  env.document.visibilityState = 'hidden';
  env.advance(1000);
  assert.equal(calls, 0); assert.equal(env.intervals.size, 0);
  env.visibility('visible'); assert.equal(calls, 1);
  env.visibility('prerender'); assert.equal(env.intervals.size, 0);
  env.visibility('hidden'); assert.equal(calls, 1);
  env.visibility('visible'); assert.equal(calls, 2);
  timer.cancel();
});

test('cancel is idempotent and removes every timer and listener in either visibility state', () => {
  const env = harness(); let calls = 0;
  for (const state of ['visible', 'hidden']) {
    env.visibility(state);
    const timer = env.create(() => { calls++; }, 300000);
    timer.cancel(); timer.cancel();
    assert.equal(env.intervals.size, 0);
    assert.equal(env.listeners.size, 0);
    env.visibility('visible'); env.advance(600000);
  }
  assert.equal(calls, 0);
});

test('cancel or hide inside a resumed callback cannot create a replacement interval', () => {
  const env = harness('hidden');
  const timer = env.create(() => timer.cancel(), 3000);
  env.visibility('visible');
  assert.equal(env.intervals.size, 0); assert.equal(env.listeners.size, 0);
  env.visibility('hidden');
  const hidingTimer = env.create(() => env.visibility('hidden'), 3000);
  env.visibility('visible');
  assert.equal(env.intervals.size, 0);
  hidingTimer.cancel();
});

test('a thrown resume callback still restores the cadence without swallowing the exception', () => {
  const env = harness('hidden'); let calls = 0;
  const error = new Error('callback failure');
  const timer = env.create(() => { if (++calls === 1) throw error; }, 8000);
  assert.throws(() => env.visibility('visible'), value => value === error);
  assert.equal(env.intervals.size, 1);
  env.advance(8000); assert.equal(calls, 2);
  timer.cancel();
});

test('independent intervals and repeated mount/cleanup cycles do not leak listeners', () => {
  const env = harness(); let first = 0, second = 0;
  const a = env.create(() => { first++; }, 3000);
  const b = env.create(() => { second++; }, 10000);
  a.cancel(); env.advance(10000);
  assert.equal(first, 0); assert.equal(second, 1);
  env.visibility('hidden'); env.visibility('visible');
  assert.equal(first, 0); assert.equal(second, 2);
  b.cancel();
  for (let i = 0; i < 30; i++) env.create(() => {}, 5000).cancel();
  assert.equal(env.intervals.size, 0); assert.equal(env.listeners.size, 0);
});

test('server-side creation is a safe no-op', () => {
  const exports = {};
  vm.runInNewContext(compiled, { exports });
  const timer = exports.createVisibilityGatedInterval(() => assert.fail('server callback'), 1000);
  timer.cancel(); timer.cancel();
});
