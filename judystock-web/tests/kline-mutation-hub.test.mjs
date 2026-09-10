import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

async function readHubScript() {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const match = source.match(/const childMutationHubBootstrap = `<script id="hanstock-child-mutation-hub">([\s\S]*?)<\/script>`;/);
  assert.ok(match, "child mutation hub bootstrap must exist");
  return match[1];
}

test("shares one childList MutationObserver across the migrated K-line listeners", async () => {
  const script = await readHubScript();
  const observers = [];
  const document = {documentElement: {}};
  const window = {};
  const context = {
    document,
    window,
    MutationObserver: class {
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(target, options) {
        this.target = target;
        this.options = options;
      }
    },
  };

  vm.runInNewContext(script, context);
  assert.equal(typeof window.__hanstockObserveChildMutations, "function");

  const calls = [];
  window.__hanstockObserveChildMutations((records, observer) => calls.push(["a", records, observer]));
  window.__hanstockObserveChildMutations((records, observer) => calls.push(["b", records, observer]));

  assert.equal(observers.length, 1);
  assert.equal(observers[0].options.subtree, true);
  assert.equal(observers[0].options.childList, true);
  const records = [{type: "childList"}];
  observers[0].callback(records, observers[0]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], "a");
  assert.strictEqual(calls[0][1], records);
  assert.strictEqual(calls[0][2], observers[0]);
  assert.equal(calls[1][0], "b");
  assert.strictEqual(calls[1][1], records);
  assert.strictEqual(calls[1][2], observers[0]);
});

test("migrates exactly the seven childList-only K-line observers", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const subscriptions = source.match(/window\.__hanstockObserveChildMutations\(/g) ?? [];
  assert.equal(subscriptions.length, 7);
  assert.doesNotMatch(source, /new MutationObserver\((?:sync|queue)\)\.observe\(document\.documentElement,\{subtree:true,childList:true\}\)/);
});

test("shares one childList+characterData MutationObserver across the migrated K-line listeners", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const match = source.match(/const childCharacterMutationHubBootstrap = `<script id="hanstock-child-character-mutation-hub">([\s\S]*?)<\/script>`;/);
  assert.ok(match, "child+characterData mutation hub bootstrap must exist");
  const observers = [];
  const document = {documentElement: {}};
  const window = {};
  const context = {
    document,
    window,
    MutationObserver: class {
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(target, options) {
        this.target = target;
        this.options = options;
      }
    },
  };

  vm.runInNewContext(match[1], context);
  assert.equal(typeof window.__hanstockObserveChildCharacterMutations, "function");

  const calls = [];
  window.__hanstockObserveChildCharacterMutations((records, observer) => calls.push(["a", records, observer]));
  window.__hanstockObserveChildCharacterMutations((records, observer) => calls.push(["b", records, observer]));

  assert.equal(observers.length, 1);
  assert.equal(observers[0].options.subtree, true);
  assert.equal(observers[0].options.childList, true);
  assert.equal(observers[0].options.characterData, true);
  const records = [{type: "characterData"}];
  observers[0].callback(records, observers[0]);
  assert.equal(calls.length, 2);
  assert.strictEqual(calls[0][1], records);
  assert.strictEqual(calls[0][2], observers[0]);
  assert.strictEqual(calls[1][1], records);
  assert.strictEqual(calls[1][2], observers[0]);
});

test("migrates exactly the eleven childList+characterData K-line observers", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const subscriptions = source.match(/window\.__hanstockObserveChildCharacterMutations\(/g) ?? [];
  assert.equal(subscriptions.length, 11);
  assert.doesNotMatch(source, /new MutationObserver\([^\n]*\)\.observe\(document\.documentElement,\{subtree:true,childList:true,characterData:true\}\)/);
});

test("caches co-located signal overlay candle DOM between draws", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  assert.match(source, /allGroupsDirty=true,cachedAllGroups=\[\],cachedSignalSvg=null,cachedSignalGroups=\[\],cachedSignalLayer=null/);
  assert.match(source, /resolveSignalDom=\(\)=>\{if\(!allGroupsDirty&&cachedSignalSvg&&cachedSignalLayer&&cachedSignalGroups\.length\)return\{svg:cachedSignalSvg,groups:cachedSignalGroups,candleLayer:cachedSignalLayer\}/);
  assert.match(source, /dom=resolveSignalDom\(\),svg=dom\.svg,groups=dom\.groups,candleLayer=dom\.candleLayer/);
});

test("invalidates co-located signal DOM cache only when the candle tree changes", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  assert.match(source, /records=>\{if\(records\.some\(record=>record\.type===\"childList\"\)\)\{allGroupsDirty=true;cachedSignalSvg=null;cachedSignalGroups=\[\];cachedSignalLayer=null;cachedSignalLines=\[\];cachedSignalDates=null;cachedSignalBars=null;cachedCandleByDate=null\}queue\(\)\}/);
});

test("co-located signal DOM ranking measures each candidate SVG only once per cache rebuild", async () => {
  const source = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  assert.match(source, /const ranked=candidates\.map\(node=>\{const box=node\.getBoundingClientRect\(\),style=getComputedStyle\(node\);return\{node,score:box\.width\*box\.height,visible:/);
  assert.match(source, /\.sort\(\(a,b\)=>b\.score-a\.score\)/);
});


test('caches signal candle lines and date index between draws', async () => {
  const source = await readFile(new URL('../app/api/kline-embed/[ticker]/route.ts', import.meta.url), 'utf8');
  assert.match(source, /cachedSignalLines=groups\.map\(group=>group\.querySelector\("line"\)\)/);
  assert.match(source, /cachedSignalBars!==bars/);
  assert.match(source, /cachedCandleByDate=new Map\(cachedSignalDates\.map\(\(date,i\)=>\[date,cachedSignalGroups\[i\]\]\)\)/);
  assert.match(source, /line=i>=0\?cachedSignalLines\[i\]:null/);
});
