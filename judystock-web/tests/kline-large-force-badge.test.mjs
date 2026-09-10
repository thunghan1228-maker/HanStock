import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {largeForceBadgeBootstrap} from '../lib/kline-large-force-badge.ts';
for (const source of ['iso','runtime','visible']) for (const value of [63.3,-28.2,0,null]) test('dated chart force badge '+source+' '+value,async()=>{
 const nodes=new Map(),callbacks=[],frames=[],calls=[];let writes=0;
 const element=()=>({dataset:{},setAttribute(){},appendChild(n){if(n.id)nodes.set(n.id,n)},set textContent(v){this.text=v;writes++}});
 nodes.set('hanstock-fixed-search-actions',element());
 const document={documentElement:{},hidden:false,getElementById:id=>nodes.get(id),createElement:element,addEventListener(){}};
 const window={__hanstockAllCandles:[{date:'2026-09-03 13:30'},{date:'2026-09-04 13:30'}],addEventListener(){}};
 if(source!=='iso'){window.__hanstockAllCandles=[{date:'09/03 13:30',ts:Date.parse('2026-09-03T05:30:00Z')},{date:'09/04 13:30',ts:Date.parse('2026-09-04T05:30:00Z')}];if(source==='visible'){window.__hanstockVisibleCandles=window.__hanstockAllCandles;window.__hanstockAllCandles=[];}}
 const script=largeForceBadgeBootstrap('2313').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
 vm.runInNewContext(script,{window,document,AbortSignal,AbortController,Date,encodeURIComponent,MutationObserver:class{constructor(fn){callbacks.push(fn)}observe(){}},requestAnimationFrame:fn=>frames.push(fn),setInterval(){},fetch:async url=>{calls.push(url);return{ok:true,json:async()=>({rows:[{ticker:'2313',tradeDate:'2026-09-04',forcePct:value}]})}}});
 const flush=async()=>{while(frames.length)frames.shift()();await new Promise(resolve=>setImmediate(resolve))};
 await flush();const badge=nodes.get('hanstock-kline-large-force');assert.equal(badge.text,'盤中大戶力 '+(value===null?'—':(value>0?'+':'')+value.toFixed(1)+'%'));assert.match(calls[0],/tradeDate=2026-09-04&tickers=2313/);
 const before=writes;callbacks[0]();await flush();assert.equal(writes,before);assert.equal(calls.length,1);
});

const result = (forcePct, tradeDate = '2026-09-04') => ({ ticker: '2313', tradeDate, forcePct });
function harness({ cached, fetcher } = {}) {
 const nodes=new Map(),frames=[],listeners={},storage=new Map(),calls=[];
 let now=Date.parse('2026-09-04T03:00:00Z'),timer;
 const element=()=>({dataset:{},children:[],setAttribute(){},appendChild(n){this.children.push(n);if(n.id)nodes.set(n.id,n)},set textContent(v){this.text=v;this.children=[]}});
 nodes.set('hanstock-fixed-search-actions',element());
 const document={documentElement:{},hidden:false,getElementById:id=>nodes.get(id),createElement:element,addEventListener:(type,fn)=>listeners[type]=fn};
 const window={__hanstockAllCandles:[{date:'2026-09-04 10:59'}],addEventListener:(type,fn)=>listeners[type]=fn};
 if(cached) storage.set('hanstock:kline-force:v2:2313:2026-09-04',JSON.stringify({savedAt:now-10000,row:cached}));
 vm.runInNewContext(largeForceBadgeBootstrap('2313').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1],{
  window,document,AbortSignal,AbortController,Date:class extends Date{static now(){return now}},encodeURIComponent,
  sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
  MutationObserver:class{observe(){}},requestAnimationFrame:fn=>frames.push(fn),setInterval(fn,ms){timer=fn;assert.equal(ms,5000)},
  fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({rows:[await (fetcher?.(calls.length)||result(8))]})}},
 });
 return {window,calls,storage,nodes,document,async flush(){while(frames.length)frames.shift()();await new Promise(r=>setImmediate(r))},
  tick(ms=5000){now+=ms;timer()},changeDate(){window.__hanstockAllCandles=[{date:'2026-09-07 09:01'}];listeners['hanstock-candles-updated']()},
  text(){const n=nodes.get('hanstock-kline-large-force');return n.text+' '+n.children.map(c=>c.text).join(' ')}};
}

test('same-date cached value appears before the network and refreshes without clearing',async()=>{
 let complete;const pending=new Promise(r=>complete=r);
 const h=harness({cached:result(6.5),fetcher:()=>pending});await h.flush();
 assert.match(h.text(),/\+6\.5%.*更新中/);complete(result(9));await h.flush();assert.match(h.text(),/\+9\.0%/);
 assert.equal(JSON.parse(h.storage.get('hanstock:kline-force:v2:2313:2026-09-04')).row.forcePct,9);
});
test('missing values stop loading, stay blank instead of zero, and retry after five seconds',async()=>{
 const h=harness({fetcher:n=>result(n===1?null:0)});await h.flush();assert.match(h.text(),/—.*尚無大戶資料/);
 h.tick(4999);await h.flush();assert.equal(h.calls.length,1);h.tick(1);await h.flush();assert.match(h.text(),/0\.0%.*累計/);
});
test('errors retain the last valid value and recover on the next update',async()=>{
 const h=harness({cached:result(6.5),fetcher:n=>{if(n===1)throw Error('timeout');return result(8)}});await h.flush();
 assert.match(h.text(),/\+6\.5%.*更新暫緩/);h.tick();await h.flush();assert.match(h.text(),/\+8\.0%/);
});
test('switching sessions cancels the old request and cannot leak its value into the new day',async()=>{
 let complete;const old=new Promise(r=>complete=r);
 const h=harness({cached:result(6.5),fetcher:n=>n===1?old:result(-3,'2026-09-07')});await h.flush();h.changeDate();await h.flush();
 assert.equal(h.calls[0].options.signal.aborted,true);assert.match(h.text(),/-3\.0%.*2026-09-07/);
 complete(result(99));await h.flush();assert.match(h.text(),/-3\.0%.*2026-09-07/);
});
test('a wrong-stock cache cannot be displayed and hidden charts do not poll',async()=>{
 const h=harness({cached:{...result(99),ticker:'2330'},fetcher:()=>result(null)});await h.flush();assert.doesNotMatch(h.text(),/99\.0/);
 h.document.hidden=true;h.tick();await h.flush();assert.equal(h.calls.length,1);
});
