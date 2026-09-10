import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

test("removes only the top K-line force badge and preserves main-force chart data", () => {
  const embed = readFileSync(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const page = readFileSync(new URL("../app/kline/page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(embed, /largeForceBadgeBootstrap|hanstock-kline-large-force/);
  assert.match(embed, /intradayForceBootstrap\(ticker, interval\)/);
  assert.match(embed, /periodAwareForceAmountQuoteBootstrap/);
  assert.match(page, /盘中主力大单进出|盤中主力大單進出/);
  assert.match(page, /日線主力大單累積/);
  assert.match(page, /force-chart-after/);
});
