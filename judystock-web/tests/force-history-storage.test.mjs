import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { runInNewContext } from "node:vm";

function storage() {
  const sqlite = new DatabaseSync(":memory:");
  let historyReads = 0;
  const statement = (sql, params = []) => ({
    bind: (...values) => statement(sql, values),
    all: async () => { if (sql.includes("WITH dates AS")) historyReads++; return { results: sqlite.prepare(sql).all(...params) }; },
    first: async () => sqlite.prepare(sql).get(...params) ?? null,
    run: async () => sqlite.prepare(sql).run(...params),
  });
  const d1 = { prepare: statement, batch: rows => Promise.all(rows.map(row => row.run())) };
  const source = stripTypeScriptTypes(readFileSync(new URL("../db/force-history.ts", import.meta.url), "utf8").replace(/\bexport /g, ""));
  const api = runInNewContext(`${source}\n({readIntradayForceHistory,saveIntradayForce,saveForceRefreshState,readForceRefreshState,saveDailyForce,readDailyForce})`, { __HANSTOCK_DB: d1 });
  return { sqlite, api, historyReads: () => historyReads };
}

test("one bulk query retains old and new sessions, real zeros, amounts, and legacy fallback", async () => {
  const { sqlite, api, historyReads } = storage();
  try {
    await api.readIntradayForceHistory("3163", "5m");
    const row = { ticker: "3163", tradeDate: "2026-09-03", interval: "5m", barTime: "2026-09-03 09:00", netVolume: 20000, buyAmount: 6000000, sellAmount: 1000000, netAmount: 5000000, mainTickCount: 3, updatedAt: 100, observed: 1, amountsAvailable: 1 };
    await api.saveIntradayForce([row, { ...row, barTime: "2026-09-03 09:05", netVolume: 0, buyAmount: 0, sellAmount: 0, netAmount: 0, mainTickCount: 0 }]);
    sqlite.prepare("INSERT INTO intraday_force_bars (ticker,trade_date,interval,bar_time,net_volume,main_tick_count,updated_at) VALUES (?,?,?,?,?,?,?)").run("3163", "2026-09-02", "5m", "2026-09-02 09:00", -15000, 2, 1);
    const count = historyReads(), rows = await api.readIntradayForceHistory("3163", "5m");
    assert.equal(historyReads() - count, 1);
    assert.equal(rows.length, 3);
    assert.equal(rows[0].netVolume, -15000);
    assert.equal(rows[1].netAmount, 5000000);
    assert.equal(rows[2].netVolume, 0);
    assert.equal(rows[2].observed, 1);
    assert.equal(rows[2].amountsAvailable, 1);
    await api.saveForceRefreshState("3163", "5m", 100000, "closed");
    assert.equal((await api.readForceRefreshState("3163", "5m")).refreshedAt, 100000);
  } finally { sqlite.close(); }
});

test('partial daily writes cannot overwrite a more complete saved day', async () => {
  const {sqlite,api}=storage();
  try {
    const full={ticker:'2303',tradeDate:'2026-09-01',netVolume:120000,barCount:54,sourceInterval:'5m',lastBarAt:'09/01 13:25',updatedAt:100};
    await api.saveDailyForce(full);
    await api.saveDailyForce({...full,netVolume:1000,barCount:2,updatedAt:200});
    await api.saveDailyForce({...full,netVolume:2000,barCount:20,sourceInterval:'1m',updatedAt:300});
    assert.equal((await api.readDailyForce('2303'))[0].netVolume,120000);
    await api.saveDailyForce({...full,netVolume:125000,barCount:271,sourceInterval:'1m',updatedAt:400});
    const stored=(await api.readDailyForce('2303'))[0];
    assert.equal(stored.netVolume,125000);
    assert.equal(stored.sourceInterval,'1m');
    await api.saveDailyForce({...full,tradeDate:'2026-09-02',netVolume:0,updatedAt:500});
    assert.equal((await api.readDailyForce('2303')).length,2);
  } finally { sqlite.close(); }
});
