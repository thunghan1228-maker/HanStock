import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const read = file => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
const compile = source => ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
function declaration(file, name) {
  const source = read(file), ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
  assert.ok(found, `Missing production function ${name}`);
  return found.getText(ast).replace(/^export /, '');
}

// Execute the real scheduler and visibility utility using a Taiwan market clock.
function focusScheduler(start, visibility = 'visible') {
  let now = Date.parse(start), nextId = 0, aborted = false;
  const intervals = new Map(), listeners = new Set(), requests = [];
  class ClockDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const document = {visibilityState: visibility, addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn)};
  const window = {setInterval: (fn, ms) => { intervals.set(++nextId, {fn, ms, next: now + ms}); return nextId; }, clearInterval: id => intervals.delete(id)};
  const context = vm.createContext({Date: ClockDate, Intl, document, window, exports: {}, load: () => requests.push(now), controller: {abort: () => { aborted = true; }}});
  vm.runInContext(compile(read('lib/useVisibilityGatedInterval.ts')), context);
  context.createVisibilityGatedInterval = context.exports.createVisibilityGatedInterval;
  vm.runInContext(compile(declaration('app/page.tsx', 'isPreopenTrialClientWindow')), context);
  const page = read('app/page.tsx'), ast = ts.createSourceFile('page.tsx', page, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let effect;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(ast) === 'useEffect' && node.arguments[0]?.getText(ast).includes('/api/focus-ranking?')) effect = node.arguments[0].getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(effect);
  const scheduler = effect.slice(effect.indexOf('    let intervalMs'));
  const cleanup = vm.runInContext(compile(`(() => {\n${scheduler})()`), context);
  return {
    requests, intervals, listeners,
    get aborted() { return aborted; },
    cancel: cleanup,
    visibility(value) { document.visibilityState = value; for (const fn of [...listeners]) fn(); },
    advance(ms) {
      const until = now + ms;
      while (true) {
        const due = [...intervals.values()].filter(item => item.next <= until).sort((a,b) => a.next-b.next)[0];
        if (!due) break;
        now = due.next; due.next += due.ms; due.fn();
      }
      now = until;
    },
  };
}

test('focus polling enters the 08:30 Taiwan trial window and drops from 5s to 30s after 09:00 without reload', () => {
  const env = focusScheduler('2026-09-08T08:29:30+08:00');
  assert.equal([...env.intervals.values()][0].ms, 30000);
  env.advance(30000);
  assert.equal([...env.intervals.values()][0].ms, 5000);
  env.advance(30 * 60000);
  assert.equal(new Date(env.requests.at(-1)).toISOString(), '2026-09-08T01:00:00.000Z');
  assert.equal([...env.intervals.values()][0].ms, 30000);
  const before = env.requests.length;
  env.advance(60000); assert.equal(env.requests.length-before, 2);
  assert.equal(env.intervals.size, 1); assert.equal(env.listeners.size, 1);
  env.cancel(); assert.equal(env.intervals.size, 0); assert.equal(env.listeners.size, 0); assert.equal(env.aborted, true);
});

test('focus polling suspends while hidden and resumes once at the current cadence with no leaked old timer', () => {
  const env = focusScheduler('2026-09-08T08:59:50+08:00');
  env.advance(5000); assert.equal(env.requests.length, 1);
  env.visibility('hidden'); assert.equal(env.intervals.size, 0);
  env.advance(60000); assert.equal(env.requests.length, 1);
  env.visibility('visible'); assert.equal(env.requests.length, 2);
  assert.equal([...env.intervals.values()][0].ms, 30000);
  assert.equal(env.intervals.size, 1); assert.equal(env.listeners.size, 1);
  env.advance(60000); assert.equal(env.requests.length, 4);
  env.cancel(); env.visibility('hidden'); env.visibility('visible'); env.advance(60000);
  assert.equal(env.requests.length, 4); assert.equal(env.listeners.size, 0);
});

test('focus polling stays at 30s on weekends and hidden mounting allocates no timer', () => {
  for (const start of ['2026-09-12T08:40:00+08:00', '2026-09-13T08:40:00+08:00', '2026-09-08T13:00:00+08:00']) {
    const env = focusScheduler(start, 'hidden');
    assert.equal(env.intervals.size, 0);
    env.advance(60000); assert.equal(env.requests.length, 0);
    env.visibility('visible'); assert.equal(env.requests.length, 1);
    assert.equal([...env.intervals.values()][0].ms, 30000);
    env.cancel();
  }
});

test('signal time display caps at its own trading-day 14:30 and preserves earlier historical timestamps', () => {
  const context = vm.createContext({Date, earlySellTime: value => value});
  vm.runInContext(compile(declaration('app/page.tsx', 'signalCenterTime')), context);
  for (const time of ['09:00', '13:15', '14:30', '15:30']) {
    const barTs = Date.parse(`2026-09-04T${time}:00+08:00`);
    assert.equal(context.signalCenterTime({tradeDate:'2026-09-04',barTs}), Math.min(barTs, Date.parse('2026-09-04T14:30:00+08:00')));
  }
});

test('signal correction deletes only the requested trading date, kind and deduplicated tickers; broad deletion remains inert', async () => {
  const queries = [], context = vm.createContext({exports:{}, database: async () => ({prepare: sql => ({bind: (...values) => ({run: async () => queries.push({sql,values})})})})});
  vm.runInContext(compile(declaration('db/early-sell-history.ts', 'deleteEarlySellSignalsForTickers') + '\n' + declaration('db/early-sell-history.ts', 'deleteEarlySellSignalsForKindTickers')), context);
  await context.deleteEarlySellSignalsForTickers('2026-09-07', ['2330']);
  await context.deleteEarlySellSignalsForKindTickers('', 'blackDragon', ['2330']);
  await context.deleteEarlySellSignalsForKindTickers('2026-09-07', 'blackDragon', []);
  assert.equal(queries.length, 0);
  await context.deleteEarlySellSignalsForKindTickers('2026-09-07', 'blackDragon', [' 2330 ', '2330', ...Array.from({length:60},(_,i)=>String(3000+i))]);
  assert.equal(queries.length, 2);
  for (const {sql,values} of queries) {
    assert.match(sql, /WHERE trade_date = \? AND kind = \? AND ticker IN/);
    assert.deepEqual(Array.from(values.slice(0,2)), ['2026-09-07','blackDragon']);
    assert.ok(values.length <= 62);
  }
  assert.equal(queries.flatMap(q=>Array.from(q.values.slice(2))).length, 61);
});

test('caller group-ranking headers are decoded, ordered and rejected when incomplete or malformed', () => {
  const source = read('app/api/daytrade-early-sell/route.ts');
  const constant = name => Number(source.match(new RegExp(`const ${name} = (\\d+);`))?.[1]);
  const minimum = constant('SIGNAL_GROUP_RANK_LIMIT'), maximum = constant('INSTANT_LARGE_GROUP_RANK_LIMIT');
  assert.ok(minimum > 0 && maximum >= minimum);
  const context = vm.createContext({SIGNAL_GROUP_RANK_LIMIT: minimum, INSTANT_LARGE_GROUP_RANK_LIMIT: maximum});
  vm.runInContext(compile(declaration('app/api/daytrade-early-sell/route.ts','clientGroupRankings')), context);
  const readHeader = value => context.clientGroupRankings({headers:new Headers(value ? {'X-HanStock-Group-Rankings':value} : {})});
  for (const invalid of [null, '%', '{bad', 'x'.repeat(12001), encodeURIComponent(JSON.stringify({strong:[],weak:[]}))]) assert.equal(readHeader(invalid), undefined);
  const rows = Array.from({length:minimum},(_,i)=>({name:`族群${i+1}`,rank:i+1,change:'+1%'})).reverse();
  const result = readHeader(encodeURIComponent(JSON.stringify({strong:rows,weak:rows})));
  assert.deepEqual(Array.from(result.strong,r=>r.rank), Array.from({length:minimum},(_,i)=>i+1));
  assert.equal(result.weak.length, minimum);
});
