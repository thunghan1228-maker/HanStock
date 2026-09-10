import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pageSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
const globalStyles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

test("homepage includes the approved market-data disclaimer in the site footer", () => {
  assert.match(pageSource, /<footer className="site-disclaimer" aria-label="資料使用聲明">/);
  assert.match(pageSource, /本站內容係依公開市場資料彙整與統計，僅供資訊查詢及研究參考，不代表任何投資建議或獲利保證。投資人應自行評估並承擔交易風險與盈虧。/);
  assert.match(globalStyles, /\.site-disclaimer\s*\{/);
});
