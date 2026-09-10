import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const panelPath = new URL("../app/stock-screener/EtfHoldingsPanel.tsx", import.meta.url);
const routePath = new URL("../app/api/active-etf-radar/route.ts", import.meta.url);

test("replaces the ETF draft with official daily holdings and market-wide radar", async () => {
  const [panel, route] = await Promise.all([
    readFile(panelPath, "utf8"),
    readFile(routePath, "utf8"),
  ]);

  assert.match(panel, /fetch\(`\/api\/active-etf-radar\?/);
  assert.match(panel, /主動式 ETF 持股雷達/);
  assert.match(panel, /回看資料日/);
  assert.match(panel, /共同持有/);
  assert.match(panel, /持股權重/);
  assert.match(panel, /連續加減碼/);
  assert.match(panel, /個股跨 ETF 明細/);
  assert.match(panel, /持股異動包含申購贖回影響/);
  assert.ok((panel.match(/onClick=\{\(\) => openKline\(row\)\}/g) ?? []).length >= 2);
  assert.match(panel, /url\.searchParams\.set\("interval", "5m"\)/);
  assert.match(panel, /開啟 \$\{row\.ticker\} \$\{row\.name\} 五分鐘 K 線/);
  assert.doesNotMatch(panel, /功能草稿/);
  assert.match(route, /\/api\/hub\/active-etf-radar/);
  assert.match(route, /active_etf_radar_pending/);
  assert.match(route, /date_invalid/);
});
