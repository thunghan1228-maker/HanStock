import test from 'node:test';
import assert from 'node:assert/strict';
import {selectWatchlistCandleWindow} from '../lib/watchlist-candle-window.ts';

test('two trading sessions cross weekends and year-end without dropping Monday opening bars',()=>{
  const rows=['12/30 09:00','12/31 09:00','12/31 13:25','01/04 09:00'].map(date=>({date}));
  assert.deepEqual(selectWatchlistCandleWindow(rows,'watchlist',true),rows.slice(1));
  assert.equal(rows.length,4);
  const partial=[{date:'09/04 09:00'}];
  assert.deepEqual(selectWatchlistCandleWindow(partial,'watchlist',true),partial);
  assert.deepEqual(selectWatchlistCandleWindow([],'watchlist',true),[]);
});

test('full charts and daily periods retain their original history',()=>{
  const rows=['2026-09-02 09:00','2026-09-03 09:00','2026-09-04 09:00'].map(date=>({date}));
  for(const view of [null,'home','grid']) assert.equal(selectWatchlistCandleWindow(rows,view,true),rows);
  assert.equal(selectWatchlistCandleWindow(rows,'watchlist',false),rows);
  assert.deepEqual(selectWatchlistCandleWindow(rows,'watchlist',true),rows.slice(1));
});
