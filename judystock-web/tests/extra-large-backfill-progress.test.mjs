import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const source=readFileSync(new URL('../app/api/daytrade-early-sell/route.ts',import.meta.url),'utf8');
const code=ts.transpileModule(source.slice(source.indexOf('async function backfillExtraLargeSellSignals('),source.indexOf('async function qualifyStoredExtraLargeSignals(')),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
function setup(available,liveFails=false){
 const deps={extraLargeSellBackfillAttempts:new Map(),loadPreviousLargeNetBaselines:async()=>[{ticker:'2317',dataDate:'2026-09-03'}],extraLargeSellCandidateTickers:()=>['2317'],extraLargeBuyCandidateTickers:()=>[],loadExtraLargeHistoricalBars:async()=>new Map(),loadMinuteBars:async()=>{if(liveFails)throw Error('upstream');return new Map();},calculateIntradayExtraLargeSellSignals:()=>[],calculateIntradayExtraLargeBuySignals:()=>[],loadStoredMinuteBars:async()=>new Map([['2317',[{ts:1,main_buy_amount:10,main_sell_amount:20}]]]),annotateMainForceGroupRanks:x=>x,signalGroupsByTicker:{},annotatePreviousChipRanks:async x=>x,saveEarlySellSignals:async()=>{},storedSnapshotCache:new Map(),fullMarketBaselineProgress:new Map([['2026-09-03',{available,total:2000}]]),taipeiMinuteParts:()=>({tradeDate:'2026-09-04'}),marketStocks:Array(2000)};
 return new Function(...Object.keys(deps),code+';return backfillExtraLargeSellSignals')(...Object.values(deps));
}
test('partial baseline batch with zero signals is not reported as complete',async()=>{const result=await setup(64)('', '2026-09-04',{});assert.equal(result.completed,false);assert.equal(result.baselineAvailable,64);assert.equal(result.generated,0);});
test('saved minute history is still checked when live provider fails',async()=>{const result=await setup(2000,true)('', '2026-09-04',{});assert.equal(result.completed,true);assert.equal(result.minuteAvailable,1);});
