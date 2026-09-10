import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [styles, page] = await Promise.all([
  readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
]);

test("the intraday popup has a wide draggable scrollbar with clickable arrows", () => {
  assert.match(styles, /\.early-sell-toast-list\s*\{[^}]*overflow-y:\s*auto/);
  assert.match(styles, /\.early-sell-toast-scroll-track\{[^}]*position:absolute/);
  assert.match(styles, /\.early-sell-toast-scroll-thumb\{[^}]*min-height:52px/);
  assert.match(styles, /\.early-sell-toast-scroll-thumb:active\{cursor:grabbing/);
  assert.match(styles, /\.early-sell-toast-scroll-button\.is-up\{top:5px\}/);
  assert.match(styles, /\.early-sell-toast-scroll-button\.is-down\{bottom:5px\}/);
  assert.match(page, /aria-label="向上捲動盤中訊號"/);
  assert.match(page, /aria-label="向下捲動盤中訊號"/);
  assert.match(page, /aria-label="拖曳盤中訊號捲軸"/);
  assert.match(page, /onPointerDown=\{startPopupThumbDrag\}/);
  assert.match(page, /onPointerMove=\{movePopupThumb\}/);
  assert.match(page, /setPointerCapture\(event\.pointerId\)/);
  assert.match(page, /list\.scrollTop = Math\.max\(0, Math\.min\(maxScroll/);
  assert.match(page, /list\.scrollBy\(\{ top: direction \* Math\.max\(220/);
});
