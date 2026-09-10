import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";
import { rankingForceClock, summarizeRankingForce, isRankingForceCurrent } from "../lib/ranking-intraday-force.ts";
import { calculateIntradayLargeForceValue } from "../lib/intraday-large-force.ts";
import { parseRankingForceClose } from "../lib/ranking-force-close.ts";

const at = clock => Date.parse(`2026-09-04T${clock}+08:00`);
const clock = rankingForceClock("2026-09-04", at("10:05:30"));
const row = pct => ({ forcePct: pct, tradeDate: "2026-09-04", barTs: at("10:05:00") });

function loadWith(supplied) {
  const source = readFileSync(new URL("../lib/ranking-intraday-force.ts", import.meta.url), "utf8");
  const script = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""));
  return runInNewContext(`${script}\n({ loadRankingForceValues })`, {
    Date, AbortSignal, calculateIntradayLargeForceValue,
    readIntradayLargeForceMonitorRows: async () => ({ rows: [] }),
    readRankingForceClose: async () => [],
    backfillRankingForceClose: async () => [],
    fetch: async () => { throw new Error("Unexpected live request"); },
    ...supplied,
  }).loadRankingForceValues;
}

test("group force is the equal-weight mean of every distinct configured member, including zero", () => {
  const values = new Map([["1111", row(30)], ["2222", row(-15)], ["3333", row(0)]]);
  const group = summarizeRankingForce(["1111", "2222", "3333", "1111"], values, clock);
  assert.equal(group.forcePct, 5);
  assert.equal(group.totalCount, 3);
  assert.equal(group.availableCount, 3);
  assert.equal(summarizeRankingForce(["1111"], values, clock).forcePct, 30);
  assert.equal(group.sampledAt, clock.sampledAt);
});

test("missing/stale members do not turn into zero or a misleading whole-group average", () => {
  const values = new Map([["1111", row(30)], ["2222", { ...row(50), barTs: at("09:00:00") }]]);
  const group = summarizeRankingForce(["1111", "2222", "3333"], values, clock);
  assert.equal(group.forcePct, null);
  assert.equal(group.availableCount, 1);
  assert.equal(group.totalCount, 3);
  assert.equal(isRankingForceCurrent({ ...row(1), tradeDate: "2026-09-03" }, clock), false);
  assert.equal(isRankingForceCurrent({ ...row(1), barTs: at("10:06:00") }, clock), false);
  assert.equal(isRankingForceCurrent(row(1), { ...clock, preopen: true }), false);
});

test("weekend ranking uses its actual closing trade date, not the calendar date", () => {
  const weekend = rankingForceClock("20260904", Date.parse("2026-09-05T09:00:00+08:00"));
  assert.equal(weekend.tradeDate, "2026-09-04");
  assert.equal(weekend.cutoff, at("13:30:00"));
  assert.equal(isRankingForceCurrent({ ...row(10), barTs: at("13:30:00") }, weekend), true);
  assert.equal(isRankingForceCurrent({ ...row(10), barTs: at("09:00:00") }, weekend), false);
});

test("historical ticks recover the complete September 4 amount, not an opening snapshot", () => {
  const source = { ticker: "3324", trade_date: "2026-09-04", main_force_data_available: true, main_force_data_status: "historical_ticks", large_buy_amount: 2995315000, large_sell_amount: 966470000, total_turnover_amount: 3961785000 };
  const value = parseRankingForceClose(source, "2026-09-04");
  assert.equal(value.forcePct.toFixed(2), "51.21");
  assert.equal(value.barTs, at("13:30:00"));
  assert.equal(parseRankingForceClose({ ...source, main_force_data_status: "pending_backfill", main_force_data_available: false }, "2026-09-04"), null);
  assert.equal(parseRankingForceClose({ ...source, main_force_data_status: "persisted_intraday_bars" }, "2026-09-04"), null);
  assert.equal(parseRankingForceClose(source, "2026-09-03"), null);
  assert.equal(parseRankingForceClose({ ...source, large_buy_amount: null }, "2026-09-04"), null);
  assert.equal(parseRankingForceClose({ ...source, large_buy_amount: 0, large_sell_amount: 0 }, "2026-09-04").forcePct, 0);
});

test("closed rankings restore saved totals and progressively backfill missing members", async () => {
  const closing = rankingForceClock("20260904", Date.parse("2026-09-05T09:00:00+08:00"));
  const complete = { ticker: "3324", ...row(51.21037613), barTs: at("13:30:00") };
  const requests = [];
  const load = loadWith({
    readIntradayLargeForceMonitorRows: async () => ({ rows: [{ ticker: "3324", ...row(100), barTs: at("09:00:00") }] }),
    readRankingForceClose: async () => [complete],
    backfillRankingForceClose: async codes => { requests.push([...codes]); return codes.map(ticker => ({ ticker, ...row(10), barTs: at("13:30:00") })); },
  });
  const members = ["3324", ...Array.from({ length: 30 }, (_, i) => String(4000 + i))];
  const values = await load(members, closing);
  assert.equal(values.get("3324").forcePct, complete.forcePct);
  assert.equal(requests[0].length, 25);
  assert.equal(requests[0].includes("3324"), false);
  assert.equal(values.size, 26);
  assert.equal(summarizeRankingForce(members.slice(0, 3), values, closing).availableCount, 3);
});

test("live ranking never reads future closing totals", async () => {
  let historicalReads = 0;
  const load = loadWith({
    readRankingForceClose: async () => { historicalReads++; return []; },
    backfillRankingForceClose: async () => { historicalReads++; return []; },
    readIntradayLargeForceMonitorRows: async () => ({ rows: [{ ticker: "3324", ...row(21) }] }),
  });
  const values = await load(["3324"], clock);
  assert.equal(values.get("3324").forcePct, 21);
  assert.equal(historicalReads, 0);
});

test("fresh snapshot members are reused and all missing members get one bounded batch calculation", async () => {
  const calls = [];
  const source = readFileSync(new URL("../lib/ranking-intraday-force.ts", import.meta.url), "utf8");
  const script = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""));
  const { loadRankingForceValues } = runInNewContext(`${script}\n({ loadRankingForceValues })`, {
    Date, AbortSignal, calculateIntradayLargeForceValue,
    readIntradayLargeForceMonitorRows: async () => ({ rows: [{ ticker: "1111", ...row(30) }, { ticker: "2222", ...row(99), barTs: at("09:00:00") }] }),
    fetch: async (_url, options) => {
      calls.push(JSON.parse(options.body).codes);
      return Response.json({ data: { "2222": [{ ts: at("10:05:00"), close: 100, volume: 1000, main_buy_amount: 10000000, main_sell_amount: 0, main_force_available: true }] } });
    },
  });
  const values = await loadRankingForceValues(["1111", "2222", "2222"], clock);
  assert.deepEqual(calls, [["2222"]]);
  assert.equal(values.get("1111").forcePct, 30);
  assert.equal(values.get("2222").forcePct, 10);
});

test("ranking response and UI no longer perform force work for hidden columns", () => {
  const route = readFileSync(new URL("../app/api/live-ranking/route.ts", import.meta.url), "utf8");
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(route, /loadRankingForceValues|withStockForce|withGroupForce|summarizeRankingForce/);
  assert.match(route, /stocks: \{ strong: strongStocks, weak: weakStocks \}/);
  assert.match(route, /"Cache-Control": "no-store"/);
  assert.equal((page.match(/<RankingForceCell value=\{row\.intradayForce\}/g) ?? []).length, 0);
  assert.doesNotMatch(page, /desktop-stock-header[^\n]*盤中大戶力|desktop-group-header[^\n]*盤中大戶力|mobile-stock-rank-header[^\n]*盤中／盤後/);
});
