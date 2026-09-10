import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_BATTLE_SETTINGS } from "../lib/battle-settings.ts";
import {
  annotateMainForceChipRanks,
  buildPreviousSessionChipTopRanks,
  extractMainForceChipRank,
  stripMainForceChipRank,
} from "../lib/main-force-chip-ranks.ts";

const point = (date, value) => ({ date, foreign: value, trust: value, dealer: value, hedge: value });

test("ranks the previous session's full market by today 60 percent plus five-day average 40 percent", () => {
  const snapshot = {
    dataDate: "2026/08/20",
    rows: [
      { code: "2609", name: "陽明", market: "twse", series: [point("2026/08/20", 90), point("2026/08/19", 70), point("2026/08/18", 70), point("2026/08/17", 70), point("2026/08/14", 70)] },
      { code: "2408", name: "南亞科", market: "twse", series: [point("2026/08/20", 80), point("2026/08/19", 80), point("2026/08/18", 80), point("2026/08/17", 80), point("2026/08/14", 80)] },
      { code: "2303", name: "聯電", market: "twse", series: [point("2026/08/20", -90), point("2026/08/19", -70), point("2026/08/18", -70), point("2026/08/17", -70), point("2026/08/14", -70)] },
      { code: "3702", name: "大聯大", market: "twse", series: [point("2026/08/20", -80), point("2026/08/19", -80), point("2026/08/18", -80), point("2026/08/17", -80), point("2026/08/14", -80)] },
    ],
  };
  const rankings = buildPreviousSessionChipTopRanks(snapshot, DEFAULT_BATTLE_SETTINGS.chipWeights, "2026-08-21");
  assert.deepEqual(rankings.increasing.get("2609"), { rank: 1, score: 83.6, dataDate: "2026-08-20", direction: "增加" });
  assert.deepEqual(rankings.increasing.get("2408"), { rank: 2, score: 80, dataDate: "2026-08-20", direction: "增加" });
  assert.deepEqual(rankings.decreasing.get("2303"), { rank: 1, score: -83.6, dataDate: "2026-08-20", direction: "減少" });
  assert.deepEqual(rankings.decreasing.get("3702"), { rank: 2, score: -80, dataDate: "2026-08-20", direction: "減少" });
});

test("maps increasing top 100 to strong bullish and decreasing top 100 to strong bearish signals", () => {
  const rankings = {
    increasing: new Map([["2609", { rank: 7, score: 71.6, dataDate: "2026-08-20", direction: "增加" }]]),
    decreasing: new Map([["2303", { rank: 4, score: -68.2, dataDate: "2026-08-20", direction: "減少" }]]),
  };
  const [bullish, bearish, ordinary, wrongSide] = annotateMainForceChipRanks([
    { tradeDate: "2026-08-21", ticker: "2609", kind: "mainForceStrongBullish", note: "A～D同步濾網 V1｜族群同步 航運 漲幅第 2 名 +2.20%" },
    { tradeDate: "2026-08-21", ticker: "2303", kind: "mainForceStrongBearish", note: "A～D同步濾網 V1｜族群同步 半導體 跌幅第 3 名 -1.20%" },
    { tradeDate: "2026-08-21", ticker: "2609", kind: "unknownSignal", note: "A～D同步濾網 V1" },
    { tradeDate: "2026-08-21", ticker: "2609", kind: "mainForceStrongBearish", note: "A～D同步濾網 V1" },
  ], rankings);
  assert.match(bullish.note, /前日綜合Top100 2026-08-20 增加第 7 名 \+71\.6 分/);
  assert.match(bearish.note, /前日綜合Top100 2026-08-20 減少第 4 名 -68\.2 分/);
  assert.deepEqual(extractMainForceChipRank(bearish.note), { dataDate: "2026-08-20", direction: "減少", rank: 4, score: -68.2 });
  assert.doesNotMatch(ordinary.note, /前日綜合Top100/);
  assert.doesNotMatch(wrongSide.note, /前日綜合Top100/);
  assert.doesNotMatch(stripMainForceChipRank(bullish.note), /前日綜合Top100/);
});

test("upgrades the old directionless badge and removes a stale wrong-side rank", () => {
  const rankings = {
    increasing: new Map(),
    decreasing: new Map([["2303", { rank: 9, score: -55.4, dataDate: "2026-08-20", direction: "減少" }]]),
  };
  const [upgraded, removed] = annotateMainForceChipRanks([
    { tradeDate: "2026-08-21", ticker: "2303", kind: "mainForceStrongBearish", note: "A～D同步濾網 V1｜前日綜合Top100 2026-08-20 第 7 名 +71.6 分" },
    { tradeDate: "2026-08-21", ticker: "2609", kind: "mainForceStrongBearish", note: "A～D同步濾網 V1｜前日綜合Top100 2026-08-20 第 7 名 +71.6 分" },
  ], rankings);
  assert.match(upgraded.note, /減少第 9 名 -55\.4 分/);
  assert.doesNotMatch(removed.note, /前日綜合Top100/);
});

test("adds previous-session increasing and decreasing ranks to today's live signal kinds", () => {
  const rankings = {
    increasing: new Map([["2609", { rank: 12, score: 48.6, dataDate: "2026-08-20", direction: "增加" }]]),
    decreasing: new Map([["2303", { rank: 18, score: -42.3, dataDate: "2026-08-20", direction: "減少" }]]),
  };
  for (const kind of ["daytradeEarlyBuy50", "triangleNearBreakout", "triangleBreakoutPendingVolume", "triangleVolumeBreakout", "fourGateBullish", "mainForceTurnBullish"]) {
    const [signal] = annotateMainForceChipRanks([{ tradeDate: "2026-08-21", ticker: "2609", kind, note: "今日即時" }], rankings);
    assert.match(signal.note, /增加第 12 名 \+48\.6 分/);
  }
  for (const kind of ["daytradeEarlySell50", "fourGateBearish", "mainForceTurnBearish"]) {
    const [signal] = annotateMainForceChipRanks([{ tradeDate: "2026-08-21", ticker: "2303", kind, note: "今日即時" }], rankings);
    assert.match(signal.note, /減少第 18 名 -42\.3 分/);
  }
  const [extraLargeSell] = annotateMainForceChipRanks([{ tradeDate: "2026-08-21", ticker: "2303", kind: "intradayExtraLargeSell", note: "盤中特大賣單" }], rankings);
  assert.match(extraLargeSell.note, /前日綜合Top100 2026-08-20 減少第 18 名 -42\.3 分/);
});
