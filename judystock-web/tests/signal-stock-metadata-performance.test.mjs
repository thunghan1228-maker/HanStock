import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [page, route] = await Promise.all([
  readEarlySellSources(),
  readFile(new URL("../app/api/stock-search/route.ts", import.meta.url), "utf8"),
]);

test("returns cached signal names and groups without waiting for live quotes", () => {
  const metadataBranch = route.indexOf('searchParams.get("metadataOnly") === "1"');
  const liveQuoteCall = route.indexOf("await fetchLiveChanges(requestedTickers)", metadataBranch);

  assert.ok(metadataBranch >= 0);
  assert.ok(liveQuoteCall > metadataBranch);
  assert.match(route, /max-age=3600, s-maxage=86400, stale-while-revalidate=604800/);
  assert.match(route, /source\.replace\(\/\\r\\n\?\/g, "\\n"\)/);
});

test("loads the open signal tab first and paints metadata one batch at a time", () => {
  assert.match(page, /centerMode === "blackDragon" \? blackDragonSignals/);
  assert.match(page, /await loadBatches\(toBatches\(focusedTickers\)\)/);
  assert.match(page, /metadataOnly=1&tickers=/);
  assert.match(page, /cache: "force-cache"/);
  assert.match(page, /applyStocks\(Array\.isArray\(payload\.stocks\)/);
  assert.match(page, /if \(active\) await loadBatches\(toBatches\(remainingTickers\)\)/);
  assert.match(page, /\}, \[focusedSignalTickers, signalTickers\]\);/);
});

test("the built worker returns real names and groups for signal tickers", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("signal-stock-meta-test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const response = await worker.fetch(
    new Request("http://localhost/api/stock-search?metadataOnly=1&tickers=2855,2221,2520"),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(payload.stocks.map(({ ticker, name, group }) => ({ ticker, name, group })), [
    { ticker: "2855", name: "統一證", group: "金融股" },
    { ticker: "2221", name: "大甲", group: "未分類" },
    { ticker: "2520", name: "冠德", group: "未分類" },
  ]);
});
