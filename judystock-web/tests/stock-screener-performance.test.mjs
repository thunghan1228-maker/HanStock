import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("opens the stock screener from saved rows while official data refreshes in the background", async () => {
  const page = await readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8");

  assert.match(page, /SCREENER_WARM_CACHE_KEY = "hanstock:stock-screener:rows:v1"/);
  assert.match(page, /SCREENER_WARM_CACHE_MAX_AGE = 7 \* 24 \* 60 \* 60_000/);
  assert.match(page, /setCachedRows\(warm\.rows\)/);
  assert.match(page, /setLoading\(false\)/);
  assert.match(page, /claimBackgroundRefresh\(\)/);
  assert.match(page, /SCREENER_BACKGROUND_REFRESH_MS = 30 \* 60_000/);
  assert.match(page, /\/api\/market-ranking\?refresh=stock-screener-background-v1/);
  assert.doesNotMatch(page, /\/api\/market-ranking\?refresh=\$\{Date\.now\(\)\}/);
});

test("uses the cached technical indicator endpoint instead of rebuilding market history", async () => {
  const page = await readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8");
  const workbench = await readFile(new URL("../app/stock-screener/StrategyWorkbench.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8");

  assert.match(page, /\/api\/technical-market\?fast=1&view=stock-screener-v1/);
  assert.match(workbench, /\/api\/technical-market\?fast=1&view=stock-screener-v1/);
  assert.doesNotMatch(workbench, /fetch\("\/api\/technical-market", \{ cache: "no-store" \}\)/);
  assert.match(workbench, /TECHNICAL_WARM_CACHE_KEY = "hanstock:stock-screener:technical:v1"/);
  assert.match(workbench, /technicalRefreshAt > Date\.now\(\)/);
  assert.match(route, /FAST_INDICATOR_CACHE_MS = 30 \* 60 \* 1_000/);
  assert.match(route, /s-maxage=1800, stale-while-revalidate=86400/);
  assert.match(route, /aboveMa5: row\.aboveMa5/);
  assert.match(route, /candle: row\.candle/);
});

test("black dragon backfill stays within the Worker memory budget", async () => {
  const [home, panel, route] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/stock-screener/BlackDragonPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/technical-market/route.ts", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(home, /tasks\.slice\(start, start \+ 4\)/);
  assert.doesNotMatch(panel, /tasks\.slice\(start, start \+ 4\)/);
  assert.match(route, /if \(compact\) \{\s*return Response\.json\(\{/);
  assert.match(route, /indicatorBackfillStats/);
});
