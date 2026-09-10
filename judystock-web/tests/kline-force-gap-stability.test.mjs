import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import {createForceChartScale} from '../lib/kline-force-scale.ts';
import {selectWatchlistCandleWindow} from '../lib/watchlist-candle-window.ts';

const runtimeSource=readFileSync(new URL('../app/api/kline-runtime/route.ts',import.meta.url),'utf8');
const module={exports:{}};
vm.runInNewContext(ts.transpileModule(runtimeSource,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{
  exports:module.exports,require:name=>name==='next/server'?{NextResponse:Response}:name.includes('?raw')?{default:readFileSync(new URL('../vendor/hanstock-kline-runtime.txt',import.meta.url),'utf8')}:{createForceChartScale,selectWatchlistCandleWindow},
  fetch:async()=>{throw Error('upstream unavailable');},Response,URL,AbortSignal,Map,Date,Math,
});
const response=await module.exports.GET({nextUrl:new URL('http://localhost/api/kline-runtime?asset=/assets/index-CCs-RpRr.js')});
const runtime=await response.text();
const callback=runtime.match(/ae\.mainForceNet\.map\((\(A,K\)=>\{if\(decodeURIComponent[\s\S]*?"force-missing-"\+K\)\})\),ae\.mainForceNet\.map/)?.[1];
assert.ok(callback,'execute the actual callback emitted in the shipped vendor runtime');
function labels(values,ticker='6187',historicalViewport=false){
  const before=[...values];
  const ke=values.map((_,i)=>({date:String(i)}));
  const ct=historicalViewport?[...ke,{date:'later'}]:ke;
  const render=vm.runInNewContext('('+callback+')',{location:{pathname:'/kline-window/'+ticker},decodeURIComponent,ke,ct,ae:{mainForceNet:values,x:i=>i*10,mainForceZeroY:100},s:{jsx:(type,props,key)=>({type,props,key})},w:false});
  const result=values.map(render).filter(Boolean);
  assert.deepEqual(values,before,'display rules must not change force data');
  return result;
}

test('live force gaps of two through five bars stay quiet; six bars show one pending label',()=>{
  for(const count of [2,3,4,5])assert.equal(labels([20,...Array(count).fill(null)]).length,0);
  const result=labels([20,...Array(6).fill(null)]);
  assert.equal(result.length,1);assert.equal(result[0].props.children,'主力資料待補');assert.equal(result[0].props.x,35);
});
test('historical force gaps retain the three-bar threshold and independent labels',()=>{
  assert.equal(labels([null,null,5]).length,0);
  assert.equal(labels([null,null,null,5]).length,1);
  assert.equal(labels([null,null,null,5,null,null,null,6]).length,2);
});
test('force completion and real zero split missing runs without inventing values',()=>{
  assert.equal(labels([1,null,null,null,null,null,0]).length,1);
  assert.equal(labels([1,null,null,0,null,null]).length,0);
  assert.equal(labels([1,0,0,0]).length,0);
  assert.equal(labels([null,null,null,null,null,null],'OTC').length,0);
});
test('panning into past bars keeps historical gap labels even at the viewport right edge',()=>{
  assert.equal(labels([1,null,null,null],'6187',true).length,1);
  assert.equal(labels([1,null,null],'6187',true).length,0);
});
test('short live-force gaps alternating between two and three bars never flash a pending label',()=>{
  for(const values of [[1,null,null],[1,null,null,null],[1,2,null,null],[1,2,null,null,null]])assert.equal(labels(values).length,0);
});
test('runtime, embed loader and preload use the same new force-gap revision',()=>{
  const revision=runtimeSource.match(/const KLINE_RUNTIME_REVISION = "([^"]+)"/)?.[1];
  const embed=readFileSync(new URL('../app/api/kline-embed/[ticker]/route.ts',import.meta.url),'utf8');
  const layout=readFileSync(new URL('../app/layout.tsx',import.meta.url),'utf8');
  assert.match(revision,/v40$/);
  assert.equal(embed.match(/const KLINE_RUNTIME_REVISION = "([^"]+)"/)?.[1],revision);
  assert.equal(layout.match(/href="\/api\/kline-runtime\?rev=([^&"]+)/)?.[1],revision);
  assert.equal(response.headers.get('X-HanStock-Runtime-Revision'),revision);
});
