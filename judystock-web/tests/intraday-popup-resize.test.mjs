import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("resizes the live signal popup from every edge and corner", async () => {
  const [source, styles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(source, /\["n", "s", "e", "w", "ne", "nw", "se", "sw"\]/);
  assert.match(source, /startPopupResize/);
  assert.match(source, /movePopupResize/);
  assert.match(source, /EARLY_SELL_TOAST_SIZE_KEY/);
  assert.match(styles, /\.early-sell-resize-handle\.direction-n/);
  assert.match(styles, /\.early-sell-resize-handle\.direction-sw/);
  assert.doesNotMatch(source, /const startPopupResize[\s\S]{0,240}window\.innerWidth <= 900/);
  assert.doesNotMatch(styles, /\.early-sell-resize-handle\s*\{\s*display:none/);
  assert.match(styles, /\.early-sell-resize-handle\.direction-e\{[^}]*right:0[^}]*width:14px/);
});

test("uses a wider popup and wraps stock and signal text", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(styles, /width: min\(880px, calc\(100vw - 32px\)\)/);
  assert.match(styles, /\.early-sell-toast-stock-identity > strong \{[^}]*white-space:normal/);
  assert.match(styles, /\.early-sell-toast-detail \.early-signal-title-line > b \{[^}]*white-space:normal/);
});

test("enlarges and brightens trading-status badges inside the live popup", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(styles, /\.early-sell-toast-stock > \.stock-trading-badges\.compact>i\{[^}]*min-height:30px[^}]*font-size:14px!important/);
  assert.match(styles, /\.early-sell-toast-stock > \.stock-trading-badges\.compact>i\.available\{[^}]*border-color:#79ffba[^}]*box-shadow/);
  assert.match(styles, /\.early-sell-toast-stock > \.early-signal-source\{[^}]*min-height:28px[^}]*font-size:12px/);
  assert.match(styles, /\.early-sell-toast-stock > \.early-signal-source\.is-previous-day\{[^}]*border-color:#ffd45f[^}]*box-shadow/);
});
