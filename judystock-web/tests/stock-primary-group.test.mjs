import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { parseHanStockOfficialPrimaryGroupMap, parseStockPrimaryGroupMap } from "../lib/stock-primary-group.ts";

const source = `STOCK_GROUPS = {
  '小電組': [('5439', '高技')],
  'PCB': [('5439', '高技'), ('4958', '臻鼎-KY')],
  'ETF': [('0050', '元大台灣50')],
}
`;

test("uses the stock catalog's first listed group as the one official primary group", () => {
  const groups = parseStockPrimaryGroupMap(source, new Set(["ETF"]));
  assert.equal(groups.get("5439"), "小電組");
  assert.equal(groups.get("4958"), "PCB");
  assert.equal(groups.has("0050"), false);
  assert.equal(groups.has("8155"), false);
});

test("parses the Windows CRLF stock catalog used by production builds", () => {
  const groups = parseStockPrimaryGroupMap(source.replaceAll("\n", "\r\n"), new Set(["ETF"]));
  assert.equal(groups.get("5439"), "小電組");
  assert.equal(groups.get("4958"), "PCB");
});

test("builds the official HanStock group whitelist without auxiliary groups", () => {
  const groups = parseHanStockOfficialPrimaryGroupMap(source);
  assert.equal(groups.get("5439"), "小電組");
  assert.equal(groups.has("0050"), false);
});

test("production fallback snapshot matches the current HanStock 67-group membership", () => {
  const productionSource = readFileSync(new URL("../data/stock_groups.py", import.meta.url), "utf8");
  const groups = parseHanStockOfficialPrimaryGroupMap(productionSource);
  assert.equal(groups.size, 555);
  assert.equal(groups.has("5608"), false);
  assert.equal(groups.get("2357"), "AI");
  assert.equal(groups.get("2364"), "D電腦");
  assert.equal(groups.get("4919"), "記憶體");
  assert.equal(groups.get("8112"), "記憶體");
  assert.equal(groups.get("8150"), "記憶體");
  assert.equal(groups.get("8155"), "PCB");
});
