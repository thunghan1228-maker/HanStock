import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";
import { mergeForceHistory } from "../lib/kline-force.ts";
import { intradayForceBootstrap } from "../lib/kline-force-bootstrap.ts";
import { createSavedForceCache } from "../lib/saved-force-cache.ts";

const ts = clock => Date.parse(`2026-09-03T${clock}:00+08:00`);
const known = { ts: ts("09:00"), date: "09/03 09:00", net: 0.02, buyAmount: 6000000, sellAmount: 1000000, netAmount: 5000000, mainForceAvailable: true, amountsAvailable: true };

test("sparse or zero-placeholder refreshes preserve previous force, amounts and cumulative totals", () => {
  const rows = mergeForceHistory([known], [
    { ...known, mainForceAvailable: false, net: 0, buyAmount: 0, sellAmount: 0 },
    { date: "09/03 09:05", net: -0.01, mainForceAvailable: true },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].buyAmount, 6000000);
  assert.equal(rows[1].dayNet, 0.01);
  const missingAmounts = mergeForceHistory(rows, [{ date: known.date, net: 0.02, amountsAvailable: false }]);
  assert.equal(missingAmounts[0].netAmount, 5000000);
  assert.equal(mergeForceHistory([known], [{ date: known.date, net: 0 }])[0].net, 0.02);
  assert.equal(mergeForceHistory([known], [{ ...known, net: 0, buyAmount: 0, sellAmount: 0 }])[0].net, 0);
  assert.equal(mergeForceHistory(rows, [{ date: "09/04 09:00", net: 0.03 }]).at(-1).dayNet, 0.03);
});

function loadForceRoute(extraContext = {}) {
  const source = readFileSync(new URL("../app/api/force-bars/route.ts", import.meta.url), "utf8");
  const script = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, "").replace(/export async function/g, "async function"));
  const saved = { ticker: "2317", tradeDate: "2026-09-03", interval: "5m", barTime: "2026-09-03 09:00", netVolume: 20000, buyAmount: 6000000, sellAmount: 1000000, netAmount: 5000000, mainTickCount: 3, updatedAt: 1 };
  const writes = [];
  const context = { Date, Map, Set, Promise, createSavedForceCache, readIntradayForceHistory: async () => [saved], saveDailyForce: async r => writes.push(r), saveIntradayForce: async r => writes.push(...r) };
  return { ...runInNewContext(`${script}\n({ GET, completeTaiwanSession, loadCompletedDays, persistForceCandles, aggregateForceMinutes, fetchHubForceCandles, readSavedFirst })`, {...context, ...extraContext}), writes };
}

test("history transfers are shared and the backup is requested only after primary failure", async () => {
  const { createMarketFetchCache } = await import('../lib/market-fetch-cache.ts');
  let now = Date.parse('2026-09-04T10:00:00+08:00');
  const calls = [];
  const fetcher = async url => {
    calls.push(url);
    return Response.json({bars:[{ts:now,main_net_volume:-20,main_sell_amount:500000,main_force_available:true}]});
  };
  const route = loadForceRoute({fetchCachedMarket:createMarketFetchCache(fetcher,()=>now),AbortSignal});
  const first = await route.fetchHubForceCandles('2327','1m');
  assert.equal(first[0].mainNetVolume,-20000);
  now += 15000;
  await route.fetchHubForceCandles('2327','1m');
  assert.equal(calls.filter(url=>url.includes('/force/bars/')).length,1);
  assert.equal(calls.filter(url=>url.includes('/bars1m/')).length,2);
  assert.equal(calls.filter(url=>url.includes('railway.app')).length,0);
  const fallbackCalls=[];
  const fallback = loadForceRoute({AbortSignal,fetchCachedMarket:async url=>{
    fallbackCalls.push(url);
    return url.startsWith('https://hanstock.xyz') ? new Response('',{status:502}) : fetcher(url);
  }});
  assert.ok((await fallback.fetchHubForceCandles('2327','1m')).length);
  assert.equal(fallbackCalls.filter(url=>url.includes('railway.app')).length,2);
});

test("server reloads saved dates absent upstream and never persists unknown zero padding", async () => {
  const route = loadForceRoute();
  const recovered = await route.loadCompletedDays("2317", "5m", []);
  assert.equal(recovered.length, 1);
  assert.equal(recovered[0].mainNetVolume, 20000);
  const partial = route.completeTaiwanSession([{ date: "09/03 09:10", mainNetVolume: 0, mainForceAvailable: false }], "5m", "09/03");
  assert.equal(partial.length, 0);
  await route.persistForceCandles("2317", "5m", [{ date: "09/03 09:10", mainNetVolume: 0, mainForceAvailable: false }]);
  assert.equal(route.writes.length, 0);
  const aggregate = route.aggregateForceMinutes([0, 1].map((offset) => ({ ts: ts("09:00") + offset * 60000, date: `09/03 09:0${offset}`, mainNetVolume: 1000, mainBuyAmount: 300000, mainSellAmount: 50000, mainNetAmount: 250000, mainForceAvailable: true, amountsAvailable: true })));
  assert.equal(aggregate[0].mainNetVolume, 2000);
  assert.equal(aggregate[0].mainNetAmount, 500000);
});

test("embedded chart displays saved force without waiting and merges the later update on refresh", async () => {
  const storage = new Map([["hanstock-candle-force-v3:2317:5m", JSON.stringify({ savedAt: Date.now(), bars: { "date:09/03 09:00": { mainNetVolume: 20000, mainBuyAmount: 6000000, mainSellAmount: 1000000 } } })]]);
  const latest = { ...known, ts: ts("09:05"), date: "09/03 09:05", net: -0.01 };
  const window = { dispatchEvent() {}, fetch: async url => {
    if (String(url).includes("force-bars")) { await new Promise(r => setTimeout(r, 130)); return Response.json({ bars: [latest] }); }
    return Response.json([{ result: { data: { json: { candles: ["09:00", "09:05"].map(clock => ({ date: `09/03 ${clock}`, open: 253, close: 254, mainNetVolume: 0, mainForceAvailable: false })) } } } }]);
  } };
  const script = intradayForceBootstrap("2317", "5m").replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
  runInNewContext(script, { window, localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) }, Date, Response, Headers, AbortSignal, Event, setTimeout });
  const result = await (await window.fetch("/api/trpc/stocks.candles")).json();
  const bars = result[0].result.data.json.candles;
  assert.equal(bars[0].mainNetVolume, 20000);
  assert.equal(bars[0].mainBuyAmount, 6000000);
  assert.equal(bars[1].mainForceAvailable, false);
  await window.__hanstockForceBarsPromise;
  const refreshed = await (await window.fetch("/api/trpc/stocks.candles")).json();
  assert.equal(refreshed[0].result.data.json.candles[1].mainNetVolume, -10000);
  assert.equal(window.__hanstockAllCandles.length, 2);
});

test("first chart with no saved force displays candles while force request is pending", async () => {
  let finish;
  const pending = new Promise(resolve => { finish = resolve; }), events = [];
  const window = { dispatchEvent: event => events.push(event.type), fetch: async url => {
    if (String(url).includes("force-bars")) return pending;
    return Response.json([{ result: { data: { json: { candles: [{ date: "09/03 09:00", open: 250, close: 251 }] } } } }]);
  } };
  runInNewContext(intradayForceBootstrap("2317", "5m").replace(/<\/?script[^>]*>/g, ""), { window, localStorage: { getItem: () => null, setItem() {} }, Date, Response, Headers, AbortSignal, Event });
  const response = await Promise.race([window.fetch("/api/trpc/stocks.candles"), new Promise(resolve => setTimeout(() => resolve(null), 80))]);
  assert.ok(response, "price response must not await force");
  finish(Response.json({ bars: [known] }));
  await window.__hanstockForceBarsPromise;
  assert.ok(events.includes("hanstock-force-ready"));
});

test("embedded chart polls force independently from candle refreshes", async () => {
  let now = Date.parse("2026-09-03T09:00:00+08:00"), intervalCallback;
  const requests = [], events = [];
  const responses = [known, { ...known, ts: ts("09:05"), date: "09/03 09:05", net: -0.01 }];
  class Clock extends Date {
    static now() { return now; }
  }
  const window = {
    document: { visibilityState: "visible", addEventListener() {} },
    dispatchEvent: event => events.push(event.type),
    setInterval: callback => { intervalCallback = callback; },
    addEventListener() {},
    fetch: async url => {
      requests.push(String(url));
      return Response.json({ bars: [responses[Math.min(requests.length - 1, responses.length - 1)]] });
    },
  };
  runInNewContext(intradayForceBootstrap("2317", "5m").replace(/<\/?script[^>]*>/g, ""), {
    window,
    localStorage: { getItem: () => null, setItem() {} },
    Date: Clock,
    Response,
    Headers,
    AbortSignal,
    Event,
  });
  await window.__hanstockForceBarsPromise;
  assert.equal(typeof intervalCallback, "function");
  now += 10_000;
  intervalCallback();
  await window.__hanstockForceBarsPromise;
  assert.equal(requests.filter(url => url.includes("force-bars")).length, 2);
  assert.equal(window.__hanstockForceBars.at(-1).date, "09/03 09:05");
  assert.ok(events.includes("hanstock-force-ready"));
});

test("unchanged saved sessions perform no bar or daily-total writes", async () => {
  const route = loadForceRoute();
  const saved = await route.loadCompletedDays("2317", "5m", []);
  await route.persistForceCandles("2317", "5m", saved, saved);
  assert.equal(route.writes.length, 0);
  const changed = [{ ...saved[0], mainNetVolume: 21000 }, { ...saved[0], date: "09/03 09:05", mainNetVolume: -1000 }];
  await route.persistForceCandles("2317", "5m", changed, saved);
  assert.equal(route.writes.filter(row => row.barTime).length, 2);
  assert.equal(route.writes.find(row => row.tradeDate && !row.barTime).netVolume, 20000);
});

test("a failed durable write is retried even when the next provider response is unchanged", async () => {
  let attempts=0;
  const tasks=[], persisted=[{ticker:'2317',tradeDate:'2026-09-03',interval:'5m',barTime:'2026-09-03 09:00',netVolume:20000,
    buyAmount:6000000,sellAmount:1000000,netAmount:5000000,mainTickCount:3,observed:1,updatedAt:1}];
  const route=loadForceRoute({AbortSignal,
    after:task=>tasks.push(task),marketCacheWindow:()=>({phase:'closed',ttl:300000}),
    readForceRefreshState:async()=>null,
    readIntradayForceHistory:async(_ticker,interval)=>interval==='5m'?[...persisted]:[],
    fetchCachedMarket:async()=>Response.json({bars:[{ts:ts('09:05'),main_net_volume:30,main_buy_amount:5000000,main_sell_amount:0,main_force_available:true}]}),
    saveDailyForce:async()=>{},saveForceRefreshState:async()=>{},
    saveIntradayForce:async rows=>{if(++attempts===1)throw Error('database busy');persisted.push(...rows);},
  });
  await route.readSavedFirst('2317','5m'); await Promise.all(tasks);
  const pending=await route.readSavedFirst('2317','5m');
  assert.equal(pending.refreshedAt,0,'failed storage must not be marked fresh');
  assert.equal(pending.value.length,2,'live observations stay visible during retry');
  await Promise.all(tasks);
  assert.equal(attempts,2);
  assert.equal(persisted.find(row=>row.barTime==='2026-09-03 09:05').netVolume,30000);
  assert.ok((await route.readSavedFirst('2317','5m')).refreshedAt>0);
});

test("durable observed zero remains a real bar; old unverified zero padding stays absent", () => {
  const route = loadForceRoute();
  const row = { ticker: "3163", tradeDate: "2026-09-03", interval: "5m", barTime: "2026-09-03 09:00", netVolume: 0, buyAmount: 0, sellAmount: 0, netAmount: 0, mainTickCount: 0 };
  assert.equal(route.completeTaiwanSession([], "5m", "09/03", [row]).length, 0);
  const actual = route.completeTaiwanSession([], "5m", "09/03", [{ ...row, observed: 1, amountsAvailable: 1 }]);
  assert.equal(actual.length, 1);
  assert.equal(actual[0].mainForceAvailable, true);
  assert.equal(actual[0].mainNetVolume, 0);
  assert.equal(actual[0].amountsAvailable, true);
});

test("force API serves durable prices, amounts and cumulative totals without any upstream request or rewrite", async () => {
  const route = loadForceRoute({
    NextResponse: { json: (body, init) => Response.json(body, init) },
    readForceRefreshState: async () => ({ refreshedAt: Date.now(), phase: "closed" }),
    marketCacheWindow: () => ({ phase: "closed", ttl: 300000 }),
    fetchCachedMarket: () => { throw Error("fresh history must not call upstream"); },
    after: () => { throw Error("fresh history must not start background work"); },
  });
  const response = await route.GET({ nextUrl: new URL("https://example.test/api/force-bars?ticker=2317&interval=5m") });
  const data = await response.json();
  assert.equal(response.status, 200);
  assert.equal(data.bars[0].net, .02);
  assert.equal(data.bars[0].netAmount, 5000000);
  assert.equal(data.bars[0].dayNet, .02);
  assert.equal(data.dayNet, .02);
  assert.equal(route.writes.length, 0);
});
