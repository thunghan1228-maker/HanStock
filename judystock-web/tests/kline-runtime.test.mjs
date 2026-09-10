import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import {createForceChartScale} from "../lib/kline-force-scale.ts";
import {selectWatchlistCandleWindow} from "../lib/watchlist-candle-window.ts";

test("bundled runtime survives upstream outage and executes force chart geometry", async () => {
  const source = readFileSync(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const mod = {exports: {}};
  vm.runInNewContext(compiled, {exports: mod.exports,
    require: name => name === "next/server" ? {NextResponse: Response} : name.includes("?raw")
      ? {default: readFileSync(new URL("../vendor/hanstock-kline-runtime.txt", import.meta.url), "utf8")}
      : {createForceChartScale, selectWatchlistCandleWindow},
    fetch: async () => {throw new Error("upstream unavailable");}, Response, URL, AbortSignal, Map, Date, Math,
  });
  const result = await mod.exports.GET({nextUrl: new URL("http://localhost/api/kline-runtime?asset=/assets/index-CCs-RpRr.js")});
  assert.equal(result.status, 200);
  const output = await result.text();
  const initializer = output.match(/\[O,L\]=v\.useState\(\(\)=>\((\{macd:[\s\S]*?boll:!1\})\)\)/)?.[1];
  assert.ok(initializer, 'MACD initializer remains present');
  for (const coarse of [true, false]) for (const search of ['', '?view=grid', '?view=watchlist']) {
    const settings = vm.runInNewContext('(' + initializer + ')', { c: '5m', URLSearchParams, location: { search }, matchMedia: () => ({ matches: coarse }) });
    assert.equal(settings.macd, false, `five minute MACD starts off ${coarse}/${search}`);
  }
  assert.ok(output.includes('T3({url:"/api/trpc",maxItems:1,transformer:$s'));
  assert.ok(output.includes('initialData:()=>window.__hanstockReadCandleSnapshot?.(t,b)'));
  assert.ok(output.includes('"data-hanstock-native-signal-side":A.side'));
  assert.ok(output.includes('zt.glyphs.push({...Rt,kind:Ye.kind,note:Ye.note,label:Ye.label})'));
  assert.ok(output.includes('"data-hanstock-signal-kind":Me.kind,"data-hanstock-signal-note":Me.note,"data-hanstock-signal-label":Me.label'));
  assert.ok(output.includes('window.addEventListener("hanstock-force-ready",onForce)'));
  const start = output.indexOf("hanstockBarScale=hanstockCreateForceScale(Ko,af,Pl)");
  assert.ok(start > 0);
  const geometry = output.slice(start, output.indexOf(",lf=Ko.some", start));
  const rendered = vm.runInNewContext("(()=>{const " + geometry + ";return {maximum:$o,zero:Ac,path:rf}})()", {
    hanstockCreateForceScale: createForceChartScale, Ko: [1000,-20000,28191000], Cc: [1000,-19000,28172000], af:100, Pl:230, Dn: i => i*10,
  });
  assert.equal(rendered.maximum, 28191000);
  assert.equal(rendered.zero, 215);
  assert.ok(rendered.path.length);
  assert.equal(result.headers.get("X-HanStock-Force-Scale"), "symlog-bars-linear-cumulative");
  // Execute the patched viewport expression, including the warmup offset used
  // by MA/MACD/force arrays, rather than merely checking its source text.
  const viewportStart = output.indexOf('ct=v.useMemo(()=>');
  const viewportEnd = output.indexOf('),ca=', viewportStart);
  assert.ok(viewportStart > 0 && viewportEnd > viewportStart);
  const candles = ['09/02 09:00','09/03 09:00','09/03 13:25','09/04 09:00'].map(date=>({date,close:100}));
  const visible = vm.runInNewContext(`(()=>{let ct;${output.slice(viewportStart, viewportEnd+1)};return ct;})()`, {
    v:{useMemo:fn=>fn()},lt:candles,it:false,Le:true,URLSearchParams,location:{search:'?view=watchlist'},
  });
  assert.deepEqual(Array.from(visible,bar=>bar.date),candles.slice(1).map(bar=>bar.date));
  assert.equal(candles.length,4);
  const macdDefault = output.match(/\[O,L\]=v\.useState\(\(\)=>\(\{macd:(.*?),kd:!1,boll:!1\}\)\)/)?.[1];
  assert.ok(macdDefault);
  for(const [search,coarse,expected] of [['?view=watchlist',false,false],['?view=watchlist',true,false],['',false,true],['',true,false]]) {
    assert.equal(vm.runInNewContext(macdDefault,{c:"1d",URLSearchParams,location:{search},matchMedia:()=>({matches:coarse})}),expected);
  }
});
