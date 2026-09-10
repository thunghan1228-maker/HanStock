import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {timedKeyedSingleFlight} from '../lib/timed-single-flight.ts';
import {withMarketRequestScope} from '../lib/market-request-scope.ts';
import * as force from '../lib/intraday-large-force.ts';
import {webcrypto} from 'node:crypto';

const calendar={loadTwseClosedTradingDates:async()=>new Set()};
function officialResponse(url,codes){
  const u=new URL(url),tpex=u.hostname.includes('tpex');
  const selected=tpex?codes.slice(268):codes.slice(0,268);
  return Response.json({date:u.searchParams.get('date').replaceAll('/',''),tables:[{data:selected.map(code=>tpex
    ?[code,'name',100,String((2000-Number(code))/100)]
    :[code,'name',0,0,0,0,0,0,100,'+',String((2000-Number(code))/100)])}]});
}

function compile(path, dependencies, context={}) {
  const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const mod={exports:{}};
  vm.runInNewContext(code,{exports:mod.exports,require:name=>name.includes('aj-official-quotes')?{
    readAjOfficialQuotes:async()=>null,saveAjOfficialQuotes:async()=>{},
  }:dependencies(name),URL,Response,AbortSignal,Date,Intl,TextDecoder,...context});
  return mod.exports;
}

test('AJ uses fast stock quotes, retains three dated sessions and recovers after a canceled request',async()=>{
  const groups=new Map(Array.from({length:67},(_,i)=>[`group${i}`,Array.from({length:8},(_,j)=>({code:String(1000+i*8+j),name:`stock${i}-${j}`}))]));
  const codes=[...groups.values()].flat().map(x=>x.code);
  let liveCalls=0, officialCalls=0;
  const live={loadConfiguredGroups:async()=>groups,loadSharedLatestQuotes:async()=>{
    if(++liveCalls===1)return new Promise(()=>{});
    return {byCode:new Map(codes.map(code=>[code,{code,date:'2026/09/07',changePct:(Number(code)-1000)/100}]))};
  }};
  class Clock extends Date{static now(){return Date.parse('2026-09-07T13:40:00+08:00');}}
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?live:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{
    Date:Clock,fetch:async url=>{
      assert.ok(!String(url).includes('groupStrength'));
      officialCalls++;
      return officialResponse(url,codes);
    },
  });
  const request=()=>new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-07&tickers=1000,1535');
  withMarketRequestScope(()=>{void route.GET(request());});
  await new Promise(r=>setTimeout(r,5));
  const response=await withMarketRequestScope(()=>route.GET(request()));
  const body=await response.json();
  assert.equal(response.status,200);
  assert.deepEqual(body.previousDates,['2026-09-04','2026-09-03','2026-09-02']);
  assert.equal(body.rows[0].qualifyingGroup,'group0');
  assert.equal(body.rows[0].currentRank,67);
  assert.equal(body.rows[1].bullishQualifyingGroup,'group66');
  assert.equal(body.rows[1].bullishCurrentRank,1);
  const prior=officialCalls;
  assert.equal((await withMarketRequestScope(()=>route.GET(request()))).status,200);
  assert.equal(officialCalls,prior);
  assert.equal(liveCalls,2);
});

test('AJ retains dated closing quotes after 14:30 and rejects a fresh response containing yesterday prices',async()=>{
  const codes=Array.from({length:536},(_,i)=>String(1000+i));
  const groups=new Map(Array.from({length:67},(_,i)=>[`group${i}`,codes.slice(i*8,i*8+8).map(code=>({code,name:code}))]));
  let quoteDate='2026/09/04'; const officialDates=[];
  class Clock extends Date{static now(){return Date.parse('2026-09-07T14:35:00+08:00');}}
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?{
    loadConfiguredGroups:async()=>groups,
    loadSharedLatestQuotes:async()=>({fetchedAt:new Clock(Clock.now()).toISOString(),byCode:new Map(codes.map(code=>[code,{code,date:quoteDate,changePct:1}]))}),
  }:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{
    Date:Clock,fetch:async url=>{officialDates.push(new URL(url).searchParams.get('date').replaceAll('/',''));return officialResponse(url,codes);},
  });
  const request=()=>new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-07&tickers=1000');
  const stale=await route.GET(request()); assert.equal(stale.status,502);
  assert.match((await stale.json()).error,/current-quote-date-coverage/);
  quoteDate='2026/09/07';
  const result=await route.GET(request()); assert.equal(result.status,200);
  const body=await result.json(); assert.equal(body.live,false);
  assert.deepEqual(body.previousDates,['2026-09-04','2026-09-03','2026-09-02']);
  assert.ok(!officialDates.includes('20260907'),'closing quotes do not require an unavailable current-day report');
});

test('saved category counts survive a background collector error; an unready snapshot stays unknown',()=>{
  const page=readFileSync(new URL('../app/page.tsx',import.meta.url),'utf8');
  const expression=page.match(/const coreCount = \(value: number\) => ([^;]+);/)[1];
  for(const [ready,error,expected] of [[true,true,341],[true,false,341],[false,true,'…']]){
    assert.equal(vm.runInNewContext(`(${expression})`,{value:341,signalSnapshotReady:ready,signalCollection:{error}}),expected);
  }
});

test('AJ does not list unverified stocks when live quote coverage fails',async()=>{
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?{
    loadConfiguredGroups:async()=>new Map(),loadSharedLatestQuotes:async()=>{throw Error('quote-coverage-0');},
  }:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{fetch:async()=>Response.json({tables:[]})});
  const response=await route.GET(new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-07&tickers=2330'));
  assert.equal(response.status,502);
  assert.deepEqual((await response.json()).rows,[]);
});

test('AJ retries a failed open day without substituting an older date or caching incomplete coverage',async()=>{
  const codes=Array.from({length:536},(_,i)=>String(1000+i));
  const groups=new Map(Array.from({length:67},(_,i)=>[`group${i}`,codes.slice(i*8,i*8+8).map(code=>({code,name:code}))]));
  const live={loadConfiguredGroups:async()=>groups,loadSharedLatestQuotes:async()=>({byCode:new Map(codes.map(code=>[code,{code,date:'2026/09/07',changePct:1}]))})};
  let failed=false;const dates=[];
  class Clock extends Date{static now(){return Date.parse('2026-09-07T13:40:00+08:00');}}
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?live:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{
    Date:Clock,fetch:async url=>{
      const u=new URL(url),date=u.searchParams.get('date').replaceAll('/','');dates.push(date);
      if(date==='20260902'&&u.hostname.includes('tpex')&&!failed){failed=true;return Response.json({date,tables:[]});}
      return officialResponse(url,codes);
    },
  });
  const request=()=>new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-07&tickers=1000');
  assert.equal((await withMarketRequestScope(()=>route.GET(request()))).status,502);
  assert.ok(!dates.includes('20260901'),'an unavailable trading day must never become a holiday');
  const response=await withMarketRequestScope(()=>route.GET(request()));
  assert.equal(response.status,200);
  assert.deepEqual((await response.json()).previousDates,['2026-09-04','2026-09-03','2026-09-02']);
  assert.equal(dates.filter(x=>x==='20260904').length,2,'completed dates remain cached during a retry');
});

test('a scan revision backfills once after the close, respects active leases, and resumes a failed batch',async()=>{
  let stored={tradeDate:'2026-09-07',status:'completed',nextIndex:32,processed:32,available:32,signalCount:5,total:32,cycle:14,updatedAt:Date.now()};
  const d1={prepare(sql){return {bind(...args){return {
    async first(){return stored?{configJson:JSON.stringify(stored)}:null;},
    async run(){
      let changes=0;
      if(sql.startsWith('INSERT')){
        if(stored?.status!=='running'||stored.updatedAt<=args[4]){stored=JSON.parse(args[1]);changes=1;}
      }else if(sql.startsWith('UPDATE')&&stored?.leaseId===args[4]){stored=JSON.parse(args[0]);changes=1;}
      return {meta:{changes}};
    },
  };}};}};
  const scan=compile('../db/intraday-large-force-scan.ts',()=>({}),{crypto:webcrypto,__HANSTOCK_DB:d1});
  const options={tradeDate:'2026-09-07',total:32,restartCompleted:false};
  const first=await scan.acquireIntradayLargeForceScanBatch(options);
  assert.equal(first.acquired,true);assert.equal(first.progress.nextIndex,0);assert.equal(first.progress.cycle,15);
  assert.equal((await scan.acquireIntradayLargeForceScanBatch(options)).acquired,false,'another viewer must respect the lease');
  await scan.failIntradayLargeForceScanBatch(first.progress,'history timeout');
  const retry=await scan.acquireIntradayLargeForceScanBatch(options);
  assert.equal(retry.progress.nextIndex,0,'failed history must retry the same stocks');
  await scan.finishIntradayLargeForceScanBatch({progress:retry.progress,processed:16,available:16,signalCount:2});
  const second=await scan.acquireIntradayLargeForceScanBatch(options);
  assert.equal(second.progress.nextIndex,16);
  await scan.finishIntradayLargeForceScanBatch({progress:second.progress,processed:16,available:16,signalCount:2});
  assert.equal((await scan.acquireIntradayLargeForceScanBatch(options)).acquired,false,'the corrected closing sweep must not loop forever');
});

test('AJ uses the dated official CSV when TPEx redirects cloud JSON requests',async()=>{
  const codes=Array.from({length:536},(_,i)=>String(1000+i));
  const groups=new Map(Array.from({length:67},(_,i)=>[`group${i}`,codes.slice(i*8,i*8+8).map(code=>({code,name:code}))]));
  let csvCalls=0;
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?{
    loadConfiguredGroups:async()=>groups,loadSharedLatestQuotes:async()=>({byCode:new Map(codes.map(code=>[code,{code,date:'2026/09/07',changePct:1}]))}),
  }:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{
    fetch:async (url,init)=>{
      const u=new URL(url);
      if(!u.hostname.includes('tpex'))return officialResponse(url,codes);
      assert.equal(init.redirect,'manual');
      if(u.searchParams.get('response')==='json')return new Response(null,{status:302,headers:{Location:'/errors'}});
      csvCalls++;
      const date=u.searchParams.get('date').replace('2026','115');
      return new Response(`TPEx\r\nDate:${date}\r\n`+codes.slice(268).map(code=>`"${code}","Stock","1,100","+10"`).join('\r\n'));
    },
  });
  const response=await route.GET(new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-04&tickers=1000'));
  assert.equal(response.status,200);
  assert.deepEqual((await response.json()).previousDates,['2026-09-03','2026-09-02','2026-09-01']);
  assert.equal(csvCalls,4);
});

test('AJ validates the date of mirrored official data when direct cloud exports fail',async()=>{
  const codes=Array.from({length:536},(_,i)=>String(1000+i));
  const groups=new Map(Array.from({length:67},(_,i)=>[`group${i}`,codes.slice(i*8,i*8+8).map(code=>({code,name:code}))]));
  let wrongDate=true;
  const route=compile('../app/api/intraday-large-force-aj/route.ts',name=>name.includes('live-group-quotes')?{
    loadConfiguredGroups:async()=>groups,loadSharedLatestQuotes:async()=>({byCode:new Map(codes.map(code=>[code,{code,date:'2026/09/07',changePct:1}]))}),
  }:name.includes('timed-single-flight')?{timedKeyedSingleFlight}:name.includes('intraday-signal-session')?calendar:force,{
    fetch:async url=>{
      const u=new URL(url);
      if(u.hostname.includes('r.jina.ai')){
        const source=String(url).replace('https://r.jina.ai/','').replaceAll('%26','&');
        const data=await officialResponse(source,codes).json();
        if(wrongDate)data.date='20260831';
        return new Response(`Title: Official report\nMarkdown Content:\n${JSON.stringify(data)}`);
      }
      if(u.hostname.includes('tpex'))return new Response(null,{status:302});
      return officialResponse(url,codes);
    },
  });
  const request=()=>new Request('https://site.test/api/intraday-large-force-aj?tradeDate=2026-09-04&tickers=1000');
  const failed=await route.GET(request());assert.equal(failed.status,502);
  assert.match((await failed.json()).error,/date-mismatch/);
  wrongDate=false;
  assert.equal((await route.GET(request())).status,200);
});
