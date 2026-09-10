import test from 'node:test';
import assert from 'node:assert/strict';
import { riverScanBatch } from '../lib/river-scan-batch.ts';
import { selectRankedRiverGroupCandidates } from '../lib/river-group-selection.ts';

test('bounded scans visit every strong and weak group without truncating a group',()=>{
  const order={bull:Array.from({length:10},(_,i)=>'bull'+i),bear:Array.from({length:10},(_,i)=>'bear'+i)};
  let cursor=0;const seen=[];
  for(let i=0;i<10;i++){const batch=riverScanBatch(order,cursor);seen.push(...batch.groups);cursor=batch.nextIndex;}
  assert.equal(cursor,0);assert.deepEqual(new Set(seen),new Set([...order.bull,...order.bear]));
  assert.equal(riverScanBatch(order,100).index,0);
});

test('a partial scan retains the original tenth-group rank and six-stock limit',()=>{
  const order={bull:Array.from({length:10},(_,i)=>'group'+i),bear:[]};
  const members=Array.from({length:12},(_,i)=>({code:String(2000+i),name:'Stock '+i}));
  const groups=new Map([['group9',members]]);
  const score=new Map(members.map(m=>[m.code,80]));
  const changes=new Map(members.map((m,i)=>[m.code,i/2]));
  const eligibility=new Map(members.map(m=>[m.code,{bull:true,bear:false}]));
  const primary=new Map(members.map(m=>[m.code,'group9']));
  const result=selectRankedRiverGroupCandidates(groups,score,order,changes,10,6,eligibility,primary);
  assert.equal(result.bull.length,6);assert.ok(result.bull.every(x=>x.groupRank===10));
  assert.equal(result.bull[0].code,'2011');assert.equal(result.bull.at(-1).code,'2006');
});
