import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { buildBlackDragonRows, findRecentBlackDragonSignal } from "../lib/black-dragon.ts";

test("創高黑龍必須同一天實際創高、均線至少 10 分且收黑 K", () => {
  const common = { name: "測試股", market: "twse", date: "2026/09/03", open: 100, high: 102, close: 99, changePct: -1, newHighPeriods: [], referenceHighs: { 5: 102 }, maLabel: "便宜", volume: 2_000_000, averageVolume20d: 1_000_000, volumeRatio20d: 2, turnoverAmount: 198_000_000, blackBodyPct: 1 };
  const rows = buildBlackDragonRows([
    { ...common, code: "3234", maScore: 10 },
    { ...common, code: "1101", maScore: 13, newHighPeriods: undefined },
    { ...common, code: "2455", maScore: 9 },
    { ...common, code: "2887", open: 99, close: 100, maScore: 15 },
    { ...common, code: "0050", maScore: 15 },
    { ...common, code: "6933", maScore: 15, newHighPeriods: [20], referenceHighs: { 5: 101, 20: 101 } },
  ]);
  assert.deepEqual(rows.map((row) => row.code), ["6933"]);
});

test("南亞只在 2026/09/01 的盤中創高黑 K 成立，後兩日資料不可混入", () => {
  const older = Array.from({ length: 240 }, (_, index) => {
    const date = new Date(Date.UTC(2025, 0, 1 + index)).toISOString().slice(0, 10).replaceAll("-", "/");
    const close = 100 + index * (142.5 / 239);
    return { date, open: close - 0.2, high: close, low: close - 1, close, volume: 1_000_000 };
  });
  const signal = findRecentBlackDragonSignal({
    code: "1303",
    name: "南亞",
    market: "twse",
    completedThrough: "2026/09/03",
    bars: [...older,
      { date: "2026/09/01", open: 250, high: 250, low: 231.5, close: 234.5, volume: 2_000_000 },
      { date: "2026/09/02", open: 240, high: 248, low: 232, close: 237.5, volume: 900_000 },
      { date: "2026/09/03", open: 243, high: 244.5, low: 219.5, close: 221, volume: 800_000 },
    ],
  });
  assert.equal(signal?.date, "2026/09/01");
  assert.equal(signal?.open, 250);
  assert.equal(signal?.high, 250);
  assert.equal(signal?.close, 234.5);
  assert.equal(signal?.changePct, -3.3);
  assert.ok(signal?.newHighPeriods.length);
  assert.equal(signal?.volumeRatio20d, 2);
  assert.equal(signal?.turnoverAmount, 469_000_000);
});

test("創高黑龍改讀同日完成 K 棒的專用結果並自動補齊全市場", () => {
  const panel = readFileSync(new URL("../app/stock-screener/BlackDragonPanel.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8");
  const blackDragonRoute = readFileSync(new URL("../app/api/black-dragon/route.ts", import.meta.url), "utf8");
  assert.match(panel, /fetch\("\/api\/black-dragon"/);
  assert.match(panel, /stockIndicatorBackfill=1&dailyStrategies=1/);
  assert.match(panel, /20 日均量/);
  assert.match(panel, /強爆量/);
  assert.match(route, /findRecentBlackDragonSignal/);
  assert.match(route, /blackDragonModelVersion: BLACK_DRAGON_MODEL_VERSION/);
  assert.match(blackDragonRoute, /stockNames\.get\(indicator\.code\) \?\? signal\.name/);
  assert.match(blackDragonRoute, /parseHanStockOfficialPrimaryGroupMap\(stockGroupsSource\)/);
  assert.match(blackDragonRoute, /officialPrimaryGroupByCode\.has\(indicator\.code\)/);
  assert.match(panel, /只保留 HanStock 正式 67 族群內的普通股/);
  assert.match(panel, /hanstock:black-dragon:v5:prior-five-high/);
});

test("策略工坊的創高黑龍卡直接共用正式名單", () => {
  const workbench = readFileSync(new URL("../app/stock-screener/StrategyWorkbench.tsx", import.meta.url), "utf8");
  assert.match(workbench, /name: "創高黑龍"/);
  assert.match(workbench, /fetch\("\/api\/black-dragon"/);
  assert.match(workbench, /enabled\.has\("blackDragon"\) && !blackDragonRows\[row\.code\]/);
  assert.match(workbench, /與「創高黑龍」專頁共用同一份正式結果/);
});

test("盤中訊號中新增創高的黑龍全部名單", () => {
  const page = readEarlySellSources();
  assert.match(page, /centerMode === "blackDragon"/);
  assert.match(page, />🐉 創高的黑龍/);
  assert.match(page, /strategyKind: "blackDragon"/);
  assert.match(page, /const endpoint = isIntradaySignalCollectionWindow/);
  assert.match(page, /setBlackDragonSignals\(\(current\)/);
  assert.match(page, /正式 67 族群 \$\{row\.groupName/);
  assert.match(page, /approvedBlackDragonKeys\.has\(intradaySignalKey\(signal\)\)/);
});
