import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import ts from 'typescript';
const s=readFileSync(new URL('../app/api/daytrade-early-sell/route.ts',import.meta.url),'utf8');const code=ts.transpileModule(s.slice(s.indexOf('const extraLargeHistoryCursor ='),s.indexOf('async function backfillExtraLargeSellSignals(')),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
test('historical repair joins only same-minute prices and force amounts from the requested session',async()=>{
 const ts0=Date.parse('2026-09-04T09:00:00+08:00');
 const fetch=async url=>({ok:true,json:async()=>String(url).includes('/trpc/')?[{result:{data:{json:{candles:[{date:'09/04 09:00',close:100,volume:200000},{date:'09/03 09:01',close:999,volume:999}]}}}}]:{bars:[{ts:ts0,main_buy_amount:10,main_sell_amount:20},{ts:ts0+60000,main_buy_amount:30,main_sell_amount:40}]}});
 const load=new Function('fetch','patternHistoryDate','taipeiMinuteParts',code+';return loadExtraLargeHistoricalBars')(fetch,(raw)=>raw.startsWith('09/04')?'2026-09-04':'2026-09-03',()=>({tradeDate:'2026-09-04'}));
 const result=await load('https://example.com','2026-09-04',['2317']);const bars=result.get('2317');assert.equal(bars.length,1);assert.equal(bars[0].close,100);assert.equal(bars[0].volume,200);assert.equal(bars[0].main_sell_amount,20);assert.equal(bars[0].ts,ts0);
});
