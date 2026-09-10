import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { timedSingleFlight } from '../lib/timed-single-flight.ts';

test('concurrent strong/weak viewers share one sweep and later prices reorder the groups and their stocks', async () => {
  const compiled = ts.transpileModule(readFileSync(new URL('../app/api/focus-ranking/route.ts', import.meta.url), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  const mod = {exports:{}};
  const shared = {exports:{}};
  const afterTasks = [], savedGroups = [];
  let groupCalls = 0, quoteCalls = 0, updated = false;
  const groups = Array.from({length:67}, (_, i) => ({name:`group${i}`, members:Array.from({length:7}, (_, j) => ({code:String(1000+i*7+j), name:`stock${i}-${j}`}))}));
  const expectedBatches = Math.ceil(67*7/50);
  class MarketDate extends Date { constructor(...args) { super(...(args.length ? args : ['2026-09-07T09:20:00+08:00'])); } static now() { return Date.parse('2026-09-07T09:20:00+08:00'); } }
  const context = {
    URL, Response, AbortSignal, Date:MarketDate, Intl,
    require:name => {
      if (name === 'next/server') return {after: callback => afterTasks.push(callback)};
      return name.includes('timed-single-flight') ? {timedSingleFlight} : {isPreopenTrialWindow:()=>false};
    },
    fetch:async url => {
      if (String(url).endsWith('stocks.groups')) { groupCalls++; return Response.json({result:{data:{json:groups}}}); }
      quoteCalls++;
      const codes = JSON.parse(new URL(url).searchParams.get('input')).json.tickers;
      await new Promise(r => setTimeout(r, 2));
      return Response.json({result:{data:{json:{fetchedAt:updated?'2026-09-07T01:21:00Z':'2026-09-07T01:20:00Z',priceType:'即時價',rows:codes.map(code=>({code,price:100,changePct:(updated?-1:1)*(Number(code)-1200)/100}))}}}});
    },
  };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../lib/live-group-quotes.ts', import.meta.url), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText, {...context, exports:shared.exports});
  vm.runInNewContext(compiled, {...context, exports:mod.exports, require:name => name.includes('live-group-quotes') ? shared.exports : context.require(name)});
  const savedStrongGroups = [];
  const request = () => new Request('https://site.test/api/focus-ranking?direction=both');
  const [a,b] = await Promise.all([mod.exports.GET(request()),mod.exports.GET(request())]);
  const before = await a.json();
  assert.equal(a.status,200);
  assert.equal((await b.json()).rankings.strong.groups[0].name,before.rankings.strong.groups[0].name);
  assert.equal(groupCalls,1);
  assert.equal(quoteCalls,expectedBatches);
  assert.equal(before.rankings.strong.groups[0].name,'group66');
  assert.equal(before.rankings.strong.groups[0].stocks[0].symbol,'1468 stock66-6');
  assert.equal(savedGroups.length,0,'history persistence runs after the ranking response');
  await Promise.all(afterTasks.splice(0).map(run=>run()));
  assert.equal(savedGroups.length,2);
  assert.equal(savedStrongGroups.length,2);
  assert.deepEqual(JSON.parse(JSON.stringify(savedStrongGroups[0].groups)),before.rankings.strong.signalGroups);
  assert.deepEqual(JSON.parse(JSON.stringify(savedGroups[0].groups)),before.rankings.weak.signalGroups);
  assert.equal(savedGroups[0].stamp,before.updatedAt);
  updated = true;
  await new Promise(r=>setTimeout(r,3010));
  const after = await (await mod.exports.GET(request())).json();
  assert.equal(groupCalls,1);
  assert.equal(quoteCalls,expectedBatches*2);
  assert.equal(after.updatedAt,'2026-09-07T01:21:00Z');
  assert.equal(after.rankings.strong.groups[0].name,'group0');
  assert.equal(after.rankings.strong.groups[0].stocks[0].symbol,'1000 stock0-0');
  assert.equal(after.rankings.weak.groups[0].name,'group66');
  await Promise.all(afterTasks.splice(0).map(run=>run()));
  assert.deepEqual(JSON.parse(JSON.stringify(savedGroups.at(-1).groups)),after.rankings.weak.signalGroups);
  assert.equal(savedGroups.at(-1).stamp,after.updatedAt);
});
