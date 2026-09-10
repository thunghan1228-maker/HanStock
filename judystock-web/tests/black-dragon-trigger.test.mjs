import test from 'node:test';
import assert from 'node:assert/strict';
import { findFirstIntradayBlackDragonSignal, hasVerifiedIntradayBlackDragonEvidence, BLACK_DRAGON_INTRADAY_MODEL_VERSION } from '../lib/black-dragon-intraday.ts';
import { mergeBlackDragonCandleWindows } from '../lib/black-dragon-candles.ts';

const day='2026-09-07', at=time=>Date.parse(`${day}T${time}:00+08:00`);
const base={modelVersion:BLACK_DRAGON_INTRADAY_MODEL_VERSION,code:'3532',name:'台勝科',market:'twse',targetDate:day,completedThrough:'2026-09-04',previousClose:399,averageVolume20d:1000000,
  maLiveBaseSums:{5:2000,10:3900,20:7200,60:18000,120:30000,240:48000},previousMaValues:{5:400,10:390,20:360,60:300,120:250,240:200},referenceHighs:{5:408.5}};
const bar=(time,open,high,close,volume=100000)=>({ts:at(time),open,high,close,low:Math.min(open,close),volume});
const opening=bar('09:00',401,402,396.5);

test('3532: a 09:55 high cannot qualify the 13:15 candle below the five-day high',()=>{
  const bars=[opening,bar('09:55',411,418.5,416),bar('11:00',410.5,413,412),bar('13:15',405,405,400)];
  assert.equal(findFirstIntradayBlackDragonSignal(base,bars,day),null);
  const match=findFirstIntradayBlackDragonSignal(base,[...bars,bar('13:20',400,409,400)],day);
  assert.equal(match.barTs,at('13:20'));
  assert.equal(match.signalHigh,409);
  assert.equal(match.referenceHigh5,408.5);
  assert.equal(match.sessionOpen,401);
  assert.equal(match.openingBarTs,at('09:00'));
  assert.ok(hasVerifiedIntradayBlackDragonEvidence(match));
  assert.equal(hasVerifiedIntradayBlackDragonEvidence({...match,signalHigh:408.5}),false);
  assert.equal(hasVerifiedIntradayBlackDragonEvidence({...match,price:402}),false);
});

test('a truncated noon feed or missing OHLC cannot invent the daily opening price',()=>{
  assert.equal(findFirstIntradayBlackDragonSignal(base,[bar('12:20',414,416,414),bar('12:25',414,415,412)],day),null);
  const valid=[opening,bar('11:00',401,409,400)];
  assert.equal(findFirstIntradayBlackDragonSignal(base,[{...opening,open:undefined},valid[1]],day),null);
  assert.equal(findFirstIntradayBlackDragonSignal({...base,sessionOpen:390},valid,day),null);
  assert.equal(findFirstIntradayBlackDragonSignal({...base,targetDate:'2026-09-04'},valid,day),null);
  assert.equal(findFirstIntradayBlackDragonSignal({...base,maLiveBaseSums:{...base.maLiveBaseSums,5:null}},valid,day),null);
  assert.ok(findFirstIntradayBlackDragonSignal({...base,sessionOpen:401.000001},valid,day));
});

test('persisted morning observations merge with current afternoon bars without counting duplicates',()=>{
  const saved=[{...opening,date:'09/07 09:00'},bar('11:00',401,409,402),{date:'09/04 09:00',open:500,high:510,low:490,close:500,volume:1}];
  const incoming=[bar('11:00',401,409,400)];
  const merged=mergeBlackDragonCandleWindows(saved,incoming,day);
  assert.equal(merged.length,2);
  assert.equal(merged[0].open,401);
  assert.equal(merged[1].close,400);
  const match=findFirstIntradayBlackDragonSignal(base,merged,day);
  assert.equal(match.cumulativeVolume,200000);
  assert.equal(mergeBlackDragonCandleWindows([],incoming,day).length,1);
  assert.equal(findFirstIntradayBlackDragonSignal(base,mergeBlackDragonCandleWindows([],incoming,day),day),null);
});
