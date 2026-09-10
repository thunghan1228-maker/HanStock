import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as detector from '../lib/black-dragon-intraday.ts';
import * as session from '../lib/black-dragon-session.ts';
import * as candles from '../lib/black-dragon-candles.ts';

const day = '2026-09-07', at = time => Date.parse(`${day}T${time}:00+08:00`);
const base = { modelVersion: detector.BLACK_DRAGON_INTRADAY_MODEL_VERSION, code: '3532', name: '台勝科', market: 'twse', targetDate: day, completedThrough: '2026-09-04', previousClose: 399, averageVolume20d: 1000000,
  maLiveBaseSums: { 5: 2000, 10: 3900, 20: 7200, 60: 18000, 120: 30000, 240: 48000 }, previousMaValues: { 5: 400, 10: 390, 20: 360, 60: 300, 120: 250, 240: 200 }, referenceHighs: { 5: 408.5 } };
const opening = { ts: at('09:00'), open: 401, high: 402, low: 396, close: 396.5, volume: 100000 };
const trigger = { ts: at('11:00'), open: 405, high: 409, low: 399, close: 400, volume: 100000 };
const valid = { ...detector.findFirstIntradayBlackDragonSignal(base, [opening, trigger], day), signalType: 'black-dragon', strategyKind: 'blackDragon', selectionRuleVersion: session.BLACK_DRAGON_SELECTION_VERSION };

function route(options = {}) {
  let stored = options.stored ?? [], finished = null, saved = [];
  const dependencies = {
    'db/technical-market-indicators': { readTechnicalMarketIndicators: async () => [{ code: '3532', payload: { blackDragonIntradayBase: options.base ?? base } }] },
    'db/black-dragon-intraday': {
      readBlackDragonIntradayScanState: async () => finished,
      claimBlackDragonIntradayBatch: async () => ({ tradeDate: day, status: 'running', startIndex: 0, nextIndex: 0, processed: 0, total: 1, cycle: 1, updatedAt: at('15:00') }),
      finishBlackDragonIntradayBatch: async value => { finished = value; return true; },
    },
    'db/river-radar-intraday': {
      readRiverStrategySignals: async () => stored,
      saveRiverStrategySignals: async rows => { saved = rows; if (options.writeFails) return false; stored = rows; return true; },
    },
    'db/kline-snapshots': { readKlineSnapshot: async () => ({ value: { candles: options.missingOpen ? [] : [opening] } }) },
    'lib/black-dragon-intraday': detector,
    'lib/black-dragon-candles': candles,
    'lib/black-dragon-session': { ...session, isBlackDragonScanAllowed: () => true },
    'lib/daily-strategy-signals': { isListedOrOtcStockCode: code => /^\d{4}$/.test(code) },
    'lib/intraday-signal-session': { loadTwseClosedTradingDates: async () => [], resolveIntradaySignalCutoverDate: date => date.getTime() === at('08:00') ? '2026-09-04' : day },
    'lib/stock-primary-group': { parseHanStockOfficialPrimaryGroupMap: () => new Map([['3532', '矽晶圓']]) },
    'data/stock_groups.py?raw': { default: '' },
  };
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../app/api/black-dragon-intraday/route.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports, Response, AbortSignal,
    Date: class extends Date { static now() { return at('15:00'); } },
    fetch: async () => Response.json([{ result: { data: { json: { candles: [trigger] } } } }]),
    require: name => {
      const key = Object.keys(dependencies).find(key => name.endsWith(key));
      if (!key) throw new Error(`Unexpected import ${name}`);
      return dependencies[key];
    },
  });
  return { get: query => exports.GET({ nextUrl: new URL(`https://example.test/api/black-dragon-intraday?${query}`) }), state: () => ({ finished, saved }) };
}

test('fast reads exclude legacy candidates and candidates without current numeric evidence', async () => {
  const api = route({ stored: [valid, { ...valid, selectionRuleVersion: 'black-dragon-intraday-volume-v3' }, { ...valid, signalHigh: 405 }, { ...valid, openingBarTs: at('12:20') }] });
  const payload = await (await api.get('fast=1')).json();
  assert.equal(payload.signals.length, 1);
  assert.equal(payload.signals[0].referenceHigh5, 408.5);
});

test('backfill combines saved opening with a truncated current feed and persists matching evidence', async () => {
  const api = route();
  const response = await api.get('backfill=1');
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.signals.length, 1);
  assert.equal(payload.signals[0].barTs, at('11:00'));
  assert.equal(payload.signals[0].sessionOpen, 401);
  assert.equal(api.state().saved.length, 1);
  const missing = route({ missingOpen: true });
  assert.equal((await (await missing.get('backfill=1')).json()).signals.length, 0);
  const stale = route({ base: { ...base, completedThrough: '2026-09-03' } });
  assert.equal((await (await stale.get('backfill=1')).json()).signals.length, 0);
});

test('failed persistence remains an error and is never reported as a completed scan', async () => {
  const api = route({ writeFails: true });
  const response = await api.get('backfill=1');
  assert.equal(response.status, 503);
  assert.equal(api.state().finished.status, 'error');
  assert.equal((await response.json()).ok, false);
});
