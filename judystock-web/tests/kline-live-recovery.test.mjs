import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as diagnostics from '../lib/market-source-diagnostics.ts';
import {klineLiveQuoteBootstrap} from '../lib/kline-live-quote-bootstrap.ts';

test('the missing live-bars route returns real candles and exposes provider failures without manufacturing bars', async()=>{
  const source=readFileSync(new URL('../app/api/live-bars/[ticker]/route.ts',import.meta.url),'utf8');
  const mod={exports:{}};let failed=false,calls=0;
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
    exports:mod.exports,AbortSignal,
    require:name=>name==='next/server'?{NextResponse:Response}:name.includes('market-source-diagnostics')?diagnostics:{fetchCachedMarket:async()=>{calls++;return failed?Response.json({status:'error'},{status:502}):Response.json({status:'ok',candles:[{date:'09/07 09:25',open:500,high:501,low:499,close:500,volume:10}]})}},
  });
  const get=ticker=>mod.exports.GET(null,{params:Promise.resolve({ticker})});
  const live=await get('2408');assert.equal(live.status,200);assert.equal((await live.json()).candles[0].date,'09/07 09:25');
  failed=true;const delayed=await get('2408');assert.equal(delayed.status,503);assert.deepEqual((await delayed.json()).candles,[]);
  assert.equal((await get('../secret')).status,400);assert.equal(calls,2);
});

test('latest trade and delayed candle times are distinct and displaying a quote does not change OHLC',()=>{
  let strip;const listeners={};
  const candles=[{date:'09/07 09:10',open:522,high:525,low:518,close:522,volume:100}];
  class MarketDate extends Date {constructor(...args){super(...(args.length?args:['2026-09-07T09:30:00+08:00']))}static now(){return Date.parse('2026-09-07T09:30:00+08:00')}}
  vm.runInNewContext(klineLiveQuoteBootstrap().replace(/<\/?script[^>]*>/g,''),{
    window:{__hanstockAllCandles:candles,addEventListener:(name,fn)=>listeners[name]=fn},
    document:{body:{appendChild:node=>strip=node},createElement:()=>({style:{},setAttribute(){}}),getElementById:()=>strip,addEventListener(){}},
    Date:MarketDate,setInterval(){},
  });
  listeners['hanstock-live-quote']({detail:{quote:{price:518.5,changePct:4.54},mode:'live',fetchedAt:'2026-09-07T01:30:00Z'}});
  assert.match(strip.textContent,/最新成交 518.5/);
  assert.match(strip.textContent,/09:30:00/);
  assert.match(strip.textContent,/K 線截至 09\/07 09:10/);
  assert.match(strip.textContent,/分 K 來源延遲/);
  assert.equal(candles.length,1);assert.equal(candles[0].close,522);
});
