import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {signalVisibilityBootstrap} from '../lib/kline-signal-visibility-bootstrap.ts';

function mount(saved){
  const ids=new Map(),frames=[],listeners=new Map(),values=new Map(saved===undefined?[]:[['hanstock.fiveMinuteSignals.visible.v2',saved]]);
  class Element{
    attrs={};children=[];listeners=new Map();textContent='';
    set id(value){this._id=value;ids.set(value,this)}get id(){return this._id}
    setAttribute(k,v){this.attrs[k]=v}getAttribute(k){return this.attrs[k]??null}
    appendChild(child){this.children.push(child)}prepend(child){this.children.unshift(child)}
    addEventListener(k,fn){this.listeners.set(k,fn)}
  }
  const root=new Element(),chart=new Element();
  const window={__hanstockActiveInterval:'5m',addEventListener:(k,fn)=>listeners.set(k,fn),dispatchEvent:event=>listeners.get(event.type)?.(event)};
  const script=signalVisibilityBootstrap().match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  runInNewContext(script,{window,document:{documentElement:root,querySelector:()=>chart,getElementById:id=>ids.get(id),createElement:()=>new Element(),addEventListener(){}},
    localStorage:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)},requestAnimationFrame:fn=>frames.push(fn),
    MutationObserver:class{observe(){}},CustomEvent:class{constructor(type){this.type=type}}});
  const flush=()=>{while(frames.length)frames.shift()()};flush();
  return {root,chart,window,values,flush,listeners,button:ids.get('hanstock-five-minute-signal-toggle')};
}

test('signals start hidden and show/hide preserves chart contents without reloading',()=>{
  const app=mount();let updates=0;app.listeners.set('hanstock-signal-visibility-change',()=>updates++);
  assert.equal(app.chart.children.length,1);assert.equal(app.button.attrs['aria-pressed'],'false');
  assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'hidden');
  app.button.listeners.get('click')({stopPropagation(){}});
  assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'shown');
  assert.equal(app.window.__hanstockSignalMarkersVisible,true);assert.equal(app.values.get('hanstock.fiveMinuteSignals.visible.v2'),'true');
  app.button.listeners.get('click')({stopPropagation(){}});
  assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'hidden');assert.equal(updates,2);
  assert.equal(app.chart.children.length,1,'the chart is not replaced on toggles');
});

test('stored preference is shared across frames and only affects the five-minute period',()=>{
  const app=mount('false');assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'hidden');
  for(const period of ['1m','1d']){
    app.window.__hanstockActiveInterval=period;app.listeners.get('hanstock-period-change')();app.flush();
    assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'shown');assert.equal(app.button.disabled,true);
  }
  app.window.__hanstockActiveInterval='5m';app.listeners.get('hanstock-period-change')();app.flush();
  assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'hidden');assert.equal(app.button.disabled,false);
  app.values.set('hanstock.fiveMinuteSignals.visible.v2','true');app.listeners.get('storage')({key:'hanstock.fiveMinuteSignals.visible.v2'});
  assert.equal(app.root.attrs['data-hanstock-five-minute-signals'],'shown');
});

test('visibility stylesheet targets signal packages only, excluding VWAP, averages and data panels',()=>{
  const css=signalVisibilityBootstrap().match(/<style[^>]*>([\s\S]*?)<\/style>/)[1];
  const hidden=css.slice(0,css.indexOf('{display:none!important}'));
  for(const marker of ['2288','hanstock-signal-overlay','hanstock-signal-leaders','hanstock-signal-tooltip'])assert.ok(hidden.includes(marker));
  for(const study of ['vwap','2158','2436','polyline','path','rect'])assert.ok(!hidden.includes(study));
});
