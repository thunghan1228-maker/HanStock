import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  normalizeInstantLargeOrderSignal,
  parseInstantLargeOrderNote,
} from "../lib/instant-large-thresholds.mjs";

function signal(kind, note, label = "舊標籤") {
  return { tradeDate: "2026-09-01", ticker: "8027", name: "鈦昇", kind, label, barTs: 1, price: 189.5, note };
}

test("parses lots and Chinese money units from instant large-order notes", () => {
  assert.deepEqual(parseInstantLargeOrderNote("同秒 2 筆｜合計 65 張｜約 1229.5 萬｜成交價 189～189.5"), {
    lots: 65,
    amount: 12_295_000,
  });
  assert.deepEqual(parseInstantLargeOrderNote("同秒 1 筆｜合計 100 張｜約 1.25 億"), {
    lots: 100,
    amount: 125_000_000,
  });
});

test("removes saved signals below both 100 lots and 30 million", () => {
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeBuy", "同秒 2 筆｜合計 65 張｜約 1229.5 萬")), null);
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeBuy", "同秒 1 筆｜合計 2 張｜約 1212.0 萬")), null);
});

test("keeps general signals at 100 lots or 30 million", () => {
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeSell", "同秒 1 筆｜合計 100 張｜約 497.0 萬"))?.label, "瞬間大單連續倒出");
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeBuy", "同秒 3 筆｜合計 66 張｜約 3618.2 萬"))?.label, "瞬間大單連續敲進");
});

test("requires 300 lots or 50 million for the extra-large label", () => {
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeSell", "同秒 1 筆｜合計 59 張｜約 3073.9 萬", "瞬間特大賣單倒出"))?.label, "瞬間大單連續倒出");
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeSell", "同秒 2 筆｜合計 300 張｜約 1000.0 萬"))?.label, "瞬間特大賣單倒出");
  assert.equal(normalizeInstantLargeOrderSignal(signal("instantLargeBuy", "同秒 2 筆｜合計 20 張｜約 5000.0 萬"))?.label, "瞬間特大買單敲進");
});

test("requests and retains the complete intraday instant-large history", async () => {
  const [route, store, page] = await Promise.all([
    readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../db/early-sell-history.ts", import.meta.url), "utf8"),
    readEarlySellSources(),
  ]);

  assert.match(route, /instantLargeUrl\.searchParams\.set\("limit", "5000"\)/);
  assert.match(route, /readEarlySellSignals\(\{ tradeDate, query, limit: 10_000 \}\)/);
  assert.match(store, /Math\.min\(10_000,/);
  assert.match(page, /daytrade-early-sell\?limit=5000&snapshot=1/);
  assert.match(page, /一般達 100 張或 3,000 萬元；特大達 300 張或 5,000 萬元/);
});
