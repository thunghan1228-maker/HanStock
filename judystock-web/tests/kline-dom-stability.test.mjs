import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=readFileSync(new URL('../app/api/kline-embed/[ticker]/route.ts',import.meta.url),'utf8');
const ast=ts.createSourceFile('route.ts',source,ts.ScriptTarget.Latest,true);
function bootstrap(name){let expression;function visit(n){if(ts.isVariableDeclaration(n)&&n.name.getText(ast)===name)expression=n.initializer.getText(ast);ts.forEachChild(n,visit)}visit(ast);const html=vm.runInNewContext(expression,{ticker:'2330',name:'台積電',escapeInlineScript:s=>s});return html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1]}
function harness(){const frames=[],observers=[];let changes=0;const mutate=()=>{changes++;for(const observer of observers)observer()};const node=(initial='')=>{let text=initial,html='';return{dataset:{},classList:{add(){}},style:{setProperty(){}},get textContent(){return text},set textContent(v){text=v;mutate()},get innerHTML(){return html},set innerHTML(v){html=v;mutate()},setAttribute(){},querySelector(){return null}}};const document={documentElement:{},addEventListener(){}};const context={document,window:{addEventListener(){}},requestAnimationFrame:fn=>frames.push(fn),MutationObserver:class{constructor(fn){this.fn=fn}observe(){observers.push(this.fn)}},fetch:()=>new Promise(()=>{}),setInterval(){}};return{document,context,node,mutate,changes:()=>changes,run:script=>vm.runInNewContext(script,context),settle:()=>{for(let i=0;i<12&&frames.length;i++){const batch=frames.splice(0);batch.forEach(fn=>fn())}assert.equal(frames.length,0,'unchanged DOM must stop scheduling animation frames')}}}

test('stock name resolution stops writing the document title when it is unchanged',()=>{const h=harness(),label=h.node();let title='';Object.defineProperty(h.document,'title',{get:()=>title,set:v=>{title=v;h.mutate()}});h.document.querySelector=()=>label;h.run(bootstrap('resolvedStockNameBootstrap'));h.settle();assert.equal(title,'2330 台積電 K 線');const count=h.changes();h.mutate();h.settle();assert.equal(h.changes(),count+1)});

test('search control refinement settles after one label update',()=>{const h=harness(),open=h.node('開啟5分K來看'),actions={querySelector:s=>s==='.hanstock-fixed-open'?open:null};h.document.getElementById=()=>actions;h.run(bootstrap('refineSearchActionsBootstrap'));h.settle();assert.equal(open.textContent,'開啟 5 分 K');const count=h.changes();h.mutate();h.settle();assert.equal(h.changes(),count+1)});

test('delayed quote loading does not rewrite its placeholder every frame',()=>{const h=harness(),original=h.node('漲跌 0%'),box=h.node();box.previousElementSibling=original;original.querySelector=()=>({});h.document.querySelectorAll=()=>[original];h.document.getElementById=()=>box;h.run(bootstrap('dailyChangeOhlcBootstrap'));h.settle();assert.match(box.innerHTML,/讀取中/);const count=h.changes();h.mutate();h.settle();assert.equal(h.changes(),count+1)});

test('hover quote correction never overwrites another trading date or candle close', async()=>{
  for(const quote of [
    {quoteTime:'2026/09/04',price:399,changePct:4.72,change:18},
    {quoteTime:'2026/09/07',price:399,changePct:4.72,change:18},
    {quoteTime:'2026/09/07',price:403,changePct:1,change:4},
  ]){
    const h=harness(),original=h.node('漲跌幅 +1.00% 漲跌 +4.00 元');let box=h.node(),display='';
    original.parentElement={textContent:'09/07 13:30 開 400 高 410 收 403 低 400 漲跌幅 +1.00%'};
    original.querySelector=()=>({});original.style={setProperty(_key,value){display=value},removeProperty(){display=''}};
    box.previousElementSibling=original;box.remove=()=>{box=null};
    h.document.querySelectorAll=()=>[original];h.document.getElementById=()=>box;
    h.context.Date=class extends Date{constructor(){super('2026-09-08T00:00:00+08:00')}};
    h.context.fetch=async()=>({json:async()=>({quotes:[{code:'2330',...quote}]})});
    h.run(bootstrap('dailyChangeOhlcBootstrap'));
    await new Promise(resolve=>setImmediate(resolve));h.settle();
    if(quote.price===403){assert.equal(display,'none');assert.match(box.innerHTML,/\+1\.00%/);assert.match(box.innerHTML,/\+4\.00 元/)}
    else{assert.equal(display,'');assert.equal(box,null);assert.match(original.textContent,/\+4\.00/)}
  }
});
