import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as parsers from '../lib/stock-trading-status.ts';
import * as diagnostics from '../lib/market-source-diagnostics.ts';

const source=readFileSync(new URL('../app/api/stock-trading-status/route.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source+'\nexport {loadSources,fetchTpexMarginSource};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const codes=Array.from({length:110},(_,i)=>String(6100+i));
function marginRows(extra=['6187','1785'],suspended=false){return [...new Set([...codes,...extra])].map(SecuritiesCompanyCode=>({SecuritiesCompanyCode,MarginPurchaseQuota:'10000',ShortSaleQuota:'10000',Note:suspended&&SecuritiesCompanyCode==='6187'?'OX':''}));}
function fixture(){
  let now=1788840000000;
  const config={margin:marginRows(),daytrade:[...codes,'6187','1785'],failMargin:false,failDaytrade:false,fastEmpty:false};
  const calls=[];
  class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
  const fetch=async url=>{
    calls.push(url);
    if(url.includes('MI_MARGN'))return Response.json([{股票代號:'2330',融資限額:'10000',融券限額:'10000',註記:''}]);
    if(url.includes('TWTB4U'))return Response.json(codes.map(Code=>({Code})));
    if(url.includes('taifex'))return new Response('<table></table>');
    if(url.includes('tpex_mainboard_margin_balance')){
      if(config.failMargin)throw Error('margin source down');
      if(config.fastEmpty&&!url.includes('r.jina.ai'))return Response.json([]);
      if(config.fastEmpty)await new Promise(resolve=>setTimeout(resolve,5));
      return Response.json(config.margin);
    }
    if(url.includes('tpex_securities')){
      if(config.failDaytrade)throw Error('day-trading source down');
      return Response.json(config.daytrade.map(SecuritiesCompanyCode=>({SecuritiesCompanyCode})));
    }
    // CSV alternatives fail in this fixture; JSON must stand on its own.
    throw Error('unavailable alternative');
  };
  const module={exports:{}};
  vm.runInNewContext(compiled,{exports:module.exports,require:name=>name.includes('market-source-diagnostics')?diagnostics:name.includes('stock-trading-status')?parsers:name.includes('disposition-risk')?{GET:async()=>Response.json({dispositions:[]})}:{},fetch,Response,URL,AbortSignal,Map,Set,Date:Clock,console});
  const get=async()=>{const response=await module.exports.GET({nextUrl:new URL('http://localhost/api/stock-trading-status?tickers=6187,1785')});return{response,data:await response.json()};};
  return{...module.exports,config,calls,get,advance:ms=>{now+=ms;},now:()=>now};
}

test('an empty fast TPEx JSON result cannot defeat a slower valid margin source',async()=>{
  const f=fixture();f.config.fastEmpty=true;
  const result=await f.fetchTpexMarginSource();
  assert.equal(result.get('6187').margin,'available');assert.equal(result.size,111);
});
test('partial TPEx margin retains missing known rows, respects new suspensions and retries at ten seconds',async()=>{
  const f=fixture();await f.get();f.advance(30*60_000+1);
  f.config.margin=marginRows(['6187'],true);
  const partial=await f.get();
  assert.equal(partial.data.rows.find(r=>r.code==='1785').margin,'available');
  assert.equal(partial.data.rows.find(r=>r.code==='6187').margin,'unavailable');
  assert.equal(partial.data.rows.find(r=>r.code==='6187').short,'unavailable');
  assert.equal(partial.data.complete,false);assert.equal(partial.response.headers.get('Cache-Control'),'no-store');
  const count=f.calls.length;f.advance(9999);await f.get();assert.equal(f.calls.length,count);
  f.config.margin=marginRows();f.advance(1);const healed=await f.get();
  assert.ok(f.calls.length>count);assert.equal(healed.data.complete,true);assert.match(healed.response.headers.get('Cache-Control'),/s-maxage=1800/);
});
test('legitimate day-trading and margin-list differences do not cause perpetual partial refreshes',async()=>{
  const f=fixture();f.config.daytrade.push('8084');
  const first=await f.get();assert.equal(first.data.complete,true);
  const count=f.calls.length;f.advance(10_001);await f.get();assert.equal(f.calls.length,count);
});
test('a cold partial source serves observed rows without fabricating missing margin eligibility',async()=>{
  const f=fixture();f.config.margin=marginRows(['6187']);
  const result=await f.get();
  assert.equal(result.data.rows.find(r=>r.code==='6187').margin,'available');
  assert.notEqual(result.data.rows.find(r=>r.code==='1785').margin,'available');
});
test('TPEx fetch failure retains prior status but cannot publish a thirty-minute complete cache',async()=>{
  const f=fixture();await f.get();f.advance(30*60_000+1);f.config.failMargin=true;f.config.failDaytrade=true;
  const result=await f.get();assert.equal(result.data.rows.find(r=>r.code==='1785').margin,'available');
  assert.equal(result.data.complete,false);assert.equal(result.response.headers.get('Cache-Control'),'no-store');
  const sources=await f.loadSources();assert.equal(sources.expiresAt-f.now(),10_000);
});
test('simultaneous trading-status consumers share one source request batch',async()=>{
  const f=fixture();const [first,second]=await Promise.all([f.loadSources(),f.loadSources()]);
  assert.equal(first,second);assert.equal(f.calls.filter(u=>u.includes('MI_MARGN')).length,1);
});
