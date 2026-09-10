import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as force from '../lib/intraday-large-force.ts';
import { readAvailableRankingForceClose } from '../lib/ranking-force-backfill.ts';

const compiled=ts.transpileModule(readFileSync(new URL('../app/api/intraday-large-force-values/route.ts',import.meta.url),'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText;
const at=time=>Date.parse('2026-09-04T'+time+':00+08:00');
const minute=(buy=2_000_000,ts=at('10:00'))=>({ts,close:100,volume:100,main_buy_amount:buy,main_sell_amount:1_000_000,main_force_available:true});
function route({now=at('10:00'),snapshot=[],closed=async()=>[],backfill=async()=>[],fetcher=async()=>({bars:[minute()]})}={}) {
 let clock=now;const calls=[],historyCalls=[],archiveCalls=[];
 const exports={};
 vm.runInNewContext(compiled,{exports,URL,URLSearchParams,AbortSignal,Date:class extends Date{static now(){return clock}},
  require(name){
   if(name==='next/server')return{NextResponse:{json:(body,options)=>({body,options})}};
   if(name.endsWith('/intraday-large-force'))return force;
   if(name.endsWith('/ranking-force-backfill'))return{
    readAvailableRankingForceClose:async(...args)=>{archiveCalls.push(args);return closed(...args)},
    backfillRankingForceClose:async(...args)=>{historyCalls.push(args);return backfill(...args)},
   };
   if(name.endsWith('/intraday-large-force-scan'))return{readIntradayLargeForceMonitorRows:async()=>({rows:snapshot,updatedAt:clock}),readIntradayLargeForceScanProgress:async()=>null};
   if(name.endsWith('/technical-market-history'))return{readPreviousTechnicalMarketSnapshot:async()=>null};
   if(name.endsWith('/intraday-force-homework'))return{homeworkPriceChange:()=>({})};
   throw Error(name);
  },
  fetch:async(url,options)=>{calls.push({url:String(url),options});return{ok:true,json:async()=>await fetcher(String(url),calls.length)}},
 });
 return{calls,historyCalls,archiveCalls,tick:ms=>clock+=ms,get:query=>exports.GET({nextUrl:new URL('https://site.test/api/intraday-large-force-values?'+(query||'tradeDate=2026-09-04&tickers=2313'))})};
}
test('the screenshot stock returns archived closing force after live subscriptions disappear',async()=>{
 const r=route({now:at('18:00'),closed:readAvailableRankingForceClose,fetcher:()=>{throw Error('No live data')}});
 const {body}=await r.get();const row=body.rows[0];assert.equal(row.ticker,'2313');assert.equal(row.tradeDate,'2026-09-04');
 assert.equal(row.source,'historical-ticks');assert.equal(row.forcePct.toFixed(2),'9.04');
 assert.equal(r.calls.length,0);assert.equal(r.historyCalls.length,0);
 await r.get();assert.equal(r.archiveCalls.length,1);
});
test('one active chart goes straight to the individual endpoint and refreshes after five seconds',async()=>{
 const r=route({fetcher:(_,n)=>({bars:[minute(n*2_000_000)]})});
 assert.equal((await r.get()).body.rows[0].forcePct,10);
 assert.match(r.calls[0].url,/\/bars1m\/2313$/);assert.equal(r.calls[0].options.method,undefined);
 assert.equal(r.archiveCalls.length,0);await r.get();assert.equal(r.calls.length,1);
 r.tick(5001);assert.equal((await r.get()).body.rows[0].forcePct,30);assert.equal(r.calls.length,2);
});
test('simultaneous chart requests share a single upstream fetch',async()=>{
 let finish;const wait=new Promise(r=>finish=r);const r=route({fetcher:async()=>{await wait;return{bars:[minute()]}}});
 const first=r.get(),second=r.get();await new Promise(r=>setImmediate(r));assert.equal(r.calls.length,1);
 finish();const results=await Promise.all([first,second]);assert.ok(results.every(x=>x.body.rows[0].forcePct===10));
});
test('a wrong-day single endpoint falls back to the backup instead of accepting its nonempty array',async()=>{
 const r=route({fetcher:(_,n)=>({bars:[minute(2_000_000,n===1?at('10:00')-86400000:at('10:00'))]})});
 assert.equal((await r.get()).body.rows[0].forcePct,10);assert.equal(r.calls.length,2);
});
test('stale monitor values are replaced when fresh bars arrive and retained on source failure',async()=>{
 const old={ticker:'2313',...force.calculateIntradayLargeForceValue([minute(8_000_000,at('09:30'))],'2026-09-04')};
 const r=route({snapshot:[old]});assert.equal((await r.get()).body.rows[0].forcePct,10);assert.equal(r.calls.length,1);
 const failed=route({snapshot:[old],fetcher:()=>{throw Error('offline')}});assert.equal((await failed.get()).body.rows[0].forcePct,70);
});
test('missing historical data has a bounded recovery and never gets cached as zero',async()=>{
 const r=route({now:at('18:00')});assert.equal((await r.get()).body.rows[0].forcePct,null);
 assert.equal(r.historyCalls[0][2].timeoutMs,2500);assert.equal(r.calls.length,0);
 await r.get();assert.equal(r.historyCalls.length,2);
});
test('market scope keeps its saved rows and does not launch per-stock requests',async()=>{
 const r=route({snapshot:[{ticker:'2313',tradeDate:'2026-09-04',barTs:at('10:00'),forcePct:5,price:100,buyAmount:6_000_000,sellAmount:1_000_000,turnoverAmount:100_000_000}]});
 assert.equal((await r.get('tradeDate=2026-09-04&scope=market')).body.rows[0].forcePct,5);
 assert.equal(r.calls.length+r.historyCalls.length+r.archiveCalls.length,0);
});
test('market scope rejects unclassified zero snapshots instead of ranking them as real force',async()=>{
 const r=route({snapshot:[{ticker:'2313',tradeDate:'2026-09-04',barTs:at('10:00'),forcePct:0,price:100,buyAmount:0,sellAmount:0,turnoverAmount:100_000_000}]});
 assert.deepEqual((await r.get('tradeDate=2026-09-04&scope=market')).body.rows,[]);
});
