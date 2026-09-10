import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import { DatabaseSync } from 'node:sqlite';
import * as session from '../lib/black-dragon-session.ts';

const require = createRequire(import.meta.url);
function moduleAt(path, dependencies, globals = {}) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, Response, ...globals, require: name => {
    const key = Object.keys(dependencies).find(key => name.endsWith(key));
    return key ? dependencies[key] : require(name);
  } });
  return exports;
}

test('Taipei 10:59:59 is blocked, 11:00 starts, and after-close backfill remains available', () => {
  const at = time => Date.parse(`2026-09-07T${time}+08:00`);
  assert.equal(session.isBlackDragonScanAllowed(at('10:59:59')), false);
  assert.equal(session.isBlackDragonSignalWindow(at('10:59:59')), false);
  assert.equal(session.isBlackDragonScanAllowed(at('11:00:00')), true);
  assert.equal(session.isBlackDragonSignalWindow(at('11:00:00')), true);
  assert.equal(session.isBlackDragonSignalWindow(at('13:30:00')), true);
  assert.equal(session.isBlackDragonSignalWindow(at('14:30:00')), false);
  assert.equal(session.isBlackDragonScanAllowed(at('14:30:00')), true);
  assert.equal(session.isBlackDragonScanAllowed(NaN), false);
});

test('normal, fast and forced-backfill API calls cannot start a pre-11 scan', async () => {
  let indicatorsRead = 0;
  let clock = Date.parse('2026-09-07T10:59:59+08:00');
  const { GET } = moduleAt('../app/api/black-dragon-intraday/route.ts', {
    'db/technical-market-indicators': { readTechnicalMarketIndicators: async () => { indicatorsRead++; return []; } },
    'db/black-dragon-intraday': { readBlackDragonIntradayScanState: async () => null, claimBlackDragonIntradayBatch: async () => null },
    'db/river-radar-intraday': { readRiverStrategySignals: async () => [] },
    'lib/black-dragon-intraday': {},
    'lib/black-dragon-candles': {},
    'db/kline-snapshots': {},
    'lib/black-dragon-session': { ...session, isBlackDragonScanAllowed: () => session.isBlackDragonScanAllowed(clock) },
    'lib/daily-strategy-signals': {},
    'lib/intraday-signal-session': { loadTwseClosedTradingDates: async () => [], resolveIntradaySignalCutoverDate: () => '2026-09-07' },
    'lib/stock-primary-group': { parseHanStockOfficialPrimaryGroupMap: () => new Map() },
    'data/stock_groups.py?raw': { default: '' },
  }, { Date: class extends Date { static now() { return clock; } } });
  for (const query of ['', '?fast=1', '?backfill=1']) {
    const response = await GET({ nextUrl: new URL(`https://example.test/api/black-dragon-intraday${query}`) });
    const payload = await response.json();
    assert.equal(payload.refreshSkipped, 'before-1100');
    assert.deepEqual(payload.signals, []);
  }
  assert.equal(indicatorsRead, 0);
  clock = Date.parse('2026-09-07T11:00:00+08:00');
  await GET({ nextUrl: new URL('https://example.test/api/black-dragon-intraday') });
  assert.equal(indicatorsRead, 1);
});

test('new black-dragon rule replaces a stored 09:00 trigger, then keeps the first eligible trigger', async () => {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec(`CREATE TABLE river_radar_strategy_signals (
    trade_date TEXT, stock_code TEXT, strategy_kind TEXT, direction TEXT, bar_ts INTEGER,
    score REAL, payload_json TEXT, updated_at INTEGER,
    PRIMARY KEY (trade_date, stock_code, strategy_kind))`);
  const d1 = {
    prepare: sql => ({ bind: (...values) => ({ run: () => sqlite.prepare(sql).run(...values) }) }),
    batch: async statements => statements.map(statement => statement.run()),
  };
  const { saveRiverStrategySignals } = moduleAt('../db/river-radar-intraday.ts', {}, { __HANSTOCK_DB: d1 });
  const signal = { tradeDate: '2026-09-07', code: '2330', strategyKind: 'blackDragon', direction: 'bear', score: 12 };
  const save = (time, version) => saveRiverStrategySignals([{ ...signal,
    barTs: Date.parse(`2026-09-07T${time}+08:00`), selectionRuleVersion: version }]);
  try {
    await save('09:00:00', 'black-dragon-intraday-volume-v2');
    await save('11:05:00', session.BLACK_DRAGON_SELECTION_VERSION);
    await save('11:10:00', session.BLACK_DRAGON_SELECTION_VERSION);
    const stored = sqlite.prepare('SELECT bar_ts,payload_json FROM river_radar_strategy_signals').get();
    assert.equal(JSON.parse(stored.payload_json).barTs, stored.bar_ts, 'payload and timestamp remain from the same signal');
    assert.equal(stored.bar_ts, Date.parse('2026-09-07T11:05:00+08:00'));
  } finally { sqlite.close(); }
});

test('multiple windows cannot steal an active scan and an expired scan retries its unfinished batch', async () => {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec('CREATE TABLE river_radar_config (config_key TEXT PRIMARY KEY, config_json TEXT, engine_version TEXT, updated_at INTEGER)');
  let clock = 100000;
  const d1 = { prepare: sql => ({ bind: (...values) => ({
    first: async () => sqlite.prepare(sql).get(...values) ?? null,
    run: async () => ({ meta: { changes: sqlite.prepare(sql).run(...values).changes } }),
  }) }) };
  const scan = moduleAt('../db/black-dragon-intraday.ts', { 'lib/black-dragon-session': session }, { __HANSTOCK_DB: d1, Date: class extends Date { static now() { return clock; } } });
  try {
    const original = await scan.claimBlackDragonIntradayBatch('2026-09-07', 160, 80);
    assert.equal(original.startIndex, 0);
    clock += 5000;
    assert.equal(await scan.claimBlackDragonIntradayBatch('2026-09-07', 160, 80), null, '4-second refresh cooldown cannot bypass the running lease');
    clock += 55000;
    const replacement = await scan.claimBlackDragonIntradayBatch('2026-09-07', 160, 80);
    assert.equal(replacement.startIndex, 0, 'expired work is retried rather than skipped');
    assert.equal(await scan.finishBlackDragonIntradayBatch({ ...original, status: 'ready', processed: 80 }), false, 'late completion cannot overwrite its replacement');
    assert.equal(await scan.finishBlackDragonIntradayBatch({ ...replacement, status: 'ready', processed: 80 }), true);
    clock += 5000;
    const second = await scan.claimBlackDragonIntradayBatch('2026-09-07', 160, 80);
    assert.equal(second.startIndex, 80);
    await scan.finishBlackDragonIntradayBatch({ ...second, status: 'error' });
    clock += 5000;
    const retry = await scan.claimBlackDragonIntradayBatch('2026-09-07', 160, 80);
    assert.equal(retry.startIndex, 80, 'failed writes retry the same batch');
    assert.equal(retry.processed, 80);
  } finally { sqlite.close(); }
});
