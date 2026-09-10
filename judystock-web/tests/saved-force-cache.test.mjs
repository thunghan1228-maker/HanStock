import test from "node:test";
import assert from "node:assert/strict";
import { createSavedForceCache } from "../lib/saved-force-cache.ts";

const snapshot = (value, refreshedAt = 0, phase = "old") => ({ value, available: value.length > 0, refreshedAt, phase });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

test("saved history returns before blocked upstream work and all callers share one refresh", async () => {
  const slow = deferred(), background = [], old = snapshot([1, 2]);
  let reads = 0, refreshes = 0;
  const load = createSavedForceCache({ now: () => 100000, empty: () => [], weight: rows => rows.length });
  const request = () => load("3163:5m", { phase: "closed", ttl: 300000 }, async () => { reads++; return old; }, async () => { refreshes++; return slow.promise; }, task => background.push(task));
  const values = await Promise.all([request(), request(), request()]);
  assert.ok(values.every(value => value === old));
  assert.equal(reads, 1); assert.equal(refreshes, 1);
  slow.resolve(snapshot([1, 2, 3], 100000, "closed"));
  await Promise.all(background);
  assert.deepEqual((await request()).value, [1, 2, 3]);
  assert.equal(refreshes, 1);
});

test("fresh durable records survive process restart without requesting either upstream interval", async () => {
  const fresh = snapshot([54], 99000, "closed");
  for (let restart = 0; restart < 2; restart++) {
    const load = createSavedForceCache({ now: () => 100000, empty: () => [], weight: rows => rows.length });
    const result = await load("3163:5m", { phase: "closed", ttl: 300000 }, async () => fresh, () => { throw Error("unneeded upstream call"); }, () => {});
    assert.equal(result, fresh);
  }
});

test("market opening revalidates off-hours snapshots while retaining immediately visible history", async () => {
  const background = [], load = createSavedForceCache({ now: () => 100000, empty: () => [], weight: rows => rows.length });
  const old = snapshot([54], 99000, "closed");
  const result = await load("3163:5m", { phase: "live", ttl: 15000 }, async () => old, async () => snapshot([55], 100000, "live"), task => background.push(task));
  assert.equal(result, old);
  await Promise.all(background);
});

test("cold missing history waits once and never caches a failed refresh as successful", async () => {
  let now = 100000, calls = 0;
  const load = createSavedForceCache({ now: () => now, empty: () => [], weight: rows => rows.length });
  const request = () => load("3163:1m", { phase: "live", ttl: 15000 }, async () => snapshot([]), async () => {
    calls++; if (calls === 1) throw Error("provider unavailable"); return snapshot([1], now, "live");
  }, () => {});
  await assert.rejects(request(), /provider unavailable/);
  now += 10001;
  assert.deepEqual((await request()).value, [1]);
  assert.equal(calls, 2);
});

test("a temporary database and upstream outage cannot erase already cached force", async () => {
  let now = 100000, reads = 0;
  const background = [], load = createSavedForceCache({ now: () => now, empty: () => [], weight: rows => rows.length });
  const request = () => load("3163:5m", { phase: "live", ttl: 15000 }, async () => {
    if (reads++) throw Error("database unavailable"); return snapshot([1, -2], now, "live");
  }, async () => { throw Error("provider unavailable"); }, task => background.push(task));
  await request(); now += 31000;
  assert.deepEqual((await request()).value, [1, -2]);
  await Promise.all(background);
  assert.deepEqual((await request()).value, [1, -2]);
});

test("a sparse durable read retains observations whose database write is still pending", async () => {
  let now=100000;
  const background=[];
  const load=createSavedForceCache({now:()=>now,empty:()=>[],weight:rows=>rows.length,
    merge:(old,next)=>[...new Set([...old,...next])]});
  const request=()=>load('2303:5m',{phase:'closed',ttl:300000},async()=>snapshot([1]),
    async old=>snapshot([...new Set([...old,2])],0,''),task=>background.push(task));
  await request(); await Promise.all(background);
  now+=31000;
  assert.deepEqual((await request()).value,[1,2]);
  await Promise.all(background);
});
