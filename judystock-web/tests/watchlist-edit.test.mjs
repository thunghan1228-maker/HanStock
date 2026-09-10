import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import ts from "typescript";
import * as watchlists from "../lib/watchlists.ts";
const {cloneDefaultWatchlists, applyWatchlistOperations, normalizeWatchlists, visibleAfterHoursStocks} = watchlists;
const move = {type:"moveStock",folderId:"watchlist-1",targetFolderId:"watchlist-2",ticker:"2344"};

test("moves one stock with metadata, survives reload and repeated synchronization", () => {
  const before = cloneDefaultWatchlists();
  before[0].stocks[0].forcePct = -12.3;
  const after = applyWatchlistOperations(before,[move]);
  assert.equal(after[0].stocks.some(s=>s.ticker==="2344"),false);
  assert.deepEqual(after[1].stocks,[before[0].stocks[0]]);
  assert.deepEqual(applyWatchlistOperations(normalizeWatchlists(JSON.parse(JSON.stringify(after))),[move]),after);
  assert.equal(before[0].stocks.length,4);
});

test("invalid and full destinations never remove a source, duplicate target keeps one copy", () => {
  const before = cloneDefaultWatchlists();
  before[1].stocks = Array.from({length:300},(_,i)=>({...before[0].stocks[0],ticker:String(5000+i)}));
  assert.deepEqual(applyWatchlistOperations(before,[move]),before);
  for (const targetFolderId of ["watchlist-1","watchlist-7","missing"]) {
    assert.deepEqual(applyWatchlistOperations(before,[{...move,targetFolderId}]),before);
  }
  before[1].stocks[0] = before[0].stocks[0];
  const after = applyWatchlistOperations(before,[move]);
  assert.equal(after[0].stocks.length,3);
  assert.equal(after[1].stocks.length,300);
  assert.equal(after[1].stocks.filter(s=>s.ticker==="2344").length,1);
});

test("after-hours edits survive refresh, are personal, and allow next trading day's import", () => {
  const before = cloneDefaultWatchlists();
  const stock = {...before[0].stocks[0],ticker:"2327",name:"國巨",signalTradeDate:"2026-09-04",forcePct:63.3};
  const op = {...move,folderId:"watchlist-7",ticker:stock.ticker,stock,tradeDate:stock.signalTradeDate};
  const after = applyWatchlistOperations(before,[op]);
  assert.deepEqual(after[1].stocks,[stock]);
  assert.deepEqual(visibleAfterHoursStocks(after[6],[stock]),[]);
  assert.deepEqual(visibleAfterHoursStocks(before[6],[stock]),[stock]);
  assert.equal(visibleAfterHoursStocks(after[6],[{...stock,signalTradeDate:"2026-09-07"}]).length,1);
  assert.deepEqual(applyWatchlistOperations(after,[op]),after);
  const removed = applyWatchlistOperations(before,[{type:"removeStock",folderId:"watchlist-7",ticker:stock.ticker,tradeDate:stock.signalTradeDate}]);
  assert.deepEqual(visibleAfterHoursStocks(normalizeWatchlists(removed)[6],[stock]),[]);
  assert.deepEqual(applyWatchlistOperations(before,[{...op,tradeDate:"invalid"}]),before);
});

test("D1 operation validation and persistence keep a move atomic on a concurrent full destination", async () => {
  let folders = cloneDefaultWatchlists();
  let revision = 1, collided = false;
  const d1 = {prepare(sql) { let args=[]; return {
    bind(...values){args=values;return this;},
    async first(){return {userEmail:"test@example.test",payload:JSON.stringify(folders),revision,
      primaryDeviceId:"test-device",updatedByDeviceId:"test-device",updatedByDeviceKind:"desktop",createdAt:1,updatedAt:1};},
    async run(){
      if(!sql.startsWith("UPDATE")) return {meta:{changes:0}};
      if(!collided){collided=true;revision++;folders[1].stocks=Array.from({length:300},(_,i)=>({...folders[0].stocks[0],ticker:String(5000+i)}));return {meta:{changes:0}};}
      if(args.at(-1)!==revision)return {meta:{changes:0}};
      folders=JSON.parse(args[0]);revision=args[1];return {meta:{changes:1}};
    }
  };}};
  const exports={};
  runInNewContext(ts.transpileModule(readFileSync(new URL("../db/watchlists.ts",import.meta.url),"utf8"),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
    {exports,require:()=>watchlists,__HANSTOCK_DB:d1,Date,JSON,Number,Set});
  const result=await exports.updateWatchlistState({email:"test@example.test",operations:[move],deviceId:"test-device",deviceKind:"desktop"});
  assert.equal(collided,true);
  assert.equal(result.watchlists[0].stocks.length,4);
  assert.equal(result.watchlists[1].stocks.length,300);
  folders[1].stocks=[];
  const moved=await exports.updateWatchlistState({email:"test@example.test",operations:[move],deviceId:"test-device",deviceKind:"ipad"});
  assert.equal(moved.watchlists[0].stocks.length,3);
  assert.equal(moved.watchlists[1].stocks[0].ticker,"2344");
});
