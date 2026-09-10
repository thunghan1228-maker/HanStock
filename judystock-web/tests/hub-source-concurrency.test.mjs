import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import vm from 'node:vm';
import ts from 'typescript';
import {successfulSourcesInCompletionOrder} from '../lib/successful-source-stream.ts';
import {calculateIntradayLargeForceValue, qualifyInstantLargeSignalByTriggerForce} from '../lib/intraday-large-force.ts';

const source=readFileSync(new URL('../app/api/daytrade-early-sell/route.ts',import.meta.url),'utf8');
const ast=ts.createSourceFile('route.ts',source,ts.ScriptTarget.Latest,true);
function loadFunctions(names,globals={}) {
  const module={exports:{}};
  const code=ast.statements.filter(n=>ts.isFunctionDeclaration(n)&&names.includes(n.name?.text)).map(n=>n.getText(ast)).join('\n')+'\nexport {'+names.join(',')+'};';
  const js=ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(js,{module,exports:module.exports,URL,AbortSignal,successfulSourcesInCompletionOrder,HUB_BASES:['primary','backup'],normalizeSignals:v=>Array.isArray(v)?v:[],...globals});
  return module.exports;
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const signalSource=(base,signals=[])=>({base,payload:{signals},instantLargePayload:{signals:[]}});

test('source stream starts both origins and delivers healthy backup while primary is still pending',{timeout:2000},async()=>{
  const slow=deferred(),fast=deferred(),started=[],signals=[];
  const stream=successfulSourcesInCompletionOrder([slow,fast].map((d,i)=>signal=>{started.push(i);signals.push(signal);return d.promise;}));
  const first=stream.next();await nextTurn();assert.deepEqual(started,[0,1]);
  fast.resolve('backup');assert.equal((await first).value,'backup');
  await stream.return();assert.ok(signals.every(s=>s.aborted));slow.reject(new Error('aborted'));
});

test('source stream preserves another success for retry after consumer rejects the first',{timeout:2000},async()=>{
  const second=deferred();const stream=successfulSourcesInCompletionOrder([async()=> 'first',()=>second.promise]);
  assert.equal((await stream.next()).value,'first');
  const retry=stream.next();second.resolve('second');assert.equal((await retry).value,'second');
  assert.equal((await stream.next()).done,true);
});

test('all failed sources are collected without unhandled rejection and empty input finishes',async()=>{
  const errors=[];
  const stream=successfulSourcesInCompletionOrder([async()=>{throw new Error('primary');},async()=>{throw new Error('backup');}],e=>errors.push(e.message));
  assert.equal((await stream.next()).done,true);assert.deepEqual(errors.sort(),['backup','primary']);
  assert.equal((await successfulSourcesInCompletionOrder([]).next()).done,true);
});

test('full hub scan returns populated backup before a hanging origin and preserves six-second timeout and rank headers',{timeout:2000},async()=>{
  const primary=deferred(),calls=[],rankings={strong:['設備股']};
  const {loadFullHubSignalSources}=loadFunctions(['loadFullHubSignalSources'],{loadHubSignalSource:(base,timeout,ranks,signal)=>{calls.push({base,timeout,ranks,signal});return base==='primary'?primary.promise:Promise.resolve(signalSource(base,[{ticker:'2330'}]));}});
  const stream=loadFullHubSignalSources(rankings);const first=await stream.next();
  assert.equal(first.value.base,'backup');assert.equal(calls.length,2);
  assert.ok(calls.every(c=>c.timeout===6000&&c.ranks===rankings));
  await stream.return();assert.ok(calls.every(c=>c.signal.aborted));primary.reject(new Error('aborted'));
});

test('fast empty backup cannot hide a slower populated primary during full scan',{timeout:2000},async()=>{
  const primary=deferred();
  const {loadFullHubSignalSources}=loadFunctions(['loadFullHubSignalSources'],{loadHubSignalSource:base=>base==='primary'?primary.promise:Promise.resolve(signalSource(base))});
  const stream=loadFullHubSignalSources();let delivered=false;const first=stream.next().then(value=>{delivered=true;return value;});
  await nextTurn();assert.equal(delivered,false);
  primary.resolve(signalSource('primary',[{ticker:'2303'}]));assert.equal((await first).value.base,'primary');
  await stream.return();
});

test('full scan keeps legitimate empty response after all other sources fail, and handles total failure',async()=>{
  for(const empty of [true,false]){
    const {loadFullHubSignalSources}=loadFunctions(['loadFullHubSignalSources'],{loadHubSignalSource:async base=>{if(empty&&base==='backup')return signalSource(base);throw new Error('unavailable');}});
    const rows=[];for await(const row of loadFullHubSignalSources())rows.push(row);
    assert.equal(rows.length,empty?1:0);
  }
});

test('hub source forwards cancellation to both requests without changing feed parameters',async()=>{
  const calls=[],controller=new AbortController(),rankings={strong:['設備股']};
  const {loadHubSignalSource}=loadFunctions(['loadHubSignalSource'],{SIGNAL_WINDOW_START:'09:00',SIGNAL_WINDOW_END:'13:30',SIGNAL_GRANULARITY:'1m',taipeiTradeDate:()=> '2026-09-08',fetch:async(url,init)=>{calls.push({url,init});return {ok:true,json:async()=>({signals:[]})};}});
  await loadHubSignalSource('https://hub.example',6000,rankings,controller.signal);
  assert.equal(calls.length,2);
  assert.equal(calls[0].url.searchParams.get('start'),'09:00');assert.equal(calls[0].url.searchParams.get('end'),'13:30');
  assert.equal(calls[0].url.searchParams.get('interval'),'1m');assert.equal(calls[1].url.searchParams.get('limit'),'5000');
  assert.ok(calls.every(c=>c.init.headers['X-HanStock-Group-Rankings']===encodeURIComponent(JSON.stringify(rankings))));
  controller.abort();assert.ok(calls.every(c=>c.init.signal.aborted));
});

test('large-force monitor returns faster bars with unchanged rows, date filter and stock metadata',{timeout:2000},async()=>{
  const slow=deferred(),seen=[],stocks=[{ticker:'2330',name:'台積電'}],bars=[{timestamp:1}],map=new Map([['2330',bars]]);
  const {loadCurrentLargeForceMonitorRows}=loadFunctions(['loadCurrentLargeForceMonitorRows'],{
    loadMinuteBars:(base,tickers,signal)=>{seen.push({base,tickers,signal});return base==='primary'?slow.promise:Promise.resolve(map);},
    calculateIntradayLargeForceValue:(got,date)=>{assert.equal(got,bars);assert.equal(date,'2026-09-08');return {force:42};},
    calculateIntradayLargeForceSignals:(ticker,name,got)=>{assert.equal(ticker,'2330');assert.equal(name,'台積電');assert.equal(got,bars);return [{tradeDate:'2026-09-08'},{tradeDate:'2026-09-07'}];},
    signalGroupsByTicker:new Map([['2330',['半導體']]]),
  });
  const result=await loadCurrentLargeForceMonitorRows(stocks,'2026-09-08');
  assert.equal(result.rows[0].force,42);assert.equal(result.rows[0].group,'半導體');assert.equal(result.signals.length,1);
  assert.equal(result.requestedStocks,stocks);assert.equal(result.barsByTicker,map);assert.equal(result.sourceReached,true);
  assert.equal(seen.length,2);assert.ok(seen.every(s=>s.signal.aborted));slow.reject(new Error('aborted'));
});

test('large-force monitor retains all-source failure instead of inventing empty data',async()=>{
  const {loadCurrentLargeForceMonitorRows}=loadFunctions(['loadCurrentLargeForceMonitorRows'],{loadMinuteBars:async()=>{throw new Error('minute-feed-empty');}});
  await assert.rejects(loadCurrentLargeForceMonitorRows([{ticker:'2330'}],'2026-09-08'),/minute-feed-empty/);
});

test('minute-bar cancellation aborts network and prevents queued batches from starting',async()=>{
  const controller=new AbortController();let fetches=0;
  const {loadMinuteBars}=loadFunctions(['loadMinuteBars'],{
    mapMarketBatches:async(batches,run)=>Promise.allSettled(batches.map(run)),
    fetchBoundedMarketJson:async(url,init)=>{fetches++;const options=init();controller.abort();assert.equal(options.signal.aborted,true);throw new Error('aborted');},
  });
  await assert.rejects(loadMinuteBars('https://hub.example',['2330'],controller.signal),/aborted/);
  await assert.rejects(loadMinuteBars('https://hub.example',['2330'],controller.signal));
  assert.equal(fetches,1);
});

const triggerTs=Date.parse('2026-09-08T10:00:00+08:00');
const trigger=(ticker,kind='instantLargeBuy')=>({ticker,name:ticker,kind,tradeDate:'2026-09-08',barTs:triggerTs,price:100,note:'真實訊號'});
const forceBar=(buy,sell,ts=triggerTs)=>({ts,close:100,volume:10,main_buy_amount:buy,main_sell_amount:sell,main_force_available:true});
function qualifier(loadMinuteBars){const cache=new Map();return {...loadFunctions(['qualifyIncomingInstantLargeSignals','instantLargeTriggerForceKey'],{loadMinuteBars,instantLargeTriggerForceCache:cache,isAfterHoursTradeSignal:()=>false,calculateIntradayLargeForceValue,qualifyInstantLargeSignalByTriggerForce}),cache};}

test('instant-large force obtains missing tickers from either source without mixing cumulative bars',async()=>{
  const calls=[],first=deferred(),second=deferred();
  const h=qualifier(base=>{calls.push(base);return base==='primary'?first.promise:second.promise;});
  const output=h.qualifyIncomingInstantLargeSignals([trigger('2330'),trigger('2303','instantLargeSell')]);
  await nextTurn();assert.deepEqual(calls,['primary','backup']);
  first.resolve(new Map([['2330',[forceBar(200000,0)]]]));
  second.resolve(new Map([['2330',[forceBar(900000,0)]],['2303',[forceBar(0,300000)]]]));
  const result=await output;
  assert.equal(result.length,2);assert.match(result[0].note,/\+20\.0%/);assert.match(result[1].note,/-30\.0%/);
});

test('instant-large force falls back when first source has bars but lacks usable classification',async()=>{
  const h=qualifier(async base=>new Map([['2330',[base==='primary'?{...forceBar(0,0),main_force_available:false}:forceBar(400000,0)]]]));
  const result=await h.qualifyIncomingInstantLargeSignals([trigger('2330')]);
  assert.equal(result.length,1);assert.match(result[0].note,/\+40\.0%/);
});

test('instant-large force never selects another source merely to reverse a known opposing direction',async()=>{
  const h=qualifier(async base=>new Map([['2330',[base==='primary'?forceBar(0,200000):forceBar(400000,0)]]]));
  assert.equal((await h.qualifyIncomingInstantLargeSignals([trigger('2330')])).length,0);
  assert.equal([...h.cache.values()][0],-20);
});

test('instant-large fallback respects the original trigger cutoff and trading date',async()=>{
  const h=qualifier(async base=>new Map([['2330',base==='primary'?[forceBar(200000,0,triggerTs-86_400_000)]:[forceBar(200000,0,triggerTs+60_000)]]]));
  assert.equal((await h.qualifyIncomingInstantLargeSignals([trigger('2330')])).length,0);
  assert.equal([...h.cache.values()][0],null);
});

test('instant-large force tolerates one failed hub, but two failures do not poison the trigger cache',async()=>{
  for(const bothFail of [false,true]){
    const h=qualifier(async base=>{if(base==='primary'||bothFail)throw new Error('feed-offline');return new Map([['2330',[forceBar(200000,0)]]]);});
    if(bothFail){await assert.rejects(h.qualifyIncomingInstantLargeSignals([trigger('2330')]),/feed-offline/);assert.equal(h.cache.size,0);}
    else assert.equal((await h.qualifyIncomingInstantLargeSignals([trigger('2330')])).length,1);
  }
});

test('instant-large force still reuses known trigger values without repeating either source request',async()=>{
  let calls=0;const h=qualifier(async()=>{calls++;return new Map([['2330',[forceBar(200000,0)]]]);});
  await h.qualifyIncomingInstantLargeSignals([trigger('2330')]);await h.qualifyIncomingInstantLargeSignals([trigger('2330')]);
  assert.equal(calls,2);
});
