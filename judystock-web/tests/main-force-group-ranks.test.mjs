import assert from "node:assert/strict";
import test from "node:test";
import {
  annotateMainForceGroupRanks,
  extractMainForceGroupRank,
  findMainForceGroupRank,
  findWeakestGroupRank,
  hasCapturedOrLegacyInstantLargeGroup,
  hasCapturedMatchingTopGroup,
  selectRiverBearSignalsWithFallback,
  stripMainForceGroupRank,
} from "../lib/main-force-group-ranks.ts";

const rankings = {
  strong: [
    { rank: 1, name: "記憶體", change: "+4.82%" },
    { rank: 2, name: "光通訊", change: "+3.67%" },
    { rank: 11, name: "網通", change: "+1.55%" },
  ],
  weak: [
    { rank: 1, name: "航運", change: "-3.24%" },
    { rank: 2, name: "營建", change: "-2.83%" },
    { rank: 11, name: "百貨", change: "-1.06%" },
  ],
};

test("matches strong-bullish signals only against the gain top ten", () => {
  assert.deepEqual(findMainForceGroupRank(
    { kind: "mainForceStrongBullish" },
    ["網通", "記憶體"],
    rankings,
  ), { group: "記憶體", rank: 1, direction: "漲幅", change: "+4.82%" });
  assert.equal(findMainForceGroupRank({ kind: "mainForceStrongBullish" }, ["航運"], rankings), null);
  assert.equal(findMainForceGroupRank({ kind: "mainForceStrongBullish" }, ["網通"], rankings), null);
});

test("matches strong-bearish signals only against the loss top ten", () => {
  assert.deepEqual(findMainForceGroupRank(
    { kind: "mainForceStrongBearish" },
    ["航運"],
    rankings,
  ), { group: "航運", rank: 1, direction: "跌幅", change: "-3.24%" });
  assert.equal(findMainForceGroupRank({ kind: "mainForceStrongBearish" }, ["記憶體"], rankings), null);
  assert.equal(findMainForceGroupRank({ kind: "mainForceStrongBearish" }, ["百貨"], rankings), null);
});

test("applies the same group top-ten and bottom-ten rules to today's live signal kinds", () => {
  for (const kind of ["daytradeEarlyBuy50", "triangleNearBreakout", "triangleBreakoutPendingVolume", "triangleVolumeBreakout", "fourGateBullish", "mainForceTurnBullish", "riverBull"]) {
    assert.equal(findMainForceGroupRank({ kind }, ["記憶體"], rankings)?.direction, "漲幅");
    assert.equal(findMainForceGroupRank({ kind }, ["航運"], rankings), null);
  }
  for (const kind of ["daytradeEarlySell50", "fourGateBearish", "mainForceTurnBearish", "riverBear"]) {
    assert.equal(findMainForceGroupRank({ kind }, ["航運"], rankings)?.direction, "跌幅");
    assert.equal(findMainForceGroupRank({ kind }, ["記憶體"], rankings), null);
  }
});

test("keeps extra-large buys with gain top ten and sells with loss top ten only", () => {
  assert.equal(findMainForceGroupRank(
    { kind: "intradayExtraLargeSell" },
    ["記憶體"],
    rankings,
  ), null);
  assert.deepEqual(findMainForceGroupRank(
    { kind: "intradayExtraLargeSell" },
    ["航運"],
    rankings,
  ), { group: "航運", rank: 1, direction: "跌幅", change: "-3.24%" });
  assert.equal(findMainForceGroupRank({ kind: "intradayExtraLargeSell" }, ["網通", "百貨"], rankings), null);
  assert.deepEqual(findMainForceGroupRank(
    { kind: "intradayExtraLargeBuy" },
    ["記憶體"],
    rankings,
  ), { group: "記憶體", rank: 1, direction: "漲幅", change: "+4.82%" });
  assert.equal(findMainForceGroupRank({ kind: "intradayExtraLargeBuy" }, ["航運"], rankings), null);
});

test("uses gain and loss top twenty only for instant same-second large orders", () => {
  assert.deepEqual(findMainForceGroupRank(
    { kind: "instantLargeBuy" },
    ["網通"],
    rankings,
  ), { group: "網通", rank: 11, direction: "漲幅", change: "+1.55%" });
  assert.deepEqual(findMainForceGroupRank(
    { kind: "instantLargeSell" },
    ["百貨"],
    rankings,
  ), { group: "百貨", rank: 11, direction: "跌幅", change: "-1.06%" });
  assert.equal(findMainForceGroupRank({ kind: "instantLargeBuy" }, ["百貨"], rankings), null);
  assert.equal(findMainForceGroupRank({ kind: "instantLargeSell" }, ["網通"], rankings), null);
});

test("writes the trigger-time group rank into the permanent signal note and can read it back", () => {
  const [annotated] = annotateMainForceGroupRanks([{
    ticker: "2344",
    kind: "mainForceStrongBullish",
    note: "A～D同步濾網 V1｜量比 1.50×",
  }], new Map([["2344", ["記憶體"]]]), rankings);
  assert.match(annotated.note, /族群同步 記憶體 漲幅第 1 名 \+4\.82%/);
  assert.deepEqual(extractMainForceGroupRank(annotated.note), {
    group: "記憶體",
    rank: 1,
    direction: "漲幅",
    change: "+4.82%",
  });
  assert.equal(stripMainForceGroupRank(annotated.note), "A～D同步濾網 V1｜量比 1.50×");
});

test("uses a captured trigger-time group rank only when its direction still matches the signal", () => {
  assert.equal(hasCapturedMatchingTopGroup({ kind: "mainForceStrongBullish", note: "A～D｜族群同步 記憶體 漲幅第 3 名 +2.10%" }), true);
  assert.equal(hasCapturedMatchingTopGroup({ kind: "mainForceStrongBullish", note: "A～D｜族群同步 航運 跌幅第 2 名 -1.80%" }), false);
  assert.equal(hasCapturedMatchingTopGroup({ kind: "mainForceStrongBearish", note: "A～D｜族群同步 航運 跌幅第 2 名 -1.80%" }), true);
  assert.equal(hasCapturedMatchingTopGroup({ kind: "intradayExtraLargeBuy", note: "特大單｜族群同步 記憶體 漲幅第 1 名" }), true);
  assert.equal(hasCapturedMatchingTopGroup({ kind: "intradayExtraLargeSell", note: "特大單｜族群同步 記憶體 漲幅第 1 名" }), false);
  assert.equal(hasCapturedMatchingTopGroup({ kind: "fourGateBullish", note: "沒有族群標記" }), false);
});

test("recovers legacy instant-large rows but still rejects a saved wrong-direction marker", () => {
  assert.equal(hasCapturedOrLegacyInstantLargeGroup({
    kind: "instantLargeBuy",
    note: "同秒 2 筆｜合計 120 張｜約 3,200.0 萬",
  }), true);
  assert.equal(hasCapturedOrLegacyInstantLargeGroup({
    kind: "instantLargeSell",
    note: "同秒 2 筆｜合計 120 張｜約 3,200.0 萬｜族群同步 航運 漲幅第 2 名",
  }), false);
  assert.equal(hasCapturedOrLegacyInstantLargeGroup({
    kind: "instantLargeSell",
    note: "同秒 2 筆｜合計 120 張｜約 3,200.0 萬｜族群同步 航運 跌幅第 2 名",
  }), true);
  assert.equal(hasCapturedOrLegacyInstantLargeGroup({
    kind: "mainForceStrongBullish",
    note: "沒有族群標記",
  }), false);
});

test("never fills river bears with stocks outside the weakest ten official groups", () => {
  const fullRankings = {
    weak: Array.from({ length: 22 }, (_, index) => ({ rank: index + 1, name: `族群${index + 1}`, change: `-${(2 - index / 20).toFixed(2)}%` })),
  };
  const signals = Array.from({ length: 12 }, (_, index) => ({
    ticker: String(6000 + index),
    kind: "riverBear",
    note: `均線趨勢分 ${44 - index}`,
  }));
  const groups = new Map(signals.map((signal, index) => [signal.ticker, [`族群${index + 11}`]]));
  const fallback = selectRiverBearSignalsWithFallback(signals, groups, fullRankings);
  assert.deepEqual(fallback, []);
  assert.deepEqual(findWeakestGroupRank(["族群11"], fullRankings), { group: "族群11", rank: 11, direction: "跌幅", change: "-1.50%" });

  const directSignal = { ticker: "9999", kind: "riverBear", note: "均線趨勢分 40" };
  const directGroups = new Map([...groups, [directSignal.ticker, ["族群2"]]]);
  assert.deepEqual(
    selectRiverBearSignalsWithFallback([...signals, directSignal], directGroups, fullRankings),
    [directSignal],
  );

  const partialGroups = new Map([
    [signals[0].ticker, ["族群11"]],
    [signals[1].ticker, ["族群13"]],
  ]);
  assert.deepEqual(selectRiverBearSignalsWithFallback(signals, partialGroups, fullRankings), []);
});
