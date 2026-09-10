import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";
import { parseRankingForceClose } from "../lib/ranking-force-close.ts";
import { backfillRankingForceClose } from "../lib/ranking-force-backfill.ts";

test("September 4 recovery archive contains valid completed records and the formerly missing stocks", () => {
  const archive = JSON.parse(readFileSync(new URL("../data/ranking-force-close/2026-09-04.json", import.meta.url), "utf8"));
  const rows = archive.rows.map(row => parseRankingForceClose(row, archive.tradeDate));
  assert.equal(rows.length, 325);
  assert.ok(rows.every(Boolean));
  assert.equal(new Set(rows.map(row => row.ticker)).size, 325);
  for (const ticker of ["2426", "3324", "3441", "6290", "8996", "3017", "2745", "6515", "3231"]) assert.ok(rows.some(row => row.ticker === ticker));
  assert.equal(rows.find(row => row.ticker === "3324").forcePct.toFixed(2), "51.21");
});

test("the recovered stocks remain available when the remote history endpoint is unavailable", async () => {
  const originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("History source unavailable"); };
  try {
    const rows = await backfillRankingForceClose(["8996", "3017", "2745"], "2026-09-04");
    assert.equal(rows.length, 3);
    assert.equal(requests, 0);
    assert.equal((await backfillRankingForceClose(["8996", "3017", "2745"], "2026-09-04")).length, 3);
    assert.equal(requests, 0);
  } finally { globalThis.fetch = originalFetch; }
});

test("verified closing totals survive a fresh worker and do not mix trade dates", async () => {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("CREATE TABLE river_radar_config (config_key TEXT PRIMARY KEY, config_json TEXT NOT NULL, engine_version TEXT NOT NULL, updated_at INTEGER NOT NULL)");
  const d1 = {
    prepare(sql) { return { bind(...args) { return { run: () => sqlite.prepare(sql).run(...args), all: () => ({ results: sqlite.prepare(sql).all(...args) }) }; } }; },
    batch: statements => Promise.all(statements.map(statement => statement.run())),
  };
  const source = readFileSync(new URL("../db/ranking-force-close.ts", import.meta.url), "utf8");
  const script = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""));
  const worker = () => runInNewContext(`${script}\n({ readRankingForceClose, saveRankingForceClose })`, { Date, __HANSTOCK_DB: d1 });
  const input = { ticker: "2426", trade_date: "2026-09-04", main_force_data_available: true, main_force_data_status: "historical_ticks", large_buy_amount: 2691034100, large_sell_amount: 996154400, total_turnover_amount: 6233846000 };
  const saved = parseRankingForceClose(input, "2026-09-04");
  await worker().saveRankingForceClose([saved]);
  await worker().saveRankingForceClose([saved]);
  const fresh = worker();
  const rows = await fresh.readRankingForceClose("2026-09-04");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].forcePct.toFixed(2), "27.19");
  assert.equal((await fresh.readRankingForceClose("2026-09-03")).length, 0);
  sqlite.close();
});
