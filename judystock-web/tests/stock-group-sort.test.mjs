import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

test("stock ranking can toggle between strength order and grouped-by-sector order on every device", () => {
  assert.match(page, /type StockGroupSort = "ranking" \| "group"/);
  assert.match(page, /const \[stockGroupSort, setStockGroupSort\] = useState<StockGroupSort>\("ranking"\)/);
  assert.match(page, /const toggleStockGroupSort = \(\) =>/);
  assert.match(page, /firstGroupPosition = new Map<string, number>\(\)/);
  assert.match(page, /將同族群股票集中排列/);
  assert.match(page, /mobile-stock-rank-header[\s\S]*?onClick=\{toggleStockGroupSort\}/);
  assert.match(page, /desktop-stock-header[\s\S]*?onClick=\{toggleStockGroupSort\}/);
  assert.match(page, /<b>\{row\.rank\}<\/b>/);
  assert.match(styles, /\.rank-group-sort-button\.active/);
  assert.match(styles, /iphone-stock-rank[^{]*\.rank-group-sort-button\.active/);
  assert.match(styles, /ipad-stock-rank[^{]*\.rank-group-sort-button\.active/);
});

test("desktop stock ranking shows price position and MA score in a wider stock-first layout", () => {
  assert.match(page, /desktop-stock-header[^\n]*目前位置[^\n]*均線分數/);
  assert.match(page, /rankingTechnicalMeta/);
  assert.match(page, /valuationRiverPosition\(quote\.price, technical\.riverBase\)/);
  assert.match(page, /className=\{`ranking-position-badge/);
  assert.match(page, /className="ranking-ma-score-badge">均線/);
  assert.match(styles, /\.desktop-dual-ranking \{[^}]*grid-template-columns: minmax\(0, 1\.65fr\) minmax\(360px, 1fr\)/);
  assert.match(styles, /\.ranking-position-badge\.is-cheap/);
  assert.match(styles, /\.ranking-ma-score-badge/);
});
