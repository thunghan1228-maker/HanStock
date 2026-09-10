import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const etfPanel = readFileSync(new URL("../app/HighDividendEtfPanel.tsx", import.meta.url), "utf8");
const rankingRoute = readFileSync(new URL("../app/api/live-ranking/route.ts", import.meta.url), "utf8");

test("keeps the battle home document available while client data loads", () => {
  assert.match(page, /dynamic\([\s\S]*ssr: false/);
  assert.match(page, /ClientOnlyBattleHome/);
  assert.match(page, /HanStock 盤中戰鬥版/);
});

test("deduplicates slow dashboard reads and bounds client waits", () => {
  assert.match(page, /fetch\("\/api\/live-ranking"/);
  assert.doesNotMatch(page, /api\/live-ranking\?refresh=/);
  assert.match(page, /AbortSignal\.timeout\(15_000\)/);
  assert.match(etfPanel, /fetch\("\/api\/high-dividend-etfs"/);
  assert.match(etfPanel, /AbortSignal\.timeout\(15_000\)/);
  assert.match(rankingRoute, /stale-while-revalidate=300/);
});
