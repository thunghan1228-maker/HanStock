import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../app/api/technical-market/route.ts', import.meta.url), 'utf8');
const ast = ts.createSourceFile('route.ts', source, ts.ScriptTarget.Latest, true);
const names = new Set(['slashDate', 'mapWithConcurrency', 'mergeSnapshotRows', 'exchangeCounts', 'completeSnapshot', 'fetchWindow']);
const constants = new Set(['FETCH_DATE_CONCURRENCY', 'MIN_TWSE_ROWS', 'MIN_TPEX_ROWS']);
const declarations = ast.statements.filter(node =>
  ts.isFunctionDeclaration(node) && names.has(node.name?.text) ||
  ts.isVariableStatement(node) && node.declarationList.declarations.some(item => constants.has(item.name.getText(ast)))
).map(node => node.getText(ast)).join('\n');
const compiled = ts.transpileModule(declarations, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const date = new Date('2026-09-07T00:00:00Z');
const rows = (market, count, close = 100) => Array.from({ length: count }, (_, index) => ({
  code: String(1000 + index), market, name: `${market}-${index}`, open: 95, close,
}));

function harness(fetchTwse, fetchTpex) {
  const saved = [];
  const context = vm.createContext({ fetchTwse, fetchTpex, saveTechnicalMarketSnapshots: async days => {
    saved.push(...JSON.parse(JSON.stringify(days))); return true;
  }, console });
  vm.runInContext(compiled, context);
  return { saved, run: (dates = [date], existing = []) => context.fetchWindow(dates, existing) };
}

test('technical backfill fetches both exchanges concurrently and persists TPEx alongside TWSE', async () => {
  let releaseTwse;
  const twsePending = new Promise(resolve => { releaseTwse = resolve; });
  let tpexCalled = false;
  const env = harness(() => twsePending, async () => { tpexCalled = true; return rows('tpex', 500); });
  const pending = env.run();
  assert.equal(tpexCalled, true, 'TPEx must start before TWSE resolves');
  releaseTwse(rows('twse', 700));
  const result = await pending;
  assert.equal(result.stats.twseDays, 1); assert.equal(result.stats.tpexDays, 1);
  assert.equal(result.stats.saved, true);
  assert.equal(env.saved[0].rows.length, 1200, 'identical codes on separate markets must not collide');
  assert.equal(env.saved[0].rows.filter(row => row.market === 'tpex').length, 500);
});

test('TPEx failure is reported while successful TWSE rows and prior TPEx data are retained', async () => {
  const env = harness(async () => rows('twse', 700, 101), async () => { throw new Error('tpex_proxy_http_502'); });
  const result = await env.run([date], [{ tradeDate: '2026/09/07', rows: rows('tpex', 500, 99) }]);
  assert.equal(result.stats.tpexDays, 0);
  assert.equal(result.stats.tpexFailures[0], '2026/09/07:tpex_proxy_http_502');
  assert.equal(env.saved[0].rows.find(row => row.market === 'tpex').close, 99);
  assert.equal(env.saved[0].rows.find(row => row.market === 'twse').close, 101);
});

test('TWSE failure does not discard successful TPEx results or the old TWSE snapshot', async () => {
  const env = harness(async () => { throw new Error('twse_timeout'); }, async () => rows('tpex', 500, 103));
  const result = await env.run([date], [{ tradeDate: '2026/09/07', rows: rows('twse', 700, 98) }]);
  assert.equal(result.stats.twseDays, 0); assert.equal(result.stats.tpexDays, 1);
  assert.equal(env.saved[0].rows.find(row => row.market === 'twse').close, 98);
  assert.equal(env.saved[0].rows.find(row => row.market === 'tpex').close, 103);
});

test('complete source failure writes no replacement day that could erase saved market rows', async () => {
  const reject = async () => { throw new Error('offline'); };
  const env = harness(reject, reject);
  const result = await env.run([date], [{ tradeDate: '2026/09/07', rows: rows('tpex', 500) }]);
  assert.equal(result.days.length, 0); assert.equal(env.saved.length, 0);
  assert.equal(result.stats.tpexFailures.length, 1);
});
