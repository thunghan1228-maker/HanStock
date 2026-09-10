import assert from 'node:assert/strict';
import test from 'node:test';
import { groupKlineSignals } from '../lib/kline-signal-groups.ts';

test('deduplicate two renderers and changed sequence labels without losing distinct signals', () => {
  const base={date:'09/07 09:05',kind:'crossUp905',label:'過905高',side:'up',x:500,anchorY:150};
  const groups=groupKlineSignals([
    {...base,id:'n1',source:'native',note:'1'},
    {...base,id:'o1',source:'overlay',note:'2',label:'第2次過905高'},
    {...base,id:'o2',source:'overlay',kind:'crossUp20ma'},
    {...base,id:'o3',source:'overlay',date:'09/07 09:10',x:512},
    {...base,id:'o4',source:'overlay',kind:'ma20turn',note:'up'},
    {...base,id:'o5',source:'overlay',kind:'ma20turn',note:'down',side:'down'},
  ]);
  assert.equal(groups.reduce((n,g)=>n+g.items.length,0),5);
  assert.equal(groups.length,3);
  assert.deepEqual(groups.find(g=>g.x===500&&g.side==='up').items.map(i=>i.id),['o1','o2','o4']);
  assert.equal(groups.find(g=>g.date==='09/07 09:10').x,512);
});

test('break35 is independent while other signals on the same candle and side still merge', () => {
  const base = { date:'09/08 11:00', side:'down', x:500, anchorY:180, source:'overlay' };
  const groups = groupKlineSignals([
    { ...base, id:'break', kind:'break15kLow', label:'第2次破三五' },
    { ...base, id:'12', kind:'short12', label:'12空' },
    { ...base, id:'20', kind:'crossDown20ma', label:'跌破20MA' },
  ]);
  assert.deepEqual(groups.map(g=>g.items.map(i=>i.id)), [['12','20'],['break']]);
  assert.ok(groups.every(g=>g.x===500 && g.date===base.date && g.side==='down'));
});

test('late break35 overlay updates never change other counts or duplicate the same event', () => {
  const base = { date:'09/08 11:00', side:'down', x:500, anchorY:180, source:'overlay' };
  const ordinary = [{...base,id:'12',kind:'short12',label:'12空'}, {...base,id:'20',kind:'crossDown20ma',label:'跌破20MA'}];
  const native = {...base,id:'native',source:'native',kind:'break15kLow',label:'破三五',note:'1'};
  const overlay = {...native,id:'overlay',source:'overlay',label:'第2次破三五',note:'2'};
  for (const changes of [[], [native], [native,overlay], [overlay], []]) {
    const groups = groupKlineSignals([...changes,...ordinary]);
    assert.deepEqual(groups[0].items.map(i=>i.id), ['12','20']);
    assert.equal(groups.length, changes.length ? 2 : 1);
    if(changes.length) assert.equal(groups[1].items.length,1);
    if(changes.includes(overlay)) assert.equal(groups[1].items[0].id,'overlay');
  }
  const next = {...overlay,id:'next',date:'09/08 11:05',x:512};
  assert.equal(groupKlineSignals([...ordinary,overlay,next]).length,3);
});
