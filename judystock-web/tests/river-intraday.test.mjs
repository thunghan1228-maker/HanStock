import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { aggregateRiverFiveMinuteBars, confirmedRiverDirection, firstRiverSelectionTimestamp, firstRiverSelectionWithMa20Filters, latestRiverSelectionWithMa20Filters, scoreRiverFiveMinuteBars } from "../lib/river-intraday.ts";

test("aggregates one-minute rows into completed five-minute river bars", () => {
  const start = Date.UTC(2026, 7, 28, 1, 0);
  const rows = Array.from({ length: 7 }, (_, index) => ({ ts: start + index * 60_000, close: 100 + index, high: 101 + index, low: 99 + index, volume: 100 }));
  const bars = aggregateRiverFiveMinuteBars(rows);
  assert.equal(bars.length, 2);
  assert.equal(bars[0].close, 104);
  assert.equal(bars[0].volume, 500);
  assert.equal(bars[1].close, 106);
  assert.equal(bars[1].volume, 200);
});

test("requires two consecutive five-minute scores before emitting a river direction", () => {
  assert.equal(confirmedRiverDirection([{ score: 80 }, { score: 76 }]), "bull");
  assert.equal(confirmedRiverDirection([{ score: 20 }, { score: 24 }]), "bear");
  assert.equal(confirmedRiverDirection([{ score: 80 }, { score: 60 }]), null);
});

test("updates daily six-MA bases with intraday five-minute prices", () => {
  const start = Date.UTC(2026, 7, 28, 1, 0);
  const bars = [{ ts: start, close: 109, high: 110, low: 108, volume: 1000 }, { ts: start + 5 * 60_000, close: 110, high: 111, low: 109, volume: 1400 }];
  const baseSums = Object.fromEntries([[5, 100], [10, 98], [20, 95], [60, 90], [120, 85], [240, 80]].map(([period, value]) => [period, (period - 1) * value]));
  const scored = scoreRiverFiveMinuteBars(bars, baseSums);
  assert.equal(scored.length, 2);
  assert.ok(scored[1].score && scored[1].score.score >= 75);
  assert.equal(confirmedRiverDirection(scored.map((row) => row.score)), "bull");
});

test("uses the first intraday five-minute bar that reaches the selected MA strength", () => {
  const start = Date.UTC(2026, 7, 31, 1, 0);
  const scored = [40, 52, 68, 61].map((value, index) => ({ ts: start + index * 5 * 60_000, score: { score: value } }));
  assert.equal(firstRiverSelectionTimestamp(scored, "bull", 60), start + 10 * 60_000);
  assert.equal(firstRiverSelectionTimestamp(scored, "bear", 45), start);
});

test("requires the signal candle to be on the correct side of both daily and five-minute MA20", () => {
  const start = Date.UTC(2026, 7, 31, 1, 0);
  const bullRows = [
    { ts: start, close: 99, score: { score: 80 } },
    { ts: start + 5 * 60_000, close: 101, score: { score: 80 } },
  ];
  const bearRows = [{ ts: start, close: 99, score: { score: 20 } }];
  assert.equal(firstRiverSelectionWithMa20Filters(bullRows, "bull", 80, Array(19).fill(100), 19 * 100)?.ts, start + 5 * 60_000);
  assert.equal(firstRiverSelectionWithMa20Filters(bearRows, "bear", 20, Array(19).fill(100), 19 * 100)?.ts, start);
  assert.equal(firstRiverSelectionWithMa20Filters(bullRows.slice(0, 1), "bull", 80, Array(19).fill(100), 19 * 100), null);
  assert.equal(firstRiverSelectionWithMa20Filters(bullRows, "bull", 80, Array(19).fill(100), 19 * 110), null);
  assert.equal(firstRiverSelectionWithMa20Filters(bullRows, "bull", 80, Array(18).fill(100), 19 * 100), null);
});

test("live ranked-group signals use the newest qualifying five-minute candle instead of backdating", () => {
  const start = Date.UTC(2026, 8, 2, 1, 0);
  const rows = [
    { ts: start, close: 101, score: { score: 80 } },
    { ts: start + 5 * 60_000, close: 102, score: { score: 80 } },
    { ts: start + 70 * 60_000, close: 104, score: { score: 80 } },
  ];
  assert.equal(latestRiverSelectionWithMa20Filters(rows, "bull", 80, Array(19).fill(100), 19 * 100)?.ts, start + 70 * 60_000);
  assert.equal(latestRiverSelectionWithMa20Filters([...rows, { ts: start + 75 * 60_000, close: 99, score: { score: 60 } }], "bull", 80, Array(19).fill(100), 19 * 100), null);
});

test("connects candidate batches and permanent signals to the separate stock screener", async () => {
  const [route, panel, storage, worker, migration, strategyMigration] = await Promise.all([
    readFile(new URL("../app/api/river-radar/intraday/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/stock-screener/RiverRadarPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../db/river-radar-intraday.ts", import.meta.url), "utf8"),
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0015_green_morg.sql", import.meta.url), "utf8"),
    readFile(new URL("../drizzle/0017_parched_sentry.sql", import.meta.url), "utf8"),
  ]);
  assert.match(route, /\/api\/hub\/bars1m\/batch/);
  assert.match(route, /HanStock-River-Radar-Fallback\/1\.0/);
  assert.match(route, /if \(!merged\.has\(code\) && Array\.isArray\(rows\) && rows\.length > 0\)/);
  assert.match(route, /const CANDLE_BATCH_SIZE = 8/);
  assert.match(route, /slice\(0, 100\)/);
  assert.match(route, /intradayEligibility/);
  assert.match(route, /loadTwseClosedTradingDates/);
  assert.match(route, /resolveIntradaySignalCutoverDate/);
  assert.match(route, /saveRiverIntraday/);
  assert.match(route, /searchParams\.get\("fast"\) === "1"/);
  assert.match(route, /readRiverIntradayScanState/);
  assert.match(route, /scanState\?\.completedAt \? new Date\(scanState\.completedAt\)\.toISOString\(\) : null/);
  assert.match(route, /readOnly: true/);
  assert.match(route, /ok: true/);
  assert.match(route, /readRiverStrategySignals\(signalDate\)/);
  assert.match(route, /accumulatedOfficialRiverSignals/);
  assert.match(route, /focus-ranking-67-ma-top3-daily-and-5m-ma20-v7/);
  assert.doesNotMatch(route, /Number\(signal\.updatedAt\) === latestUpdatedAt/);
  assert.match(panel, /盤中均線趨勢訊號/);
  assert.match(panel, /collect \? "\/api\/river-radar\/intraday" : "\/api\/river-radar\/intraday\?fast=1"/);
  assert.match(panel, /setInterval\(\(\) => void load\(controller\.signal\), 10_000\)/);
  assert.match(panel, /setInterval\(\(\) => void load\(controller\.signal, false, true\), 5_000\)/);
  assert.match(route, /selectRankedRiverGroupCandidates/);
  assert.match(route, /getFocusRanking/);
  assert.match(route, /selectionSource === "focus-ranking-67"/);
  assert.match(route, /matchesPersistedOfficialPrimaryGroup\(officialPrimaryGroup, signal\.groupName\)/);
  assert.match(route, /river-groups-incomplete/);
  assert.match(route, /focus-ranking-67-primary-group-change-top6-daily-and-5m-ma20-live-v10/);
  assert.match(route, /OFFICIAL_PRIMARY_GROUP_BY_CODE/);
  assert.match(route, /latestRiverSelectionWithMa20Filters/);
  assert.match(route, /interval: "5m"/);
  assert.match(route, /當日日 K 在日線 20MA 之上/);
  assert.match(route, /訊號當下 5 分 K 收盤價在 5 分 20MA 之上/);
  assert.match(route, /若缺少前一交易日 19 根五分 K/);
  assert.doesNotMatch(route, /Date\.parse\(`\$\{selectedTradeDate[\s\S]*?T09:00:00/);
  assert.doesNotMatch(route, /bullOverSevenCount/);
  assert.match(route, /selectedBullCandidates/);
  assert.match(route, /selectedBearCandidates/);
  assert.doesNotMatch(route, /riverSignalTimestamp/);
  assert.doesNotMatch(route, /tradeDate: selectedSignalDate\(\), barTs: Date\.now\(\)/);
  assert.match(panel, /url\.searchParams\.set\("interval", "5m"\)/);
  assert.doesNotMatch(worker, /riverIntradayLastRefreshAt/);
  assert.doesNotMatch(worker, /river-intraday-background-refresh-failed/);
  assert.match(worker, /daytradeSignalsLastRefreshAt/);
  assert.doesNotMatch(worker, /riverIntradayUrl\.searchParams\.set\("refresh", "1"\)/);
  assert.match(storage, /SELECT bar_ts AS barTs, payload_json AS payloadJson, updated_at AS updatedAt FROM river_radar_signals/);
  assert.match(storage, /ORDER BY updated_at DESC, bar_ts DESC/);
  assert.match(storage, /json_extract\(river_radar_signals\.payload_json, '\$\.groupName'\)/);
  assert.match(storage, /selectionRuleVersion/);
  assert.match(storage, /ELSE excluded\.bar_ts END/);
  assert.match(storage, /acquireRiverIntradayScanLease/);
  assert.match(storage, /json_extract\(river_radar_config\.config_json, '\$\.status'\) <> 'running'/);
  assert.match(storage, /runningLeaseMs = 120_000/);
  assert.match(storage, /SELECT bar_ts AS barTs, payload_json AS payloadJson FROM river_radar_strategy_signals/);
  assert.match(storage, /selectionRuleVersion/);
  assert.match(storage, /\{ \.\.\.parsed, barTs: Number\(item\.barTs\), updatedAt: Number\(item\.updatedAt\) \}/);
  assert.match(migration, /CREATE TABLE `river_radar_intraday`/);
  assert.match(migration, /CREATE TABLE `river_radar_signals`/);
  assert.match(strategyMigration, /CREATE TABLE `river_radar_strategy_signals`/);
});
