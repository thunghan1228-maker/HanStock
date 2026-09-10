import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { hasIncompleteIntradaySession } from "../lib/kline-history.ts";
import * as daily from "../lib/kline-daily.ts";
import * as volume from "../lib/kline-volume.ts";
import { replayKlineSignals, mergeKlineSignals } from "../lib/kline-signals.ts";
import { createMarketFetchCache, marketCacheWindow } from "../lib/market-fetch-cache.ts";
import { createSavedForceCache } from "../lib/saved-force-cache.ts";
import { mergeKlineSnapshot, cleanKlineSnapshot } from "../lib/kline-snapshot.ts";
import { successfulSourcesInCompletionOrder } from "../lib/successful-source-stream.ts";
import { setImmediate as nextTurn } from "node:timers/promises";

const saturday = Date.parse("2026-09-05T08:00:00+08:00");
const at = (date, clock) => Date.parse(`${date}T${clock}:00+08:00`);
const bar = (ts, extra = {}) => ({ ts, open: 252.5, high: 254, low: 252, close: 253, volume: 1000, ...extra });
const session = (day, step = 5) => Array.from({ length: 270 / step }, (_, index) => bar(at(day, "09:00") + index * step * 60000));

test("empty history and Friday's single opening bar require weekend repair", () => {
  assert.equal(hasIncompleteIntradaySession([], "5m", saturday), true);
  assert.equal(hasIncompleteIntradaySession([bar(at("2026-09-04", "09:00"))], "5m", saturday), true);
  assert.equal(hasIncompleteIntradaySession(session("2026-09-04"), "5m", saturday), false);
  assert.equal(hasIncompleteIntradaySession(session("2026-09-04", 1), "1m", saturday), false);
});

test("detects missing middle/tail and ignores unusable price rows", () => {
  const full = session("2026-09-04");
  assert.equal(hasIncompleteIntradaySession(full.slice(0, 20), "5m", saturday), true);
  assert.equal(hasIncompleteIntradaySession(full.filter((_, i) => i < 5 || i > 30), "5m", saturday), true);
  assert.equal(hasIncompleteIntradaySession(full.map(b => ({ ...b, close: null })), "5m", saturday), true);
});

test("does not demand a full day at the opening or invent weekend candles", () => {
  assert.equal(hasIncompleteIntradaySession([bar(at("2026-09-04", "09:00"))], "5m", at("2026-09-04", "09:05")), false);
  const lastYear = session("2025-12-31").map(({ ts, ...b }) => ({ ...b, date: `12/31 ${new Date(ts + 28800000).toISOString().slice(11, 16)}` }));
  assert.equal(hasIncompleteIntradaySession(lastYear, "5m", at("2026-01-01", "10:00")), false);
});

function loadRoute(fetch, overrides = {}) {
  const source = readFileSync(new URL("../app/api/trpc/[...path]/route.ts", import.meta.url), "utf8");
  const script = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, "").replace(/^export const /gm, "const "));
  class Clock extends Date { static now() { return saturday; } }
  const context = { ...daily, ...volume, replayKlineSignals: (ticker, bars) => replayKlineSignals(ticker, bars, saturday), mergeKlineSignals, hasIncompleteIntradaySession: (bars, interval) => hasIncompleteIntradaySession(bars, interval, saturday), fetch, Date: Clock, URL, Headers, Request, Response, AbortSignal, setTimeout, NextResponse: class extends Response { static json(data, init) { return new Response(JSON.stringify(data), init); } } };
  context.fetchCachedMarket = createMarketFetchCache(fetch, () => saturday);
  context.marketCacheWindow = (policy) => marketCacheWindow(policy, saturday);
  Object.assign(context, { successfulSourcesInCompletionOrder, createSavedForceCache, mergeKlineSnapshot, cleanKlineSnapshot: (value, interval) => cleanKlineSnapshot(value, interval, saturday), readKlineSnapshot: async () => null, saveKlineSnapshot: async () => {}, after() {} }, overrides);
  return runInNewContext(`${script}\n({ GET, proxy, fetchRepair, mergeRepair, aggregateYahooFiveMinuteBars, loadHubRepairFromSources, fetchOtcBreak35Signals })`, context);
}

function yahooPayload() {
  const bars = session("2026-09-04", 1);
  bars.push(bar(at("2026-09-04", "13:30"), { volume: 5000 }));
  return { chart: { result: [{ timestamp: bars.map(b => b.ts / 1000), indicators: { quote: [{ open: bars.map(b => b.open), high: bars.map(b => b.high), low: bars.map(b => b.low), close: bars.map(b => b.close), volume: bars.map(b => b.volume) }] } }] } };
}

test("new Worker serves durable candle snapshots for single and batched requests without contacting upstream", async () => {
  const saved = { candles: [{ date: "09/04 09:00", ...bar(at("2026-09-04", "09:00")) }], ticker: "3163", interval: "5m" };
  for (const batch of [false, true]) {
    const route = loadRoute(() => { throw Error("must not refetch completed candles"); }, {
      createSavedForceCache: options => createSavedForceCache({ ...options, now: () => saturday }),
      readKlineSnapshot: async () => ({ value: saved, available: true, refreshedAt: saturday, phase: marketCacheWindow({ ticker: "3163" }, saturday).phase }),
      after: () => { throw Error("no refresh necessary"); },
    });
    const input = { json: { ticker: "3163", interval: "5m" } };
    const request = new Request("http://localhost/api/trpc/stocks.candles?" + (batch ? "batch=1&" : "") + "input=" + encodeURIComponent(JSON.stringify(batch ? { 0: input } : input)));
    request.nextUrl = new URL(request.url);
    const response = await route.GET(request, { params: Promise.resolve({ path: ["stocks.candles"] }) });
    const result = await response.json();
    assert.deepEqual((batch ? result[0] : result).result.data.json, saved);
    assert.equal(response.headers.get("x-hanstock-kline-source"), "saved-snapshot-first");
  }
});

test("first response waits for real backfill despite a false history_ok flag and >80ms latency", async () => {
  const calls = [];
  const route = loadRoute(async (url) => {
    const u = String(url); calls.push(u);
    if (u.includes("/api/trpc/")) return Response.json([{ result: { data: { json: { candles: [{ date: "09/04 09:00", ...bar(undefined), mainForceAvailable: true, mainNetVolume: 1599000 }] } } } }]);
    if (u.includes("/api/hub/")) { await new Promise(resolve => setTimeout(resolve, 120)); return Response.json({ bars: [], bootstrap: { history_ok: true, auto_repair: { waiting: false } } }); }
    if (u.includes("2317.TW?")) return Response.json(yahooPayload());
    return new Response("", { status: 404 });
  });
  const request = new Request("http://localhost/api/trpc/stocks.candles?batch=1&input=" + encodeURIComponent(JSON.stringify({ 0: { json: { ticker: "2317", interval: "5m" } } })));
  request.nextUrl = new URL(request.url);
  const response = await route.proxy(request, { params: Promise.resolve({ path: ["stocks.candles"] }) });
  const json = (await response.json())[0].result.data.json;
  assert.equal(json.candles.length, 54);
  assert.equal(json.candles[0].date, "09/04 09:00");
  assert.equal(json.candles.at(-1).date, "09/04 13:25");
  assert.equal(json.candles.at(-1).volume, 10000);
  assert.equal(json.candles[0].mainNetVolume, 1599000);
  assert.equal(json.candles[0].mainForceAvailable, true);
  assert.equal(json.candles[1].mainForceAvailable, false);
  assert.equal(json.autoRepair.historyOk, true);
  assert.equal(response.headers.get("x-hanstock-kline-auto-repair"), "repaired");
  assert.ok(calls.some(u => u.includes("finance.yahoo.com")));
});

test("complete Hub history avoids fallback and one-minute fallback keeps each minute", async () => {
  const calls = [];
  const route = loadRoute(async url => { calls.push(String(url)); return Response.json({ bars: session("2026-09-04"), bootstrap: { history_ok: true } }); });
  const repaired = await route.fetchRepair({ ticker: "2317", interval: "5m" });
  assert.equal(repaired.bars.length, 54);
  assert.equal(repaired.bars[0].volume, 1000000);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(url => url.includes('/api/hub/bars/2317')));
  assert.deepEqual(new Set(calls.map(url => new URL(url).origin)), new Set(['https://hanstock.xyz', 'https://hanstock-production.up.railway.app']));
  const oneMinute = loadRoute(async url => String(url).includes("/api/hub/") ? Response.json({ bars: [], bootstrap: { history_ok: true } }) : Response.json(yahooPayload()));
  assert.equal((await oneMinute.fetchRepair({ ticker: "2317", interval: "1m" })).bars.length, 271);
});

test("repairs the correct signal batch entry while keeping other results and stored observations", async () => {
  const old = { id: 42, ticker: "2317", kind: "crossUp905", barTs: at("2026-09-03", "09:05"), tradeDate: "2026-09-03", note: "stored" };
  const payload = [{ result: { data: { json: { unchanged: true } } } }, { result: { data: { json: [old] } } }];
  const route = loadRoute(async url => {
    if (String(url).includes("intradaySignalsByTicker")) return Response.json(payload);
    if (String(url).includes("/api/trpc/")) return Response.json({ result: { data: { json: { candles: [] } } } });
    return Response.json({ bars: session("2026-09-04"), bootstrap: { history_ok: true } });
  });
  const operations = "stocks.quote,schedule.intradaySignalsByTicker";
  const request = new Request("http://localhost/api/trpc/" + operations + "?batch=1&input=" + encodeURIComponent(JSON.stringify({ 0: { json: { ticker: "2317" } }, 1: { json: { ticker: "2317", sinceTs: at("2026-09-03", "09:00") } } })));
  request.nextUrl = new URL(request.url);
  const response = await route.proxy(request, { params: Promise.resolve({ path: [operations] }) });
  const result = await response.json();
  assert.deepEqual(result[0], payload[0]);
  assert.equal(result[1].result.data.json.find(row => row.id === 42).note, "stored");
  assert.ok(result[1].result.data.json.some(row => row.tradeDate === "2026-09-04" && row.kind === "crossUp905"));
  assert.equal(response.headers.get("x-hanstock-kline-signal-source"), "stored+chart-replay");
});

test("failed fallback preserves original candles and reports waiting instead of complete", async () => {
  const route = loadRoute(async () => { throw new Error("source unavailable"); });
  const payload = [{ result: { data: { json: { candles: [{ date: "09/04 09:00", ...bar(undefined) }] } } } }];
  const result = route.mergeRepair(payload, await route.fetchRepair({ ticker: "2317", interval: "5m" }), 0, "5m");
  assert.equal(result.payload[0].result.data.json.candles.length, 1);
  assert.equal(result.waiting, true);
});

test("K-line repair uses populated backup before hanging primary and cancels the peer", {timeout: 2000}, async () => {
  const calls = [];
  const route = loadRoute(async (url, init) => {
    calls.push({url, init});
    if (url.startsWith('https://hanstock.xyz')) return new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true}));
    return Response.json({code: '2317', bars: session('2026-09-04'), bootstrap: {history_ok: true}});
  });
  const result = await route.fetchRepair({ticker: '2317', interval: '5m'});
  assert.equal(result.bars.length, 54);
  assert.equal(result.bars[0].volume, 1_000_000);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(c => c.init.signal.aborted));
  assert.equal(calls[1].init.headers['User-Agent'], 'HanStock-Battle-Kline-Repair/1.0');
});

test("K-line repair waits past empty fast backup for populated primary", {timeout: 2000}, async () => {
  let respond;
  const pending = new Promise(resolve => {respond = resolve;});
  const route = loadRoute(async url => url.startsWith('https://hanstock.xyz') ? pending : Response.json({bars: []}));
  let done = false;
  const result = route.loadHubRepairFromSources('/api/hub/bars/2317', '2317', 5000, 'test').then(x => {done = true;return x;});
  await nextTurn();
  assert.equal(done, false);
  respond(Response.json({bars: [bar(1)], code: '2317'}));
  assert.equal((await result).bars[0].close, 253);
});

test("K-line repair tolerates HTTP error or malformed JSON from either origin", async () => {
  for (const failure of ['http', 'json']) {
    const route = loadRoute(async url => url.startsWith('https://hanstock.xyz')
      ? new Response('not-json', {status: failure === 'http' ? 503 : 200})
      : Response.json({bars: [bar(1)], bootstrap: {history_ok: false}}));
    const result = await route.loadHubRepairFromSources('/api/hub/bars/2317', '2317', 5000, 'test');
    assert.equal(result.bars.length, 1);
    assert.equal(result.bootstrap.history_ok, false);
  }
});

test("K-line repair retains empty status only after other source fails and returns null for total failure", async () => {
  for (const empty of [false, true]) {
    const route = loadRoute(async url => {
      if (empty && url.includes('railway.app')) return Response.json({bars: [], bootstrap: {auto_repair: {waiting: true}}});
      throw new Error('unavailable');
    });
    const result = await route.loadHubRepairFromSources('/api/hub/bars/2317', '2317', 5000, 'test');
    if (empty) {assert.equal(result.bars.length, 0);assert.equal(result.bootstrap.auto_repair.waiting, true);}
    else assert.equal(result, null);
  }
});

test("K-line hub race preserves per-interval timeout and existing endpoint parameters", async () => {
  for (const [ticker, interval, path, timeout] of [
    ['2317','1m','/api/hub/bars1m/2317',5000],
    ['2317','5m','/api/hub/bars/2317',5000],
    ['2317','1d','/api/hub/bars1m/2317?days=1&limit=600&backfill=false',15000],
  ]) {
    const calls=[], timeouts=[];
    const route=loadRoute(async url => {
      if (url.includes('/api/hub/')) {calls.push(url);return Response.json({bars: session('2026-09-04'), bootstrap:{history_ok:true}});}
      return new Response('',{status:404});
    }, {loadOfficialOtcMinuteBars:async()=>[], AbortSignal:{any:AbortSignal.any,timeout:ms=>{timeouts.push(ms);return AbortSignal.timeout(ms);}}});
    await route.fetchRepair({ticker,interval});
    assert.equal(calls.length,2);
    assert.ok(calls.every(url=>url.endsWith(path)));
    assert.equal(timeouts.filter(ms=>ms===timeout).length >= 2,true);
  }
});


