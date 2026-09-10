import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as thresholds from '../lib/instant-large-thresholds.mjs';
import * as rankings from '../lib/main-force-group-ranks.ts';
import * as session from '../lib/intraday-signal-session.ts';
import { timedKeyedSingleFlight } from '../lib/timed-single-flight.ts';
import * as force from '../lib/intraday-large-force.ts';
import { DatabaseSync } from 'node:sqlite';

function compile(path, dependencies, context = {}) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  runInNewContext(code, { exports, require: dependencies, Date, URL, Response, AbortSignal, ...context });
  return exports;
}
const trades = compile('../lib/after-hours-trades.ts', name => name.includes('thresholds') ? thresholds : rankings);
const lists = compile('../lib/after-hours-watchlist.ts', () => rankings);
const row = (ticker, extra = {}) => ({ tradeDate: '2026-09-07', ticker, name: ticker === '3481' ? '群創' : '華通',
  kind: 'instantLargeBuy', label: '瞬間大單連續敲進', barTs: Date.parse('2026-09-07T14:30:00+08:00'), price: 49.95,
  note: '同秒 1 筆｜合計 293 張｜約 1463.5 萬｜成交價 49.95～49.95｜族群同步 面板 漲幅第 2 名', ...extra });

const twse = (data, date = '20260907') => ({ stat: 'OK', date,
  fields: ['證券代號', '成交價', '成交數量', '最後揭示買量', '最後揭示賣量'], data });
function harness({ time = '2026-09-07T15:35:00+08:00', saved = [], source = { tradeDate: '2026-09-07', signals: [row('3481'), row('2313')] }, fail = false, official = {} } = {}) {
  let calls = [], writes = 0;
  class Clock extends Date { constructor(...args) { super(...(args.length ? args : [time])); } static now() { return Date.parse(time); } }
  const storage = {
    readEarlySellSignals: async options => saved.filter(row => !options.tradeDate || row.tradeDate === options.tradeDate)
      .sort((a, b) => b.barTs - a.barTs),
    saveEarlySellSignals: async rows => { writes += rows.length; for (const row of rows) {
      const index = saved.findIndex(old => old.tradeDate === row.tradeDate && old.ticker === row.ticker && old.kind === row.kind && old.barTs === row.barTs);
      if (index < 0) saved.push(row); else saved[index] = row;
    } },
  };
  const collector = compile('../db/after-hours-trades.ts', name => name.includes('early-sell-history') ? storage
    : name.includes('timed-single-flight') ? { timedKeyedSingleFlight } : trades, {
    Date: Clock, fetch: async url => {
      const target = String(url); calls.push(target);
      if (target.includes('BFT41U')) return Response.json(official.twse ?? {});
      if (target.includes('tpex_off_market')) {
        if (official.tpex instanceof Error) throw official.tpex;
        return Response.json(official.tpex ?? []);
      }
      if (fail) throw new Error('offline'); return Response.json(source);
    },
  });
  const watchlist = compile('../db/after-hours-watchlist.ts', name => name.includes('early-sell-history') ? storage
    : name.includes('intraday-signal-session') ? { ...session, loadTwseClosedTradingDates: async () => new Set() }
    : name.includes('after-hours-trades') ? collector : lists, { Date: Clock });
  return { ...collector, ...watchlist, saved, calls, writes: () => writes };
}

test('accepts real 14:30 executions without inventing intraday force and keeps size/group rules', () => {
  const real = row('3481');
  const rejected = [
    row('2313', { barTs: Date.parse('2026-09-07T13:30:00+08:00') }),
    row('2313', { barTs: Date.parse('2026-09-07T14:31:00+08:00') }),
    row('2313', { tradeDate: '2026-09-04' }),
    row('2313', { kind: 'riverBull' }), row('2313', { price: 0 }),
    row('2313', { note: real.note.replace('293 張', '3 張') }),
    row('2313', { note: real.note.replace('第 2 名', '第 21 名') }),
    row('2313', { kind: 'instantLargeSell' }),
  ];
  const validSell = row('2408', { kind: 'instantLargeSell', note: real.note.replace('漲幅', '跌幅') });
  const result = trades.normalizeAfterHoursTrades([real, real, validSell, ...rejected], '2026-09-07');
  assert.deepEqual(Array.from(result, r => r.ticker), ['3481', '2408']);
  assert.ok(result.every(r => r.note.includes('14:30 盤後定價成交')));
  assert.ok(result.every(r => !r.note.includes('觸發當時盤中大戶力')));
});

test('missing 14:30 trades are persisted and returned by folder 7; refresh is idempotent', async () => {
  const h = harness();
  const result = await h.loadAfterHoursWatchlist();
  assert.equal(result.tradeDate, '2026-09-07');
  assert.equal(result.sourceStatus, 'ready');
  assert.equal(result.stocks.length, 2);
  assert.equal(h.writes(), 2);
  assert.ok(h.calls.some(url => url.includes('intraday-large-orders?limit=5000')));
  const second = await h.loadAfterHoursWatchlist();
  assert.equal(second.stocks.length, 2);
  assert.equal(h.writes(), 2);
  assert.equal(h.calls.length, 3, 'same-date source requests share the result, including the two official sources');
  assert.equal(result.stocks.find(s => s.ticker === '3481').price, '49.95');
  assert.equal(result.stocks.find(s => s.ticker === '3481').forcePct, null);
});

test('a fresh process cannot duplicate saved trades or erase already annotated force', async () => {
  const saved = trades.normalizeAfterHoursTrades([row('3481', { note: row('3481').note + '｜觸發當時盤中大戶力 +8.0%' })], '2026-09-07');
  const h = harness({ saved });
  await h.refreshAfterHoursTrades('2026-09-07');
  assert.equal(h.writes(), 1);
  assert.equal(saved.find(r => r.ticker === '3481').note.includes('+8.0%'), true);
});

test('official imbalance uses unmatched volume, including zero, missing and contradictory cases', () => {
  // The handoff examples are the dated TWSE 2026-09-07 report, not 09-08.
  assert.equal(trades.calculateAfterHoursForcePct(4, 0, 83).toFixed(1), '-95.4');
  assert.equal(trades.calculateAfterHoursForcePct(56, 39, 0).toFixed(1), '41.1');
  assert.equal(trades.calculateAfterHoursForcePct(5, 33, 0).toFixed(1), '86.8');
  assert.equal(trades.calculateAfterHoursForcePct(10, 0, 0), 0);
  assert.equal(trades.calculateAfterHoursForcePct(0, 3, 0), 100);
  assert.equal(trades.calculateAfterHoursForcePct(0, 0, 3), -100);
  for (const args of [[0, 0, 0], [1, 2, 3], [-1, 0, 3], [NaN, 0, 3], [1, Infinity, 0]]) {
    assert.equal(trades.calculateAfterHoursForcePct(...args), null);
  }
});

test('official parsers require correct date, schema and units; TPEx uses unexecuted columns', () => {
  const dated = twse([['1718', '10.45', '56', '39', ''], ['1709', '34.20', '4', '', '83']]);
  assert.equal(trades.parseAfterHoursForceQuotes(dated, 'twse', '2026-09-07').get('1718').forcePct.toFixed(1), '41.1');
  assert.equal(trades.parseAfterHoursForceQuotes(dated, 'twse', '2026-09-08').size, 0);
  assert.equal(trades.parseAfterHoursForceQuotes([{ Code: '1709', TradeVolume: '4', AskVolume: '83' }], 'twse', '2026-09-08').size, 0);
  const otc = { Date: '1150907', SecuritiesCompanyCode: '3481', Close: '49.95', TradeVolume: '1,000',
    BidVolume: '1,000', AskVolume: '1,500', BidVolumeUnexecute: '0', OfferVolumeUnexecute: '500' };
  const parsed = trades.parseAfterHoursForceQuotes([otc], 'tpex', '2026-09-07');
  assert.equal(parsed.get('3481').forcePct.toFixed(1), '-33.3');
  assert.equal(trades.parseAfterHoursForceQuotes([otc], 'tpex', '2026-09-08').size, 0);
  for (const invalid of [undefined, '', '--', 'unknown']) {
    assert.equal(trades.parseAfterHoursForceQuotes([{ ...otc, TradeVolume: invalid }], 'tpex', '2026-09-07').size, 0);
  }
});

test('official note enrichment is idempotent and rejects a different fixed price', () => {
  const quotes = new Map([['3481', { price: 49.95, forcePct: 41.0526315789 }]]);
  const [first] = trades.normalizeAfterHoursTrades([row('3481')], '2026-09-07', quotes);
  assert.match(first.note, /盤後大戶力 \+41\.1%/);
  assert.equal(trades.normalizeAfterHoursTrades([first], '2026-09-07', quotes)[0].note, first.note);
  assert.equal(trades.normalizeAfterHoursTrades([first], '2026-09-07')[0].note, first.note);
  assert.doesNotMatch(trades.normalizeAfterHoursTrades([row('3481', { price: 50 })], '2026-09-07', quotes)[0].note, /盤後大戶力/);
  assert.equal(trades.afterHoursForceFromNote(first.note), 41.1);
  for (const note of ['盤後大戶力 999%', '盤後大戶力 --%', '盤中大戶力 +1%']) assert.equal(trades.afterHoursForceFromNote(note), null);
  assert.equal(trades.afterHoursForceFromNote('盤後大戶力 0.0%'), 0);
});

test('saved 14:30 rows receive official force without losing other annotations or duplicating records', async () => {
  const saved = trades.normalizeAfterHoursTrades([row('3481', { note: row('3481').note + '｜自有歷史註記' })], '2026-09-07');
  const h = harness({ saved, official: { twse: twse([['3481', '49.95', '56', '39', '0']]) } });
  await h.refreshAfterHoursTrades('2026-09-07');
  assert.equal(saved.length, 2);
  assert.match(saved.find(r => r.ticker === '3481').note, /自有歷史註記.*盤後大戶力 \+41\.1%/);
  assert.equal(h.writes(), 2);
  await h.refreshAfterHoursTrades('2026-09-07');
  assert.equal(h.writes(), 2);
});

test('a failed official market does not block imports, and a failed hub does not block saved-row enrichment', async () => {
  const official = { twse: twse([['3481', '49.95', '56', '39', '0']]), tpex: new Error('timeout') };
  const h = harness({ official });
  await h.refreshAfterHoursTrades('2026-09-07');
  assert.equal(h.saved.length, 2);
  assert.match(h.saved[0].note, /盤後大戶力 \+41\.1%/);
  assert.doesNotMatch(h.saved[1].note, /盤後大戶力/);
  const offline = harness({ official, saved: [row('3481')], fail: true });
  await assert.rejects(offline.refreshAfterHoursTrades('2026-09-07'), /offline/);
  assert.match(offline.saved[0].note, /盤後大戶力 \+41\.1%/);
});

test('historical recovery never requests current-only official enrichment', async () => {
  const h = harness({ time: '2026-09-08T15:30:00+08:00', official: { twse: twse([['3481', '49.95', '56', '39', '0']]) } });
  await h.refreshAfterHoursTrades('2026-09-07');
  assert.equal(h.calls.length, 1);
  assert.ok(h.calls[0].includes('trade_date=2026-09-07'));
  assert.ok(h.saved.every(r => !r.note.includes('盤後大戶力')));
});

test('real SQL upsert retains verified after-hours force across subsequent hub writes', async () => {
  const sqlite = new DatabaseSync(':memory:');
  const d1 = { prepare: sql => ({ sql, args: [], bind(...args) { this.args = args; return this; } }),
    batch: async statements => statements.map(({ sql, args }) => sqlite.prepare(sql).run(...args)) };
  const storage = compile('../db/early-sell-history.ts', () => ({}), { __HANSTOCK_DB: d1 });
  try {
    const enriched = trades.normalizeAfterHoursTrades([row('3481')], '2026-09-07', new Map([['3481', { forcePct: 41.1, price: 49.95 }]]));
    enriched[0].note += '｜其他已存註記';
    await storage.saveEarlySellSignals(enriched);
    await storage.saveEarlySellSignals([row('3481', { note: row('3481').note + '｜新增籌碼排名' })]);
    const saved = sqlite.prepare('SELECT note FROM early_sell_signals').get().note;
    assert.match(saved, /新增籌碼排名.*14:30 盤後定價成交.*盤後大戶力 \+41\.1%$/);
    assert.equal((saved.match(/盤後大戶力/g) ?? []).length, 1);
    assert.equal(sqlite.prepare('SELECT count(*) AS n FROM early_sell_signals').get().n, 1);
    await storage.saveEarlySellSignals([row('3481', { price: 50 })]);
    assert.doesNotMatch(sqlite.prepare('SELECT note FROM early_sell_signals').get().note, /盤後大戶力/);
  } finally { sqlite.close(); }
});

test('empty, wrong-date and failed responses preserve the previous list with its true date', async () => {
  const friday = row('3481', { tradeDate: '2026-09-04', barTs: Date.parse('2026-09-04T14:30:00+08:00') });
  for (const options of [{ source: { tradeDate: '2026-09-07', signals: [] } },
    { source: { tradeDate: '2026-09-04', signals: [friday] } }, { fail: true }]) {
    const h = harness({ saved: [friday], ...options });
    const result = await h.loadAfterHoursWatchlist();
    assert.equal(result.tradeDate, '2026-09-04');
    assert.equal(result.targetTradeDate, '2026-09-07');
    assert.equal(result.stocks.length, 1);
    assert.match(result.message, /2026-09-04/);
    assert.equal(h.writes(), 0);
  }
});

test('before the next 14:30 cutoff, the previous list loads by its historical date', async () => {
  const friday = row('3481', { tradeDate: '2026-09-04', barTs: Date.parse('2026-09-04T14:30:00+08:00') });
  const h = harness({ time: '2026-09-07T14:29:59+08:00', source: { tradeDate: '2026-09-04', signals: [friday] } });
  const result = await h.loadAfterHoursWatchlist();
  assert.equal(result.tradeDate, '2026-09-04');
  assert.ok(h.calls[0].includes('trade_date=2026-09-04'));
  const beforeCalls = h.calls.length;
  assert.equal((await h.refreshAfterHoursTrades('2026-09-07')).status, 'pending');
  assert.equal(h.calls.length, beforeCalls);
});

test('after-hours imports run from popup polling without restarting the intraday scan', async () => {
  for (const [minutes, expected] of [[869, 0], [870, 1], [940, 1]]) {
    let imports = 0;
    const urls = [], pending = [];
    const deps = { default: { fetch: async request => { urls.push(new URL(request.url).pathname); return new Response('ok'); } },
      taipeiMarketClock: () => ({ date: '2026-09-07', weekday: 'Mon', minutes }),
      shouldTriggerChipServerRefresh: () => false, withMarketRequestScope: run => run(),
      loadAfterHoursWatchlist: async () => { imports++; } };
    const worker = compile('../worker/index.ts', () => deps).default;
    await worker.fetch(new Request('https://site.test/api/daytrade-early-sell?snapshot=1'), { DB: {} }, { waitUntil: p => pending.push(p) });
    await Promise.all(pending);
    assert.equal(imports, expected);
    assert.deepEqual(urls, ['/api/daytrade-early-sell']);
  }
});

test('incoming 14:30 executions avoid missing minute bars, while intraday trades keep the force filter', async () => {
  const source = readFileSync(new URL('../app/api/daytrade-early-sell/route.ts', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('route.ts', source, ts.ScriptTarget.Latest, true);
  const functions = ast.statements.filter(n => ts.isFunctionDeclaration(n)
    && ['qualifyIncomingInstantLargeSignals', 'instantLargeTriggerForceKey'].includes(n.name?.text)).map(n => n.getText(ast)).join('\n');
  let loads = 0;
  const qualifier = runInNewContext(ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
    + '\nqualifyIncomingInstantLargeSignals', { ...trades, ...force,
      HUB_BASES: ['https://primary.test', 'https://backup.test'],
      instantLargeTriggerForceCache: new Map(), loadMinuteBars: async () => { loads++; return new Map(); } });
  const incoming = [row('3481'), row('2313', { barTs: Date.parse('2026-09-07T13:30:00+08:00') })];
  const result = await qualifier(incoming);
  assert.deepEqual(Array.from(result, r => r.ticker), ['3481']);
  assert.equal(loads, 2);
  loads = 0;
  assert.equal((await qualifier([row('3481')])).length, 1);
  assert.equal(loads, 0);
});
