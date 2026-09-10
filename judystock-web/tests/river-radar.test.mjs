import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { calculateRiverScore, riverStatus, RIVER_MA_PERIODS } from "../lib/river-radar.ts";

const pagePath = new URL("../app/stock-screener/page.tsx", import.meta.url);
const panelPath = new URL("../app/stock-screener/RiverRadarPanel.tsx", import.meta.url);
const routePath = new URL("../app/api/river-radar/daily/route.ts", import.meta.url);
const migrationPath = new URL("../drizzle/0014_moaning_tattoo.sql", import.meta.url);
const workerPath = new URL("../worker/index.ts", import.meta.url);

test("river engine uses the production six-MA model and stable 0-100 classifications", () => {
  assert.deepEqual(RIVER_MA_PERIODS, [5, 10, 20, 60, 120, 240]);
  const bullish = calculateRiverScore({
    close: 115,
    maValues: { 5: 110, 10: 108, 20: 105, 60: 102, 120: 98, 240: 95 },
    previousMaValues: { 5: 109, 10: 107, 20: 104, 60: 101, 120: 97, 240: 94 },
    volumeRatio: 1.3,
    vwap: 112,
  });
  const bearish = calculateRiverScore({
    close: 85,
    maValues: { 5: 90, 10: 92, 20: 95, 60: 98, 120: 102, 240: 105 },
    previousMaValues: { 5: 91, 10: 93, 20: 96, 60: 99, 120: 103, 240: 106 },
    volumeRatio: 1.3,
    vwap: 88,
  });
  assert.ok(bullish && bullish.score >= 75);
  assert.equal(bullish?.status, "強多");
  assert.ok(bearish && bearish.score < 25);
  assert.equal(bearish?.status, "強空");
  assert.equal(riverStatus(60).status, "偏多");
  assert.equal(riverStatus(40).status, "中性");
  assert.equal(riverStatus(25).status, "偏空");
});

test("missing volume and VWAP are disclosed instead of fabricated as complete confirmation", () => {
  const result = calculateRiverScore({ close: 100, maValues: { 5: 99, 10: 98, 20: 97, 60: 96, 120: 95, 240: 94 } });
  assert.equal(result?.components.volumeVwap, 5);
  assert.equal(result?.confirmationState, "量能待補");
});

test("river radar is wired into strategy screener, API, snapshots and five-minute K navigation", async () => {
  const [page, panel, route, migration, worker] = await Promise.all([
    readFile(pagePath, "utf8"), readFile(panelPath, "utf8"), readFile(routePath, "utf8"), readFile(migrationPath, "utf8"), readFile(workerPath, "utf8"),
  ]);
  assert.match(page, /均線多空趨勢雷達/);
  assert.match(page, /<RiverRadarPanel rows=\{allRows\}/);
  assert.match(panel, /盤中均線/);
  assert.match(panel, /盤後均線/);
  assert.match(panel, /新進榜/);
  assert.match(panel, /連續多／空/);
  assert.match(panel, /退場名單/);
  assert.match(panel, /今日首次進榜/);
  assert.match(panel, /離開原方向/);
  assert.match(panel, /模式結果/);
  assert.match(panel, /countForTab/);
  assert.match(panel, /modeCounts/);
  assert.match(panel, /把大名單再縮成可追蹤清單/);
  assert.match(panel, /80 強度＋12\/15＋2 日＋3%/);
  assert.match(panel, /85 強度＋15\/15＋3 日＋5%/);
  assert.match(panel, /當日動能/);
  assert.match(panel, /收盤同方向/);
  assert.match(panel, /強勢同向 ≥2%/);
  assert.match(panel, /六條均線兩兩比較，共 15 組/);
  assert.match(panel, /均線趨勢分＝/);
  assert.match(panel, /（MA5－MA240）÷ MA240/);
  assert.match(panel, /type RadarSortKey = "changePct" \| "riverScore" \| "riverOpeningPct" \| "riverTrendDays"/);
  assert.match(panel, /toggleSort\("changePct"\)/);
  assert.match(panel, /toggleSort\("riverScore"\)/);
  assert.match(panel, /toggleSort\("riverOpeningPct"\)/);
  assert.match(panel, /toggleSort\("riverTrendDays"\)/);
  assert.match(panel, /sortDirection === "desc" \? Number\(right\) - Number\(left\) : Number\(left\) - Number\(right\)/);
  assert.match(panel, /盤中量能與 VWAP 每 15 秒即時補算/);
  assert.match(panel, /量能已補/);
  assert.match(panel, /instrumentType === "stock"/);
  assert.match(panel, /url\.searchParams\.set\("interval", "5m"\)/);
  assert.match(route, /saveRiverRadarDaily/);
  assert.match(route, /readLatestRiverRadarDaily/);
  assert.match(route, /const stored = await readLatestRiverRadarDaily/);
  assert.match(route, /if \(refresh \|\| \(stored\?\.rows\.length \?\? 0\) <= 1_000\)/);
  assert.match(route, /snapshotCacheHit/);
  assert.match(route, /stale-while-revalidate=900/);
  assert.match(route, /searchParams\.set\("readOnly", "1"\)/);
  assert.match(route, /historyRequiredDays: 240/);
  assert.match(route, /view === "exit"/);
  assert.match(route, /previousSide\(row\) === side/);
  assert.match(route, /totalMatches: filtered\.length/);
  assert.match(route, /refineScore/);
  assert.match(route, /matchesMaLevel/);
  assert.match(route, /minTrendDays/);
  assert.match(route, /matchesOpening/);
  assert.match(route, /matchesDayMomentum/);
  assert.match(route, /row\.changePct <= -2/);
  assert.match(route, /view !== "new" && row\.riverTrendDays < minTrendDays/);
  assert.match(route, /modeCounts/);
  assert.match(route, /view === "continuous" && b\.riverTrendDays !== a\.riverTrendDays/);
  assert.match(panel, /stockIndicatorBackfill=1&indicatorMarket=/);
  assert.match(panel, /六均線逐檔更新中/);
  assert.match(panel, /contentType\.includes\("application\/json"\)/);
  assert.match(panel, /fetchRiverJson<RadarPayload>/);
  assert.match(panel, /河流資料更新中，系統會自動重試/);
  assert.doesNotMatch(panel, /setError\(reason instanceof Error \? reason\.message/);
  assert.match(migration, /CREATE TABLE `river_radar_daily`/);
  assert.match(migration, /CREATE TABLE `river_radar_config`/);
  assert.match(worker, /new URL\("\/api\/river-radar\/daily"/);
  assert.match(worker, /riverUrl\.searchParams\.set\("refresh", "1"\)/);
  assert.match(panel, /url\.startsWith\("\/api\/river-radar\/daily"\) \? "default" : "no-store"/);
});
