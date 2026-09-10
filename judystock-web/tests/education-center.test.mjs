import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("teaching center ships search, category filters and future video structure", async () => {
  const page = await read("app/education/page.tsx");
  const data = await read("app/education/education-data.ts");

  for (const category of ["beginner", "intraday", "five-minute", "daily", "screening", "chips", "features", "video"]) {
    assert.match(data, new RegExp(`id: "${category}"`));
  }
  for (const term of ["盤中特大買單", "盤中特大賣單", "12 空", "1＋2 多", "905D", "520", "日線 11 策略", "三角收斂", "創高黑龍"]) {
    assert.ok(data.includes(term), `missing teaching term: ${term}`);
  }
  assert.match(page, /type="search"/);
  assert.match(data, /影音教學專區/);
  assert.match(page, /條件整理中/);
});

test("battle and screener pages link to the teaching center without changing bottom navigation", async () => {
  const battle = await read("app/page.tsx");
  const screener = await read("app/stock-screener/page.tsx");
  const bottomNav = battle.match(/<nav className="bottom-nav"[\s\S]*?<\/nav>/)?.[0] ?? "";

  assert.match(battle, /href="\/education"/);
  assert.match(battle, /href="\/education\?category=intraday"/);
  assert.match(screener, /href="\/education\?category=screening"/);
  assert.equal((bottomNav.match(/<button/g) ?? []).length, 8);
});
