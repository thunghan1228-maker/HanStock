import assert from 'node:assert/strict';
import test from 'node:test';
import { blackDragonReferenceHighs, blackDragonNewHighPeriods } from '../lib/black-dragon-highs.ts';
import { buildBlackDragonRows, findRecentBlackDragonSignal } from '../lib/black-dragon.ts';
import { BLACK_DRAGON_INTRADAY_MODEL_VERSION, findFirstIntradayBlackDragonSignal } from '../lib/black-dragon-intraday.ts';

test('five-day high needs all five prior sessions, rejects a tie and missing prices', () => {
  const refs = blackDragonReferenceHighs([120, 101, 102, 103, 104].map(high => ({ high })));
  assert.equal(refs[5], 120);
  assert.deepEqual(blackDragonNewHighPeriods(119, refs), []);
  assert.deepEqual(blackDragonNewHighPeriods(120, refs), []);
  assert.deepEqual(blackDragonNewHighPeriods(15.100000381469727, {5:15.1}), [], 'provider floating-point noise is still an equal high');
  assert.deepEqual(blackDragonNewHighPeriods(121, refs), [5]);
  assert.deepEqual(blackDragonNewHighPeriods(121, blackDragonReferenceHighs([{high:120}])), []);
  assert.deepEqual(blackDragonNewHighPeriods(121, blackDragonReferenceHighs([120,101,null,103,104].map(high => ({high})))), []);
  assert.deepEqual(blackDragonNewHighPeriods(121, { 5:null, 20:120, 50:120 }), []);
  assert.deepEqual(blackDragonNewHighPeriods(121, { 5:120, 20:120, 50:120 }), [5,20,50]);
});

const history = Array.from({length:245}, (_, i) => ({
  date: new Date(Date.UTC(2025,0,1+i)).toISOString().slice(0,10),
  open: 100+i/10, high: 101+i/10, close: 100.5+i/10, volume: 1000000,
}));
const candidate = {date:'2026-09-03',open:140,high:141,close:139,volume:2000000};
const scan = bars => findRecentBlackDragonSignal({code:'2330',market:'twse',completedThrough:'2026-09-03',recentSessions:1,bars});

test('regression: high five sessions ago cannot be dropped from the comparison', () => {
  const bars = history.map(bar => ({...bar}));
  bars[bars.length-5].high = 150;
  assert.equal(scan([...bars,candidate]), null);
  bars[bars.length-5].high = 141;
  assert.equal(scan([...bars,candidate]), null);
  bars[bars.length-5].high = 140;
  const signal = scan([...bars,candidate]);
  assert.equal(signal?.date, '2026/09/03');
  assert.equal(signal?.referenceHighs[5], 140);
  assert.ok(signal?.newHighPeriods.includes(5));
  assert.equal(scan([...bars,{...candidate,close:140}]), null, 'flat K is not black K');
  assert.equal(scan([...bars,{...candidate,close:141}]), null, 'red K is not black K');
});

test('persisted labels cannot override the actual high or black-candle check', () => {
  const source = {...candidate,code:'2330',market:'twse',maScore:14,newHighPeriods:[5,20,50]};
  assert.deepEqual(buildBlackDragonRows([source]), [], 'legacy records need price evidence');
  assert.deepEqual(buildBlackDragonRows([{...source,referenceHighs:{5:142}}]), []);
  assert.deepEqual(buildBlackDragonRows([{...source,close:141,referenceHighs:{5:140}}]), []);
  const result = buildBlackDragonRows([{...source,referenceHighs:{5:140,20:145}}]);
  assert.deepEqual(result[0].newHighPeriods,[5], 'recompute periods, do not trust old labels');
});

test('intraday scan also requires the five-day reference even if a longer label could pass', () => {
  const base = {modelVersion:BLACK_DRAGON_INTRADAY_MODEL_VERSION,code:'2330',name:'測試',market:'twse',
    targetDate:'2026-09-04',completedThrough:'2026-09-03',previousClose:100,averageVolume20d:1000000,
    maLiveBaseSums:{5:502,10:1052,20:2102,60:5702,120:10202,240:17402},
    previousMaValues:{5:120,10:115,20:110,60:100,120:90,240:80},referenceHighs:{5:103,20:101}};
  const bars=[{ts:Date.parse('2026-09-04T09:00:00+08:00'),open:100,high:102,low:98,close:99,volume:1000000},
    {ts:Date.parse('2026-09-04T11:00:00+08:00'),open:99,high:102,low:97,close:98,volume:1000000}];
  assert.equal(findFirstIntradayBlackDragonSignal(base,bars,base.targetDate),null);
  const signal=findFirstIntradayBlackDragonSignal({...base,referenceHighs:{5:101,20:101}},bars,base.targetDate);
  assert.deepEqual(signal?.newHighPeriods,[5,20]);
});
