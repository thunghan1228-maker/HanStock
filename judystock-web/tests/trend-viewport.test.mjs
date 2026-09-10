import test from "node:test";
import assert from "node:assert/strict";
import { clampTrendWindow, zoomTrendWindow, panTrendWindow, resizeTrendWindow, restoreTrendWindow } from "../lib/trend-viewport.ts";
import { buildWatchlistTrend, trendMacd } from "../lib/watchlist-trend.ts";

test("zoom stays anchored at the cursor and small wheel deltas can leave maximum zoom", () => {
  assert.deepEqual(zoomTrendWindow({ start: 0, end: 160 }, 160, .5, 1), { start: 80, end: 160 });
  assert.deepEqual(zoomTrendWindow({ start: 20, end: 120 }, 160, .5, 0), { start: 20, end: 70 });
  assert.deepEqual(zoomTrendWindow({ start: 90, end: 92 }, 160, 1.003), { start: 90, end: 93 });
  assert.deepEqual(zoomTrendWindow({ start: 80, end: 160 }, 160, 10, 1), { start: 0, end: 160 });
});

test("panning preserves the visible span at either history boundary", () => {
  assert.deepEqual(panTrendWindow({ start: 100, end: 150 }, 160, 200), { start: 110, end: 160 });
  assert.deepEqual(panTrendWindow({ start: 100, end: 150 }, 160, -200), { start: 0, end: 50 });
  assert.deepEqual(panTrendWindow({ start: 100, end: 150 }, 160, -50), { start: 50, end: 100 });
});

test("range handles cannot cross or move the opposite edge", () => {
  assert.deepEqual(resizeTrendWindow({ start: 20, end: 80 }, 160, "start", 100), { start: 78, end: 80 });
  assert.deepEqual(resizeTrendWindow({ start: 20, end: 80 }, 160, "end", -10), { start: 20, end: 22 });
  assert.deepEqual(resizeTrendWindow({ start: 20, end: 80 }, 160, "start", -10), { start: 0, end: 80 });
  assert.deepEqual(resizeTrendWindow({ start: 20, end: 80 }, 160, "end", 200), { start: 20, end: 160 });
});

test("range bounds remain valid through empty and sparse trading sessions", () => {
  assert.deepEqual(clampTrendWindow({ start: 1, end: 10 }, 0), { start: 0, end: 0 });
  assert.deepEqual(zoomTrendWindow({ start: 0, end: 1 }, 1, .1), { start: 0, end: 1 });
  for (const length of [1, 2, 3, 54, 160, 801]) {
    for (const factor of [.01, .9, 1, 1.01, 100]) {
      const range = zoomTrendWindow({ start: 0, end: length }, length, factor, .8);
      assert.ok(range.start >= 0 && range.end <= length && range.end - range.start >= Math.min(2, length));
    }
  }
});

test("refresh preserves historical focus while latest-following ranges track appended prices", () => {
  const points = Array.from({ length: 8 }, (_, i) => ({ date: `2026-09-04 09:${String(i * 5).padStart(2, "0")}` }));
  assert.deepEqual(restoreTrendWindow(points, { startDate: points[2].date, count: 3, followLatest: false }), { start: 2, end: 5 });
  assert.deepEqual(restoreTrendWindow(points.slice(2), { startDate: points[2].date, count: 3, followLatest: false }), { start: 0, end: 3 });
  assert.deepEqual(restoreTrendWindow(points, { startDate: points[2].date, count: 3, followLatest: true }), { start: 5, end: 8 });
  assert.deepEqual(restoreTrendWindow(points, null), { start: 0, end: 8 });
});

test("zooming uses existing full-history VWAP and MA values without changing or discarding history", () => {
  const candles = Array.from({ length: 40 }, (_, i) => ({ date: `2026-09-04 ${String(9 + Math.floor(i / 12)).padStart(2, "0")}:${String(i % 12 * 5).padStart(2, "0")}`, close: 100 + i, volume: i + 1 }));
  const all = buildWatchlistTrend(candles, "5m"), original = structuredClone(all);
  const allMacd = trendMacd(all), range = zoomTrendWindow({ start: 0, end: all.length }, all.length, .25, 1);
  const visible = all.slice(range.start, range.end), visibleMacd = allMacd.slice(range.start, range.end);
  assert.equal(visible.length, 10);
  assert.equal(visible[0], all[30]);
  assert.ok(visible[0].ma20 !== null);
  assert.equal(visibleMacd[0], allMacd[30]);
  assert.deepEqual(all, original);
});
