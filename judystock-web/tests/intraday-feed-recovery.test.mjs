import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {withMarketRequestScope} from '../lib/market-request-scope.ts';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;

test('empty standby responses fail over instead of reporting a successful live collection', async () => {
  const source = readFileSync(new URL('../app/api/daytrade-early-sell/route.ts', import.meta.url),'utf8');
  const ast = ts.createSourceFile('route.ts',source,ts.ScriptTarget.Latest,true);
  const fn = ast.statements.find(n=>ts.isFunctionDeclaration(n) && n.name?.text === 'loadMinuteBars');
  let payload = {data:{'2303':[]}};
  const context = vm.createContext({URL,AbortSignal,fetchBoundedMarketJson:async()=>payload,mapMarketBatches:async(items,run)=>Promise.allSettled(items.map(run))});
  vm.runInContext(compile(fn.getText(ast)),context);
  await assert.rejects(context.loadMinuteBars('https://hub.test',['2303']),/minute-feed-empty/);
  payload = {data:{'2303':[{ts:Date.parse('2026-09-07T10:28:00+08:00'),close:137}]}};
  const bars = await context.loadMinuteBars('https://hub.test',['2303','2408']);
  assert.equal(bars.get('2303')[0].close,137);
  assert.equal(bars.get('2408').length,0);
});

for (const [minutes,expectHistory] of [[9*60+5,false],[13*60+45,true]]) {
  test(`homepage at ${minutes} minutes keeps market history separate from live collection`, async () => {
    const source = readFileSync(new URL('../worker/index.ts', import.meta.url),'utf8');
    const mod = {exports:{}};
    const urls = [];
    let chunksRead = 0;
    const handler = {fetch:async(req)=>{
      urls.push(new URL(req.url));
      let done = false;
      return {body:{getReader:()=>({read:async()=>{chunksRead++; if(done)return {done:true}; done=true; return {done:false,value:new Uint8Array(4)};},releaseLock(){}})},arrayBuffer(){throw new Error('background responses must not be fully buffered');}};
    }};
    const deps = {withMarketRequestScope,default:handler,taipeiMarketClock:()=>({date:'2026-09-07',weekday:'Mon',minutes}),shouldTriggerChipServerRefresh:()=>false};
    vm.runInNewContext(compile(source),{exports:mod.exports,require:()=>deps,URL,Request,Response,Date,console});
    const pending=[];
    await mod.exports.default.fetch(new Request('https://site.test/'),{DB:{}},{waitUntil:p=>pending.push(p)});
    await Promise.all(pending);
    assert.equal(urls.some(u=>u.pathname==='/api/technical-market'),expectHistory);
    assert.equal(urls.some(u=>u.pathname==='/api/daytrade-early-sell'),!expectHistory);
    assert.ok(chunksRead>0);
  });
}
