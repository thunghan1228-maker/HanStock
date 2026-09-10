import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

test('intraday indicator reads preserve signal inputs without loading or rebuilding market history', async () => {
  const source = readFileSync(new URL('../app/api/technical-market/route.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const mod = {exports:{}};
  let indicatorReads = 0;
  const payload = {code:'2408',market:'twse',date:'2026/09/04',close:496,technicalReady:true,availableTradingDays:240,riverBase:450,riverScore:82,riverSide:'bull',maLiveBaseSums:{5:1900,20:8700}};
  const forbidden = () => { throw new Error('full history must not be read during an intraday scan'); };
  const dependencies = {
    RIVER_MA_PERIODS:[5,10,20,60,120,240],
    readTechnicalMarketIndicators:async () => {indicatorReads++; return [{code:payload.code,market:payload.market,dataDate:payload.date,payload}];},
    readLatestTechnicalMarketSnapshot:async () => ({tradeDate:'2026/09/04',rows:[{code:'2408',market:'twse',close:496}]}),
    readTechnicalMarketSnapshots:forbidden, saveTechnicalMarketSnapshots:forbidden,
  };
  vm.runInNewContext(compiled,{exports:mod.exports,require:()=>dependencies,Response,Date,URL,fetch:forbidden});
  const request = {nextUrl:new URL('https://site.test/api/technical-market?fast=1')};
  const first = await (await mod.exports.GET(request)).json();
  const second = await (await mod.exports.GET(request)).json();
  assert.equal(first.ok,true);
  assert.equal(indicatorReads,1);
  assert.deepEqual(first.rows,second.rows);
  assert.equal(first.rows[0].riverScore,82);
  assert.equal(first.rows[0].riverSide,'bull');
  assert.deepEqual(first.rows[0].maLiveBaseSums,{5:1900,20:8700});
});
