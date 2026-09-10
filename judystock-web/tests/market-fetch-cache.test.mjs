import test from 'node:test';
import assert from 'node:assert/strict';
import { createMarketFetchCache, marketCacheWindow } from '../lib/market-fetch-cache.ts';

const time = value => Date.parse(value + '+08:00');
test('cash open invalidates closed cache; futures night and Saturday morning remain live', () => {
  const cash = {ticker:'2327',activeMs:15000};
  assert.equal(marketCacheWindow(cash,time('2026-09-04T14:30:00')).ttl,15000);
  assert.equal(marketCacheWindow(cash,time('2026-09-05T10:00:00')).ttl,300000);
  assert.notEqual(marketCacheWindow(cash,time('2026-09-07T08:44:59')).phase,marketCacheWindow(cash,time('2026-09-07T08:45:00')).phase);
  for (const clock of ['2026-09-04T21:00:00','2026-09-05T04:59:00']) assert.equal(marketCacheWindow({ticker:'TXF1'},time(clock)).ttl,5000);
  for (const clock of ['2026-09-05T05:00:00','2026-09-06T01:00:00','2026-09-07T01:00:00']) assert.equal(marketCacheWindow({ticker:'TXF1'},time(clock)).ttl,300000);
  assert.equal(marketCacheWindow({ticker:'UNKNOWN'},time('2026-09-05T10:00:00')).ttl,5000);
});

test('simultaneous reads share one transfer, bodies are independent, and history refreshes on expiry', async () => {
  let calls=0, clock=time('2026-09-04T10:00:00'), release;
  const gate=new Promise(resolve=>release=resolve);
  const cached=createMarketFetchCache(async()=>{calls++;await gate;return Response.json({bars:[{close:calls}]});},()=>clock);
  const options={ticker:'2327',history:true};
  const a=cached('https://example.test/history',{},options),b=cached('https://example.test/history',{},options);
  release();
  assert.deepEqual(await(await a).json(),await(await b).json());
  clock+=15000; await cached('https://example.test/history',{},options);
  assert.equal(calls,1);
  clock+=300000; await cached('https://example.test/history',{},options);
  assert.equal(calls,2);
});

test('opening a session refreshes immediately and failures recover without a cached error', async () => {
  let calls=0, clock=time('2026-09-07T08:44:59');
  const cached=createMarketFetchCache(async()=>{calls++; return calls===1 ? Response.json({bars:[]},{status:502}) : Response.json({bars:[calls]});},()=>clock);
  const options={ticker:'2327'};
  await cached('x',{},options); await cached('x',{},options); await cached('x',{},options);
  assert.equal(calls,2);
  clock+=1000; await cached('x',{},options); assert.equal(calls,3);
});

test('LRU entry and byte caps release old responses; oversized payloads are delivered without retaining them', async () => {
  let calls=0;
  const cached=createMarketFetchCache(async()=>{calls++;return Response.json({bars:['x'.repeat(100)]});},()=>1,2,10000);
  await cached('a',{}); await cached('b',{}); await cached('a',{}); await cached('c',{}); await cached('b',{});
  assert.equal(calls,4);
  const small=createMarketFetchCache(async()=>{calls++;return Response.json({bars:['x'.repeat(100)]});},()=>1,2,100);
  await small('a',{}); await small('a',{}); assert.equal(calls,6);
});

test('private responses, errors and mutations cannot enter the shared cache', async () => {
  for(const headers of [{'cache-control':'private'},{'set-cookie':'session=x'}]){
    let calls=0;const cached=createMarketFetchCache(async()=>{calls++;return Response.json({bars:[1]},{headers});});
    await cached('a',{});await cached('a',{});assert.equal(calls,2);
  }
  const cached=createMarketFetchCache(async()=>{throw Error('should not send');});
  await assert.rejects(cached('a',{method:'POST'}),/public_get/);
  await assert.rejects(cached('a',{headers:{Authorization:'secret'}}),/public_get/);
});
