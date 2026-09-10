import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  calculateIntradayLargeForceValue,
  calculateIntradayLargeForceSignals,
  filterAjIntradayLargeForceSignals,
  filterCandidateIntradayLargeForceSignals,
  hasQualifiedInstantLargeTriggerForce,
  qualifyInstantLargeSignalByTriggerForce,
  AJ_CURRENT_STRONG_GROUP_RANK_LIMIT,
  AJ_LARGE_FORCE_MIN_TURNOVER_AMOUNT,
  AJ_LARGE_FORCE_MIN_PCT,
  AJ_LARGE_FORCE_MAX_PCT,
  AJ_RECENT_WEAK_GROUP_RANK_START,
  INTRADAY_LARGE_FORCE_MIN_NET_AMOUNT,
  INTRADAY_LARGE_FORCE_MIN_TURNOVER_AMOUNT,
  INTRADAY_LARGE_FORCE_STRONG_THRESHOLD_PCT,
  INTRADAY_LARGE_FORCE_THRESHOLD_PCT,
} from "../lib/intraday-large-force.ts";

const atTaipei = (hour, minute) => Date.UTC(2026, 8, 3, hour - 8, minute);
const bar = (hour, minute, buyAmount, sellAmount, volume = 1_000, close = 100) => ({
  ts: atTaipei(hour, minute),
  close,
  volume,
  main_buy_amount: buyAmount,
  main_sell_amount: sellAmount,
  main_force_available: true,
});

test("emits a bullish intraday large-force signal only after two consecutive qualified minutes", () => {
  const signals = calculateIntradayLargeForceSignals("2330", "台積電", [
    bar(9, 5, 15_000_000, 0),
    bar(9, 6, 15_000_000, 0),
    bar(9, 7, 15_000_000, 0),
  ]);
  assert.equal(INTRADAY_LARGE_FORCE_THRESHOLD_PCT, 12);
  assert.equal(INTRADAY_LARGE_FORCE_MIN_TURNOVER_AMOUNT, 100_000_000);
  assert.equal(INTRADAY_LARGE_FORCE_MIN_NET_AMOUNT, 30_000_000);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].kind, "intradayLargeForceBuy");
  assert.equal(signals[0].label, "盤中大戶偏買");
  assert.equal(signals[0].barTs, atTaipei(9, 7));
  assert.match(signals[0].note, /盤中大戶力 \+15\.0%/);
  assert.match(signals[0].note, /大戶淨額 \+4,500 萬/);
  assert.match(signals[0].note, /成交額 3\.00 億/);
  assert.match(signals[0].note, /連續 2 根 1 分 K 確認/);
});

test("mirrors the calculation for strong selling and keeps one signal per direction", () => {
  const signals = calculateIntradayLargeForceSignals("2303", "聯電", [
    bar(9, 5, 0, 40_000_000),
    bar(9, 6, 0, 40_000_000),
    bar(9, 7, 0, 40_000_000),
    bar(9, 8, 0, 40_000_000),
  ]);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].kind, "intradayLargeForceSell");
  assert.equal(signals[0].label, "盤中大戶強力賣出");
  assert.equal(signals[0].barTs, atTaipei(9, 6));
  assert.match(signals[0].note, /盤中大戶力 -40\.0%/);
  assert.match(signals[0].note, /大戶淨額 -8,000 萬/);
});

test("calculates the latest intraday large-force value without applying the signal threshold", () => {
  const value = calculateIntradayLargeForceValue([
    bar(9, 5, 3_000_000, 1_000_000),
    bar(9, 6, 4_000_000, 1_000_000),
  ], "2026-09-03");
  assert.equal(value?.tradeDate, "2026-09-03");
  assert.equal(value?.barTs, atTaipei(9, 6));
  assert.equal(value?.forcePct, 2.5);
  assert.equal(value?.price, 100);
  assert.equal(value?.buyAmount, 7_000_000);
  assert.equal(value?.sellAmount, 2_000_000);
  assert.equal(value?.netAmount, 5_000_000);
  assert.equal(value?.turnoverAmount, 200_000_000);
  assert.equal(INTRADAY_LARGE_FORCE_STRONG_THRESHOLD_PCT, 28);
});

test("treats zero/zero classification placeholders as unavailable instead of 0% force", () => {
  assert.equal(calculateIntradayLargeForceValue([
    bar(9, 5, 0, 0),
    bar(9, 6, 0, 0),
  ], "2026-09-03"), null);
});

test("instant large orders require the matching trigger-minute force and keep that historical value", () => {
  const bars = [
    bar(9, 5, 7_000_000, 1_000_000),
    bar(9, 6, 5_000_000, 1_000_000),
    bar(9, 7, 0, 30_000_000),
  ];
  const triggerValue = calculateIntradayLargeForceValue(bars, "2026-09-03", atTaipei(9, 6));
  const laterValue = calculateIntradayLargeForceValue(bars, "2026-09-03");
  assert.equal(triggerValue?.forcePct, 5);
  assert.equal(laterValue?.forcePct, -20 / 3);
  const buy = { tradeDate: "2026-09-03", ticker: "2330", kind: "instantLargeBuy", barTs: atTaipei(9, 6), note: "同秒 2 筆｜合計 100 張｜約 3,000 萬" };
  const kept = qualifyInstantLargeSignalByTriggerForce(buy, triggerValue?.forcePct ?? null);
  assert.match(kept?.note ?? "", /觸發當時盤中大戶力 \+5\.0%/);
  assert.equal(hasQualifiedInstantLargeTriggerForce(kept), true);
  assert.equal(qualifyInstantLargeSignalByTriggerForce(buy, laterValue?.forcePct ?? null), null);
  const sell = { ...buy, kind: "instantLargeSell" };
  assert.equal(qualifyInstantLargeSignalByTriggerForce(sell, triggerValue?.forcePct ?? null), null);
  assert.equal(hasQualifiedInstantLargeTriggerForce({ ...sell, note: `${sell.note}｜觸發當時盤中大戶力 -3.3%` }), true);
});

test("rejects opening noise, insufficient amounts, and a single threshold touch", () => {
  assert.deepEqual(calculateIntradayLargeForceSignals("1101", "台泥", [
    bar(9, 3, 100_000_000, 0),
    bar(9, 4, 100_000_000, 0),
  ]), []);
  assert.deepEqual(calculateIntradayLargeForceSignals("1101", "台泥", [
    bar(9, 5, 10_000_000, 0),
    bar(9, 6, 10_000_000, 0),
  ]), []);
  assert.deepEqual(calculateIntradayLargeForceSignals("1101", "台泥", [
    bar(9, 5, 40_000_000, 0),
    bar(9, 6, 0, 40_000_000),
  ]), []);
});

test("keeps only large-force stocks already present in the intraday candidate pool", () => {
  const largeForce = [
    { tradeDate: "2026-09-03", ticker: "2330", kind: "intradayLargeForceSell", barTs: 300 },
    { tradeDate: "2026-09-03", ticker: "2330", kind: "intradayLargeForceSell", barTs: 200 },
    { tradeDate: "2026-09-03", ticker: "2330", kind: "intradayLargeForceBuy", barTs: 400 },
    { tradeDate: "2026-09-03", ticker: "2317", kind: "intradayLargeForceSell", barTs: 100 },
  ];
  const candidates = [
    { tradeDate: "2026-09-03", ticker: "2330", kind: "mainForceBuy", barTs: 50 },
    { tradeDate: "2026-09-03", ticker: "2330", kind: "instantLargeBuy", barTs: 60 },
    { tradeDate: "2026-09-03", ticker: "2317", kind: "intradayLargeForceSell", barTs: 70 },
  ];
  assert.deepEqual(filterCandidateIntradayLargeForceSignals(largeForce, candidates), [
    largeForce[1],
    largeForce[2],
  ]);
});

test("applies all four AJ filters to both bullish and bearish rows", () => {
  const signals = [
    { tradeDate: "2026-09-03", ticker: "2330", kind: "intradayLargeForceBuy", barTs: 100, note: "盤中大戶力 +15.0%｜成交額 3.50 億" },
    { tradeDate: "2026-09-03", ticker: "3008", kind: "intradayLargeForceBuy", barTs: 150, note: "盤中大戶力 +20.0%｜成交額 2.99 億" },
    { tradeDate: "2026-09-03", ticker: "2317", kind: "intradayLargeForceSell", barTs: 200, note: "盤中大戶力 -18.0%｜成交額 3.20 億" },
    { tradeDate: "2026-09-03", ticker: "2303", kind: "intradayLargeForceSell", barTs: 300, note: "盤中大戶力 -20.0%｜成交額 2.99 億" },
    { tradeDate: "2026-09-03", ticker: "2454", kind: "intradayLargeForceSell", barTs: 400, note: "盤中大戶力 -22.0%｜成交額 4.00 億" },
  ];
  const transitions = {
    "2330": { ticker: "2330", qualifyingGroup: null, currentRank: null, bestRecentRank: null, recentRanks: [], bullishQualifyingGroup: "AI伺服器", bullishCurrentRank: 7, worstRecentRank: 61, bullishRecentRanks: [61, 40, 25] },
    "3008": { ticker: "3008", qualifyingGroup: null, currentRank: null, bestRecentRank: null, recentRanks: [], bullishQualifyingGroup: "光學鏡頭", bullishCurrentRank: 11, worstRecentRank: 55, bullishRecentRanks: [55, 44, 30] },
    "2317": { ticker: "2317", qualifyingGroup: "AI伺服器", currentRank: 42, bestRecentRank: 8, recentRanks: [8, 15, 25], bullishQualifyingGroup: null, bullishCurrentRank: null, worstRecentRank: null, bullishRecentRanks: [] },
    "2303": { ticker: "2303", qualifyingGroup: "晶圓代工", currentRank: 31, bestRecentRank: 12, recentRanks: [12, 18, 24], bullishQualifyingGroup: null, bullishCurrentRank: null, worstRecentRank: null, bullishRecentRanks: [] },
    "2454": { ticker: "2454", qualifyingGroup: null, currentRank: null, bestRecentRank: null, recentRanks: [], bullishQualifyingGroup: null, bullishCurrentRank: null, worstRecentRank: null, bullishRecentRanks: [] },
  };
  assert.equal(AJ_LARGE_FORCE_MIN_PCT, 10);
  assert.equal(AJ_LARGE_FORCE_MAX_PCT, -10);
  assert.equal(AJ_LARGE_FORCE_MIN_TURNOVER_AMOUNT, 300_000_000);
  assert.equal(AJ_RECENT_WEAK_GROUP_RANK_START, 48);
  assert.equal(AJ_CURRENT_STRONG_GROUP_RANK_LIMIT, 20);
  assert.deepEqual(filterAjIntradayLargeForceSignals(signals, transitions), [signals[0], signals[2]]);
});

test("persists live large-force rankings without duplicate collectors and continues after Worker restarts", async () => {
  const [route, progressStore, page, homeworkHook] = await Promise.all([
    readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../db/intraday-large-force-scan.ts", import.meta.url), "utf8"),
    readEarlySellSources(),
    readFile(new URL("../app/useIntradayForceHomework.ts", import.meta.url), "utf8"),
  ]);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("collect"\) === "large-force"/);
  assert.doesNotMatch(route, /large-force-active-/);
  assert.match(route, /\/api\/hub\/bars1m\/batch/);
  assert.match(route, /largeForceMarketStocks\.slice\(progress\.nextIndex/);
  assert.match(route, /saveIntradayLargeForceMonitorRows\(\{ tradeDate, batchStart: progress.nextIndex/);
  assert.match(route, /saveEarlySellSignals\(monitor\.signals\)/);
  assert.match(route, /calculateFiveMinutePatternSignals/);
  assert.match(progressStore, /intraday-large-force-scan:\$\{tradeDate\}/);
  assert.match(progressStore, /json_extract\(river_radar_config\.config_json, '\$\.status'\) <> 'running'/);
  assert.match(progressStore, /const nextIndex = Math\.min/);
  assert.match(page, /daytrade-early-sell\?collect=large-force/);
  assert.match(page, /isLargeForceBackfillWindow/);
  assert.match(homeworkHook, /scope: "market"/);
  assert.doesNotMatch(homeworkHook, /daytrade-early-sell\?collect=large-force/);
});

test("colors the displayed intraday large-force value red for bullish and green for bearish", async () => {
  const [page, styles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(page, /note\.match\(\/盤中大戶力\\s\+\[\+-\]\?\\d\+/);
  assert.match(page, /signal\.kind === "intradayLargeForceBuy" \? "is-bullish" : "is-bearish"/);
  assert.match(page, /const forcePct = Number\.isFinite\(noteValue\)\s*\? noteValue/);
  assert.match(page, /intraday-large-force-value \$\{direction\}/);
  assert.match(styles, /\.intraday-large-force-value\{[^}]*color:#fff!important/);
  assert.match(styles, /\.intraday-large-force-value\.is-bullish\{[^}]*background:#c72a33/);
  assert.match(styles, /\.intraday-large-force-value\.is-bearish\{[^}]*background:#147246/);
});

test("adds a batched intraday large-force badge to every signal row and popup", async () => {
  const [page, route, styles] = await Promise.all([
    readEarlySellSources(),
    readFile(new URL("../app/api/intraday-large-force-values/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.equal((page.match(/<SignalIntradayLargeForceBadge signal=\{item\}/g) ?? []).length, 3);
  assert.match(page, /intraday-large-force-values\?tradeDate=/);
  assert.match(page, /tickers\.slice\(index \* 200, index \* 200 \+ 200\)/);
  assert.match(route, /MAX_TICKERS = 200/);
  assert.match(route, /\/api\/hub\/bars1m\/batch/);
  assert.match(route, /readIntradayLargeForceMonitorRows\(tradeDate\)/);
  assert.match(route, /\/api\/hub\/bars1m\/\$\{encodeURIComponent\(ticker\)\}/);
  assert.match(route, /row\?\.forcePct !== null/);
  assert.match(route, /calculateIntradayLargeForceValue/);
  assert.match(styles, /intraday-signal-large-force\.is-bullish\{[^}]*background:#c62b34/);
  assert.match(styles, /intraday-signal-large-force\.is-bearish\{[^}]*background:#147246/);
  assert.match(page, /EARLY_SELL_LARGE_FORCE_VALUES_KEY/);
  assert.match(page, /setSignalLargeForceValues\(restored\)/);
  assert.match(page, /if \(inFlight\) return;/);
  assert.match(page, /localStorage\.setItem\(EARLY_SELL_LARGE_FORCE_VALUES_KEY/);
  assert.match(styles, /html\[data-hanstock-device="ipad"\] \.early-signal-stock-meta i\.intraday-signal-large-force \{[^}]*display: inline-flex!important[^}]*visibility: visible!important[^}]*opacity: 1!important/);
});

test("keeps the current AJ large-force snapshot visible while iPad refreshes or retries", async () => {
  const page = readEarlySellSources();
  assert.match(page, /const hasSameDateSnapshot = largeForceAjTradeDateRef\.current === tradeDate/);
  assert.match(page, /active && !hasSameDateSnapshot\) setLargeForceAjStatus\("loading"\)/);
  assert.match(page, /const keepExistingSnapshot = largeForceAjTradeDateRef\.current === tradeDate/);
  assert.match(page, /keepExistingSnapshot \? \{ \.\.\.current, \.\.\.nextRows \} : nextRows/);
  assert.match(page, /if \(largeForceAjTradeDateRef\.current === tradeDate\) setLargeForceAjStatus\("ready"\)/);
  assert.match(page, /touchDevice === "ipad" \|\| touchDevice === "iphone"/);
});

test("builds a staged large-force table from the existing intraday candidate pool", async () => {
  const [page, calculator, route, styles] = await Promise.all([
    readEarlySellSources(),
    readFile(new URL("../lib/intraday-large-force.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/intraday-large-force-aj/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(calculator, /filterCandidateIntradayLargeForceSignals/);
  assert.match(calculator, /filterAjIntradayLargeForceSignals/);
  assert.match(page, /filterCandidateIntradayLargeForceSignals\(rawLargeForce/);
  assert.match(page, /盤中大戶力多空達標條件/);
  assert.match(page, /多方大戶力達標/);
  assert.match(page, /空方大戶力達標/);
  assert.doesNotMatch(page, /AJ 多空|AJ 多方|AJ 空方|AJ 族群|套用 AJ/);
  assert.match(page, /largeForceStageGroups\.map/);
  assert.match(page, /階段 \{stage\.code\}/);
  assert.match(page, /\{ code: "盤中", label: "持續追蹤", time: "10:00–13:30" \}[\s\S]*\{ code: "C"[\s\S]*\{ code: "B"[\s\S]*\{ code: "A"/);
  assert.match(page, /right\.barTs - left\.barTs \|\| left\.ticker\.localeCompare\(right\.ticker\)/);
  assert.match(page, /成交額 ≥ 3 億/);
  assert.match(route, /loadPreviousSessions/);
  assert.match(route, /AJ_CURRENT_WEAK_GROUP_RANK_START/);
  assert.match(route, /AJ_RECENT_STRONG_GROUP_RANK_LIMIT/);
  assert.match(route, /AJ_CURRENT_STRONG_GROUP_RANK_LIMIT/);
  assert.match(route, /AJ_RECENT_WEAK_GROUP_RANK_START/);
  assert.match(styles, /\.large-force-monitor-stage/);
  assert.match(styles, /\.large-force-monitor-row/);
  assert.match(styles, /\.aj-large-force-transition/);
});
