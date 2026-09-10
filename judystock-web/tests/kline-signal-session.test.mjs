import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { createKlineSignalSessionBootstrap } from "../lib/kline-signal-session.ts";
import { resolveIntradaySignalCutoverDate } from "../lib/intraday-signal-session.ts";

const tickerKey = "hanstock-ticker-signal-latch-v2:2317";
const marketKey = "hanstock-market-break15k-latch-v1";
const signal = (date, kind = "crossUp20ma", ticker = "2317") =>
  ({ ticker, kind, barTs: Date.parse(date + "T10:00:00+08:00"), note: "2" });

function browser(now, closed = [], stored = {}) {
  let clock = Date.parse(now);
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  const storage = new Map(Object.entries(stored).map(([key, rows]) => [key, JSON.stringify(rows)]));
  const timers = [], markers = [], events = [], listeners = new Map();
  let payload = [], mutation;
  const window = {
    fetch: async () => Response.json(structuredClone(payload)),
    addEventListener(type, handler) { listeners.set(type, handler); },
    dispatchEvent(event) { events.push(event); listeners.get(event.type)?.(event); },
  };
  runInNewContext(createKlineSignalSessionBootstrap("2317", new Set(closed)).replace(/<\/?script[^>]*>/g, ""), {
    window, Date: Clock, Headers, Response, Intl, CustomEvent,
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    document: {documentElement: {}, querySelectorAll: () => markers, addEventListener() {}},
    MutationObserver: class { constructor(fn) { mutation = fn; } observe() {} },
    setInterval: fn => timers.push(fn),
  });
  return {
    window, markers, events, storage,
    tick(value) { clock = Date.parse(value); timers.forEach(fn => fn()); },
    mutate: () => mutation(),
    async fetch(rows, market = false, date = "2026-09-02") {
      payload = [{result: {data: {json: market ? {date, marketSignals: rows} : rows}}}];
      const response = await window.fetch("/api/trpc/schedule.intradaySignals" + (market ? "" : "ByTicker"));
      return (await response.json())[0].result.data.json;
    },
  };
}

test("latest session stays Friday through the weekend and Monday 08:44:59, then clears without a fetch", async () => {
  const fri = signal("2026-09-04"), old = signal("2026-09-02");
  const app = browser("2026-09-04T14:00:00+08:00", [], {[tickerKey]: [old, fri]});
  await app.fetch([market], true);
  for (const time of ["2026-09-05T12:00:00+08:00", "2026-09-06T20:00:00+08:00", "2026-09-07T08:44:59+08:00"]) {
    app.tick(time);
    assert.deepEqual(await app.fetch([]), [fri, market]);
  }
  app.markers.push({getAttribute: () => "09/04 10:00", style: {display: ""}});
  app.tick("2026-09-07T08:45:00+08:00");
  assert.equal(app.window.__hanstockSignalDisplayDate, "2026-09-07");
  assert.equal(app.markers[0].style.display, "none");
  assert.equal(app.storage.get(tickerKey), "[]");
  assert.equal(app.storage.get(marketKey), "[]");
  assert.deepEqual(await app.fetch([old, fri]), []);
  assert.deepEqual((await app.fetch([market], true, "2026-09-04")).marketSignals, []);
});

test("a latest trading day with no signals never falls back to older markers", async () => {
  const old = signal("2026-09-02");
  const app = browser("2026-09-05T09:00:00+08:00", [], {[tickerKey]: [old]});
  assert.equal(app.window.__hanstockSignalSessionDate(), "2026-09-04");
  assert.deepEqual(await app.fetch([old]), []);
  assert.equal(app.window.__hanstockIsSignalCandleVisible({date: "09/02 10:00"}), false);
  assert.equal(app.window.__hanstockIsSignalCandleVisible({date: "09/04 10:00"}), true);
});

test("pre-open rows cannot switch the retained day early, and empty refreshes keep that day's signals", async () => {
  const fri = signal("2026-09-04"), mon = signal("2026-09-07");
  const app = browser("2026-09-07T08:30:00+08:00");
  assert.deepEqual(await app.fetch([fri, mon]), [fri]);
  assert.deepEqual(await app.fetch([]), [fri]);
  app.tick("2026-09-07T08:45:00+08:00");
  assert.deepEqual(await app.fetch([fri, mon]), [mon]);
  assert.deepEqual(await app.fetch([]), [mon]);
});

test("exchange holidays preserve the last open session even beyond the old ten-day cache limit", async () => {
  const closed = ["2026-02-12", "2026-02-13", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20"];
  const row = signal("2026-02-11");
  const app = browser("2026-02-22T18:00:00+08:00", closed, {[tickerKey]: [row]});
  assert.deepEqual(await app.fetch([]), [row]);
  assert.equal(app.window.__hanstockSignalSinceTs(), Date.parse("2026-02-11T00:00:00+08:00"));
  for (const time of ["2026-02-20T12:00:00+08:00", "2026-02-23T08:44:59+08:00", "2026-02-23T08:45:00+08:00"]) {
    app.tick(time);
    assert.equal(app.window.__hanstockSignalSessionDate(), resolveIntradaySignalCutoverDate(new Date(time), new Set(closed)));
  }
  assert.deepEqual(await app.fetch([row]), []);
});

test("native markers inserted by a delayed React render are masked against the current session", () => {
  const app = browser("2026-09-07T08:45:00+08:00");
  app.markers.push(...["09/04 13:30", "09/07 09:05"].map(date => ({getAttribute: () => date, style: {display: ""}})));
  app.mutate();
  assert.deepEqual(app.markers.map(marker => marker.style.display), ["none", ""]);
  assert.equal(app.window.__hanstockIsSignalVisible({barTs: null}), false);
  assert.equal(app.window.__hanstockIsSignalCandleVisible({ts: Date.parse("2025-09-07T09:05:00+08:00"), date: "09/07 09:05"}), false);
});
