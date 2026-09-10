import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {mergeKlineSnapshot,cleanKlineSnapshot} from '../lib/kline-snapshot.ts';
import {candleSnapshotBootstrap} from '../lib/kline-candle-bootstrap.ts';

const candle=(date,extra={})=>({date,open:250,high:255,low:248,close:253,volume:100,...extra});
test('five-minute grid removes minute quote placeholders and future bars, sorts Taiwan labels consistently',()=>{
  const now=Date.parse('2026-09-08T09:25:46+08:00');
  const rows=[candle('09/08 09:25',{ts:1788830700000}),candle('09/08 09:03',{volume:0}),candle('09/08 09:04',{volume:0}),candle('09/08 09:30'),candle('09/08 09:00')];
  const clean=cleanKlineSnapshot({candles:rows},'5m',now);
  assert.deepEqual(clean.candles.map(b=>b.date),['09/08 09:00','09/08 09:25']);
  assert.equal(clean.candles[0].ts,Date.parse('2026-09-08T09:00:00+08:00'));
  assert.equal(cleanKlineSnapshot({candles:[rows[1]]},'1m',now).candles.length,1);
});
test('partial and delayed responses retain history, completed OHLC and observed main force',()=>{
  const old={candles:[candle('09/02 09:00'),candle('09/03 09:00'),candle('09/04 09:00',{mainForceAvailable:true,mainNetVolume:4000,mainBuyAmount:1000})]};
  const merged=mergeKlineSnapshot(old,{candles:[candle('09/04 09:00',{volume:1,close:251,mainForceAvailable:false,mainNetVolume:0}),candle('09/04 09:05')]});
  assert.equal(merged.candles.length,4);
  assert.deepEqual(merged.candles.slice(0,3),old.candles);
  assert.deepEqual(mergeKlineSnapshot(merged,{candles:[]}).candles,merged.candles);
});
test('new complete observations update prices and real zero force without inventing missing bars',()=>{
  const old={candles:[candle('09/04 09:00',{mainForceAvailable:true,mainNetVolume:4000})]};
  const fresh=candle('09/04 09:00',{close:254,volume:120,mainForceAvailable:true,mainNetVolume:0});
  assert.deepEqual(mergeKlineSnapshot(old,{candles:[fresh,candle('09/04 09:05',{close:0})]}).candles,[fresh]);
});
test('daily corrections replace obsolete volume and storage stays bounded to 31 intraday sessions',()=>{
  const daily=mergeKlineSnapshot({candles:[candle('2026-09-04')]},{candles:[candle('2026-09-04',{volume:50,close:126})]});
  assert.equal(daily.candles[0].close,126);
  const bars=Array.from({length:45},(_,i)=>{const ts=Date.UTC(2026,6,i+1);return candle(new Date(ts).toISOString().slice(5,10).replace('-','/')+' 09:00',{ts})});
  assert.equal(mergeKlineSnapshot(null,{candles:bars}).candles.length,31);
});
function page(storage,fetcher){
  const window={fetch:fetcher},localStorage={get length(){return storage.size},key:i=>[...storage.keys()][i],getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
  runInNewContext(candleSnapshotBootstrap().replace(/<\/?script[^>]*>/g,''),{window,localStorage,Date,Response,URL,location:{origin:'https://example.test'}});
  return window;
}
const url=(ticker,interval='5m')=>'/api/trpc/stocks.candles?batch=1&input='+encodeURIComponent(JSON.stringify({0:{json:{ticker,interval}}}));
test('reopening iframe reuses saved candles immediately, while network replies update that instrument only',async()=>{
  const storage=new Map(),data={candles:[candle('09/04 09:00')]};let calls=0;
  let window=page(storage,async()=>{calls++;return Response.json([{result:{data:{json:data}}}])});
  await window.fetch(url('3163'));
  window=page(storage,async()=>{throw Error('offline')});
  assert.deepEqual(JSON.parse(JSON.stringify(window.__hanstockReadCandleSnapshot('3163','5m'))),data);
  assert.equal(window.__hanstockReadCandleSnapshot('2317','5m'),undefined);
  assert.equal(window.__hanstockReadCandleSnapshot('3163','1m'),undefined);
  assert.equal(calls,1);
});
test('snapshot storage evicts only its own oldest entries, tolerates blocked storage and leaves other API replies alone',async()=>{
  const storage=new Map([['hanstock-watchlist','keep']]),data={candles:[candle('09/04 09:00')]};
  const window=page(storage,async()=>Response.json([{result:{data:{json:data}}}]));
  for(let i=0;i<8;i++)await window.fetch(url(String(2300+i)));
  assert.equal(storage.size,6);assert.equal(storage.get('hanstock-watchlist'),'keep');
  await window.fetch('/api/trpc/account.info');assert.equal(storage.size,6);
  assert.doesNotThrow(()=>runInNewContext(candleSnapshotBootstrap().replace(/<\/?script[^>]*>/g,''),{window:{fetch:async()=>Response.json({})},get localStorage(){throw Error('blocked')}}));
});
