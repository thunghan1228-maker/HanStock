import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  classifyMonthlyRevenueRecord,
  currentRevenueTarget,
  monthlyRevenueBaseline,
  parseMopsMonthlyRevenueHtml,
} from "../lib/monthly-revenue-records.ts";

test("targets the prior calendar month in Taipei", () => {
  assert.deepEqual(currentRevenueTarget(new Date("2026-09-02T00:00:00Z")), {
    revenueMonth: "2026-08",
    rocYear: 115,
    month: 8,
  });
});

test("parses a partial official MOPS monthly revenue row", () => {
  const html = `<div>出表日期：115/09/02</div><table><tr align=right><td align=center>4749</td><td align=left>新應材</td><td nowrap>484,959</td><td nowrap>412,000</td><td nowrap>301,000</td><td nowrap>17.71</td><td nowrap>61.12</td><td nowrap>1</td><td nowrap>1</td><td nowrap>1</td><td align=center>-</td></tr></table>`;
  const parsed = parseMopsMonthlyRevenueHtml(html, "tpex", "2026-08", "2026-09-02");
  assert.equal(parsed.sourcePublishedDate, "2026-09-02");
  assert.deepEqual(parsed.rows[0], {
    revenueMonth: "2026-08",
    sourcePublishedDate: "2026-09-02",
    stockCode: "4749",
    name: "新應材",
    market: "tpex",
    revenue: 484959,
    previousMonthRevenue: 412000,
    previousYearRevenue: 301000,
    momPct: 17.71,
    yoyPct: 61.12,
  });
});

test("classifies historical and rolling 12-month records independently", () => {
  assert.equal(monthlyRevenueBaseline.historyStart, "2011-01");
  assert.equal(monthlyRevenueBaseline.historyEnd, "2026-07");
  const highKinds = classifyMonthlyRevenueRecord({
    revenueMonth: "2026-08",
    sourcePublishedDate: "2026-09-02",
    stockCode: "4749",
    name: "新應材",
    market: "tpex",
    revenue: 484959,
    previousMonthRevenue: 412000,
    previousYearRevenue: 301000,
    momPct: 17.71,
    yoyPct: 61.12,
  }, []).map((signal) => signal.signalKind);
  assert.deepEqual(highKinds, ["all-time-high", "rolling-12-high"]);

  const lowKinds = classifyMonthlyRevenueRecord({
    revenueMonth: "2026-08",
    sourcePublishedDate: "2026-09-02",
    stockCode: "5315",
    name: "光聯",
    market: "tpex",
    revenue: 131249,
    previousMonthRevenue: 140000,
    previousYearRevenue: 160000,
    momPct: -6.25,
    yoyPct: -17.97,
  }, []).map((signal) => signal.signalKind);
  assert.deepEqual(lowKinds, ["rolling-12-low"]);
});

test("keeps monthly revenue history and automatic refresh wired without popup alerts", async () => {
  const [panel, route, worker, schema] = await Promise.all([
    readFile(new URL("../app/monthly-revenue-records.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/monthly-revenue-records/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
    readFile(new URL("../db/schema.ts", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(panel, /提醒開啟/);
  assert.doesNotMatch(panel, /提醒關閉/);
  assert.match(panel, /查看全部歷史/);
  assert.match(panel, /搜尋歷史/);
  assert.match(panel, /漲跌幅/);
  assert.match(panel, /即時漲跌/);
  assert.match(panel, /成交價/);
  assert.match(panel, /quoteSourceLabel/);
  assert.match(panel, /最近交易/);
  assert.doesNotMatch(panel, /revenue-alert-/);
  assert.doesNotMatch(panel, /營收高低點新公告|NEW REVENUE RECORD/);
  assert.doesNotMatch(panel, /ALERTS_ENABLED_KEY|LAST_SEEN_KEY|popupStocks|markCurrentSeen/);
  assert.doesNotMatch(panel, /if \(!active\) return \[\]/);
  assert.match(panel, /StockTradingBadges/);
  assert.match(panel, /QUOTE_REFRESH_MS = 10_000/);
  assert.match(panel, /每月營收成長榜/);
  assert.match(panel, /族群檢視/);
  assert.match(panel, /年增中位數排行/);
  assert.match(panel, /至少3檔已公布/);
  assert.match(route, /fetchCurrentOfficialMonthlyRevenue/);
  assert.match(route, /readMonthlyRevenueSnapshots/);
  assert.match(route, /parseHanStockOfficialPrimaryGroupMap/);
  assert.match(worker, /monthly-revenue-records/);
  assert.match(schema, /monthlyRevenueSignals/);
  assert.match(schema, /monthlyRevenueSnapshots/);
});
