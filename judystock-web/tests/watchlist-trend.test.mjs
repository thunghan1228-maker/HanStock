import test from "node:test";
import assert from "node:assert/strict";
import { buildWatchlistTrend, trendMacd, watchlistQuoteTone, trendPriceSegments, trendPriceTone } from "../lib/watchlist-trend.ts";

const now = Date.parse("2026-09-05T10:00:00+08:00");
const row = (date, close, volume = 1) => ({ date, close, high: close, low: close, volume });

test("intraday views retain three trading sessions through weekends, calculate MAs before trimming, and reset VWAP daily", () => {
  const warmup = Array.from({ length: 20 }, (_, i) => row(`09/01 09:${String(i).padStart(2, "0")}`, 10));
  for (const interval of ["1m", "5m"]) {
    const result = buildWatchlistTrend([...warmup, row("09/02 09:00", 20, 1), row("09/02 09:05", 40, 3), row("09/03 09:00", 60, 2), row("09/04 09:00", 80, 5)], interval, now);
    assert.deepEqual([...new Set(result.map(point => point.session))], ["2026-09-02", "2026-09-03", "2026-09-04"]);
    assert.equal(result[0].ma5, 12);
    assert.equal(result[0].ma20, 10.5);
    assert.equal(result[1].vwap, 35);
    assert.equal(result[2].vwap, 60);
    assert.equal(result[3].vwap, 80);
  }
});

test("missing volume never invents VWAP and year-boundary sessions stay ordered", () => {
  const result = buildWatchlistTrend([row("12/31 09:00", 10, 0), row("01/02 09:00", 20, 0), row("01/02 09:05", 30, 2)], "5m", Date.parse("2026-01-03T10:00:00+08:00"));
  assert.equal(result[0].session, "2025-12-31");
  assert.equal(result[1].session, "2026-01-02");
  assert.equal(result[0].vwap, null);
  assert.equal(result[1].vwap, null);
  assert.equal(result[2].vwap, 30);
});

test("daily trend has historical MA warmup and optional flat-price MACD is zero", () => {
  const rows = Array.from({ length: 150 }, (_, index) => row(new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10), 100, 5));
  const points = buildWatchlistTrend(rows, "1d", now);
  assert.equal(points.length, 120);
  assert.equal(points[0].ma20, 100);
  assert.equal(points.at(-1).vwap, 100);
  assert.ok(trendMacd(points).every(point => point.dif === 0 && point.signal === 0 && point.histogram === 0));
});

test("price, percentage and absolute change share red/white/green including flat zero", () => {
  assert.equal(watchlistQuoteTone(51, "+9.98%"), "positive");
  assert.equal(watchlistQuoteTone(-5, "-1.00%"), "negative");
  assert.equal(watchlistQuoteTone(0, "0.00%"), "neutral");
  assert.equal(watchlistQuoteTone(null, "+0.00%"), "neutral");
  assert.equal(watchlistQuoteTone(null, "—"), "neutral");
});

const pricePoint = (close, vwap, session = "2026-09-04") => ({ date: session, session, close, vwap, ma5: null, ma20: null });

test("trend turns red above VWAP and green below, with exact crossing for a moving VWAP", () => {
  const points = [pricePoint(90, 100), pricePoint(130, 110)];
  const segments = trendPriceSegments(points, "5m");
  assert.deepEqual(segments.map(segment => segment.tone), ["below", "above"]);
  assert.equal(segments[0].to, segments[1].from);
  assert.ok(Math.abs(segments[0].to.index - 1 / 3) < 1e-10);
  assert.ok(Math.abs(segments[0].to.price - 103.33333333333333) < 1e-10);
  assert.equal(trendPriceTone(points[0]), "below");
  assert.equal(trendPriceTone(points[1]), "above");
  assert.deepEqual(trendPriceSegments([...points].reverse(), "1m").map(segment => segment.tone), ["above", "below"]);
});

test("VWAP equality and missing volume do not invent a red or green direction", () => {
  assert.equal(trendPriceTone(pricePoint(100, 100)), "neutral");
  assert.equal(trendPriceTone(pricePoint(100, null)), "neutral");
  assert.equal(trendPriceSegments([pricePoint(100, 100), pricePoint(100, 100)], "5m")[0].tone, "neutral");
  assert.equal(trendPriceSegments([pricePoint(100, 100), pricePoint(90, 100)], "5m")[0].tone, "below");
  assert.equal(trendPriceSegments([pricePoint(100, null), pricePoint(90, 100)], "5m")[0].tone, "neutral");
});

test("VWAP crossings never interpolate across the overnight intraday reset", () => {
  const points = [pricePoint(90, 100, "2026-09-03"), pricePoint(130, 110)];
  assert.deepEqual(trendPriceSegments(points, "5m"), []);
  assert.equal(trendPriceSegments(points, "1d").length, 2);
});
