import test from 'node:test';
import assert from 'node:assert/strict';
import { dailyForceHistory } from '../lib/daily-force-history.ts';

const minute = (date, time, net, interval='5m') => ({tradeDate:date,barTime:`${date} ${time}`,netVolume:net,interval,observed:1});
test('daily summaries recover missing days from saved minutes and preserve archive-only days', () => {
  const rows=dailyForceHistory([{tradeDate:'2026-08-31',netVolume:100000,barCount:54,sourceInterval:'5m',lastBarAt:'08/31 13:25'}], [
    minute('2026-09-01','09:00',20000),minute('2026-09-01','09:05',-10000),
    minute('2026-09-02','09:00',0),
    {...minute('2026-09-03','09:00',0),observed:0},
  ]);
  assert.deepEqual(rows.map(row=>[row.date,row.net]), [['2026-08-31',0.1],['2026-09-01',0.01],['2026-09-02',0]]);
});
test('one/five-minute overlap is not summed twice and short data cannot replace complete totals', () => {
  const daily=[{tradeDate:'2026-09-01',netVolume:100000,barCount:54,sourceInterval:'5m',lastBarAt:'09/01 13:25'}];
  const rows=dailyForceHistory(daily,[minute('2026-09-01','09:00',1000,'1m'),minute('2026-09-02','09:00',1000,'1m'),
    minute('2026-09-02','09:00',10000),minute('2026-09-02','09:00',12000)]);
  assert.equal(rows[0].net,0.1);
  assert.equal(rows[1].net,0.012);
  assert.equal(rows[1].barCount,1);
});
