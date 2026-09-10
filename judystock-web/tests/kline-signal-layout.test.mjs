import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutKlineSignals} from '../lib/kline-signal-layout.ts';
import {signalLayoutBootstrap} from '../lib/kline-signal-layout-bootstrap.ts';
import {runInNewContext} from 'node:vm';

const bounds={left:5,right:900,top:20,bottom:460};
const glyph=(id,x,y,extra={})=>({id:String(id),x,y,width:34,height:37,anchorY:y+25,side:'up',...extra});
function verify(input,rect=bounds){const out=layoutKlineSignals(input,rect);assert.deepEqual(out.map(p=>p.id),input.map(p=>p.id));for(let i=0;i<out.length;i++)for(let j=0;j<i;j++){const a=out[i],b=out[j];assert.ok(Math.abs(a.x-b.x)>= (a.width+b.width)/2+3.999||Math.abs(a.y-b.y)>=(a.height+b.height)/2+3.999,`overlap ${a.id}/${b.id}`)}return out}
test('adjacent candles, same-candle stacks and top-clamped counts all avoid one another',()=>{
  const input=Array.from({length:65},(_,i)=>glyph(i,60+Math.floor(i/4)*38,25+i%3*4,{width:i%5===0?43:34,height:i%5===0?47:37}));
  const out=verify(input);assert.ok(out.every(p=>p.y-p.height/2>=bounds.top));
  assert.deepEqual(layoutKlineSignals(input,bounds),out);
});
test('red and green marks share collision protection after resize, pan and zoom',()=>{
  const input=Array.from({length:90},(_,i)=>glyph(i,25+i%30*17,100+i%4*32,{side:i%2?'up':'down',anchorY:150,width:23,height:27}));
  for(const width of [550,900,2400])verify(input.map(p=>({...p,x:p.x*width/550})),{...bounds,right:width});
});
test('oversubscribed views keep every signal above the price panel, never in main-force or volume panels',()=>{
  const out=verify(Array.from({length:80},(_,i)=>glyph(i,55,40)),{left:0,right:110,top:20,bottom:100});
  assert.ok(out.some(p=>p.y<0));assert.ok(out.every(p=>p.y+p.height/2<=100));
});

test('compact signal installer is valid JavaScript and cannot move a mark to a different candle',()=>{
  const script=signalLayoutBootstrap().match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  assert.doesNotThrow(()=>new Function(script));
  assert.ok(script.includes('group.x-size/2'));
  assert.ok(!script.includes('position.x'));
  assert.ok(!script.includes('pack(items'));
});

test('SVG adapter merges duplicate native/overlay events, anchors groups and opens full details',()=>{
  const frames=[],listeners=new Map(),identity={inverse(){return this}},markers=[],candles=[];
  class Element {
    constructor(attrs={}){this.attrs={...attrs};this.style={};this.children=[];this.textContent='';this.events={}}
    get id(){return this.attrs.id} set id(value){this.attrs.id=value}
    getAttribute(k){return this.attrs[k]??null} hasAttribute(k){return k in this.attrs}
    setAttribute(k,v){this.attrs[k]=String(v)} removeAttribute(k){delete this.attrs[k]}
    getCTM(){return identity} getBoundingClientRect(){return{left:0,top:0,bottom:460,width:900,height:460}}
    get offsetWidth(){return 180} get offsetHeight(){return 100}
    appendChild(n){n.parentElement=this;this.children.push(n)} prepend(n){n.parentElement=this;this.children.unshift(n)}
    remove(){this.parentElement.children=this.parentElement.children.filter(n=>n!==this)}
    closest(selector){if(selector.includes('2288'))return this.native;return null}
    getBBox(){return this.box}
    querySelector(selector){if(selector==='line')return this.line;if(selector==='title')return null;return null}
    cloneNode(){const n=new Element(this.attrs);n.box=this.box;return n}
    addEventListener(k,fn){this.events[k]=fn}
  }
  const svg=new Element(),layer=new Element(),separator=new Element({x1:0,x2:900,y1:460}),controls=new Element(),body=new Element();layer.parentElement=svg;
  svg.createSVGPoint=()=>({x:0,y:0,matrixTransform(){return{x:this.x,y:this.y}}});
  svg.querySelectorAll=selector=>selector.includes('2158')?candles:markers;
  svg.querySelector=selector=>selector.includes('2428')?separator:layer.children.find(n=>'#'+n.id===selector)||null;
  for(let i=0;i<2;i++){const candle=new Element({'data-hanstock-bar-date':`09/07 09:0${i*5}`});candle.parentElement=layer;candle.line=new Element({x1:600+i*12,x2:600+i*12,y1:150,y2:180});candles.push(candle)}
  const kinds=['crossUp905','crossUp20ma','ma520Up'];
  for(const source of ['native','overlay'])for(let i=0;i<6;i++){
    const date=candles[i%2].getAttribute('data-hanstock-bar-date'),kind=kinds[Math.floor(i/2)],node=new Element({'data-hanstock-signal-kind':kind,'data-hanstock-signal-label':kind,'data-hanstock-signal-time':date});
    node.parentElement=layer;if(source==='native'){node.native=new Element({'data-hanstock-native-signal-date':date,'data-hanstock-native-signal-side':'up'});node.parentElement=node.native}
    node.box={x:100,y:30,width:34,height:37};markers.push(node);
  }
  const win={__hanstockActiveInterval:'5m',innerWidth:900,innerHeight:500,addEventListener:(k,v)=>listeners.set(k,v)};
  runInNewContext(signalLayoutBootstrap().match(/<script[^>]*>([\s\S]*?)<\/script>/)[1],{
    document:{documentElement:{},body,querySelectorAll:()=>[svg],createElementNS:()=>new Element(),createElement:()=>new Element(),addEventListener(){},getElementById:id=>id==='hanstock-five-minute-signal-controls'?controls:controls.children.find(n=>n.id===id)},
    window:win,requestAnimationFrame:fn=>frames.push(fn),MutationObserver:class{observe(){}},
  });
  frames.shift()();
  const output=()=>layer.children.find(n=>n.id==='hanstock-candle-signals');
  assert.equal(win.__hanstockSignalLayoutCount,6,'six real events, not twelve duplicated layers');
  assert.equal(output().children.length,2,'one group per candle/side');
  assert.deepEqual(output().children.map(n=>n.getAttribute('data-anchor-x')),['600','612']);
  assert.ok(output().children.every(n=>n.getAttribute('data-signal-count')==='3'));
  assert.ok(markers.every(n=>n.getAttribute('data-hanstock-condensed-source')==='true'));
  const same=output();listeners.get('resize')();frames.shift()();assert.equal(output(),same,'unchanged frames do not rebuild');
  const mark=output().children[0],hit=mark.children.find(n=>n.getAttribute('data-signal-hit-area')==='true');
  assert.ok(Number(hit.getAttribute('width'))>Number(mark.getAttribute('data-marker-size')),'the edge around the visual mark is interactive');
  mark.events.pointerenter({pointerType:'mouse',buttons:0,clientX:895,clientY:480});
  const tip=body.children.find(n=>n.id==='hanstock-candle-signal-hover');
  assert.equal(tip.hidden,false,'hover details open synchronously without a browser tooltip delay');
  assert.equal(controls.children.length,0,'hover never expands the controls or shifts the chart');
  for(const kind of kinds)assert.ok(tip.textContent.includes(kind));
  assert.ok(parseFloat(tip.style.left)+tip.offsetWidth<=win.innerWidth);
  assert.ok(parseFloat(tip.style.top)+tip.offsetHeight<=win.innerHeight);
  mark.events.pointerleave();assert.equal(tip.hidden,true);
  mark.events.pointerenter({pointerType:'touch',buttons:0});assert.equal(tip.hidden,true,'touch retains click-to-open behavior');
  mark.events.pointermove({pointerType:'mouse',buttons:1,clientX:500,clientY:200});assert.equal(tip.hidden,true,'chart dragging must not open tooltips');
  mark.events.focus();assert.equal(tip.hidden,false,'keyboard focus exposes the same details');
  mark.events.keydown({key:'Escape'});assert.equal(tip.hidden,true);
  output().children[0].events.click({stopPropagation(){}});
  const panel=controls.children[0];assert.ok(panel.textContent.includes('09/07 09:00'));
  for(const kind of kinds)assert.ok(panel.textContent.includes(kind));
  candles[0].line.setAttribute('x1',630);candles[0].line.setAttribute('x2',630);
  listeners.get('hanstock-candles-updated')();frames.shift()();
  assert.deepEqual(output().children.map(n=>n.getAttribute('data-anchor-x')),['612','630']);
  assert.equal(svg.hasAttribute('viewBox'),false,'never resize price or main-force chart');
  // Simulate the separately-polled market overlay arriving after ordinary signals.
  const addDown = (kind,label) => {
    const node=new Element({'data-hanstock-signal-kind':kind,'data-hanstock-signal-label':label,'data-hanstock-signal-time':'09/07 09:00'});
    node.parentElement=layer;node.box={x:100,y:30,width:34,height:37};markers.push(node);return node;
  };
  addDown('short12','12空'); addDown('crossDown20ma','跌破20MA');
  listeners.get('hanstock-candles-updated')();frames.shift()();
  const countBox=()=>output().children.find(n=>n.getAttribute('aria-label')==='09/07 09:00，12空；跌破20MA');
  assert.equal(countBox().getAttribute('data-signal-count'),'2');
  const standalone=addDown('break15kLow','第2次破三五');
  for (const low of [180,455]) {
    candles[0].line.setAttribute('y2',low);
    listeners.get('hanstock-candles-updated')();frames.shift()();
    const regular=countBox(), separate=output().children.find(n=>n.getAttribute('aria-label')==='09/07 09:00，第2次破三五');
    assert.equal(regular.getAttribute('data-signal-count'),'2');
    assert.equal(separate.getAttribute('data-signal-count'),'1');
    assert.equal(separate.getAttribute('data-anchor-x'),regular.getAttribute('data-anchor-x'));
    const area=n=>n.children.find(c=>c.getAttribute('data-signal-hit-area')==='true');
    assert.ok(Number(area(separate).getAttribute('y'))>=Number(area(regular).getAttribute('y'))+Number(area(regular).getAttribute('height'))-0.001,'independent hit areas must not overlap, even at the bottom boundary');
    assert.ok(Number(area(separate).getAttribute('y'))+Number(area(separate).getAttribute('height'))<=458);
    separate.events.click({stopPropagation(){}});
    assert.ok(controls.children[0].textContent.includes('第2次破三五'));
    assert.ok(!controls.children[0].textContent.includes('12空'));
  }
  markers.splice(markers.indexOf(standalone),1);
  listeners.get('hanstock-candles-updated')();frames.shift()();
  assert.equal(countBox().getAttribute('data-signal-count'),'2');
});
