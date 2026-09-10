import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import ts from "typescript";
import * as rankings from "../lib/main-force-group-ranks.ts";
import {normalizeWatchlists, applyWatchlistOperations, diffWatchlists, cloneDefaultWatchlists} from "../lib/watchlists.ts";
import {resolveIntradaySignalCutoverDate} from "../lib/intraday-signal-session.ts";

const exports = {};
runInNewContext(ts.transpileModule(readFileSync(new URL("../lib/after-hours-watchlist.ts", import.meta.url), "utf8"),
  {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
  {exports, require:()=>rankings, Map, Date, Number});
const {afterHoursWatchlistStocks} = exports;
const row = (ticker, time, kind="instantLargeBuy", force="+63.3") => ({ticker, name:ticker==="2327"?"國巨":"浩鼎", kind,
  tradeDate:time.slice(0,10), barTs:Date.parse(time+"+08:00"), price:562,
  note:`同秒 1 筆｜族群同步 被動元件 漲幅第 4 名｜觸發當時盤中大戶力 ${force}%`});

test("imports only real 14:30 trade signals, both directions, once per ticker", () => {
  const result = afterHoursWatchlistStocks([
    row("2327","2026-09-04T14:30:00"), row("2327","2026-09-04T14:30:01"),
    row("4174","2026-09-04T14:30:00","instantLargeSell","-15.2"),
    row("2330","2026-09-04T13:30:00"), row("2331","2026-09-04T14:31:00"),
    row("2332","2026-09-04T14:30:00","riverBull"), row("2333","2026-09-03T14:30:00"),
  ],"2026-09-04");
  assert.deepEqual(Array.from(result, stock=>stock.ticker), ["2327","4174"]);
  assert.equal(result[0].forcePct,63.3);
  assert.equal(result[1].forcePct,-15.2);
  assert.equal(result[0].groupRank.rank,4);
  assert.equal(result[0].signalTradeDate,"2026-09-04");
});

test("zero force remains zero, missing force remains missing, and date mismatches are excluded", () => {
  const zero = row("2327","2026-09-04T14:30:00","instantLargeBuy","0.0");
  const missing = {...row("4174","2026-09-04T14:30:00"),note:"同秒 1 筆"};
  const wrongDate = {...zero,ticker:"2317",barTs:Date.parse("2026-09-03T14:30:00+08:00")};
  const result = afterHoursWatchlistStocks([zero,missing,wrongDate],"2026-09-04");
  assert.equal(result.length,2);
  assert.equal(result.find(stock=>stock.ticker==="2327").forcePct,0);
  assert.equal(result.find(stock=>stock.ticker==="4174").forcePct,null);
});

test("six existing folders migrate without loss and automatic-folder refreshes are not uploaded as user edits", () => {
  const original=cloneDefaultWatchlists().slice(0,6);
  original[1].name="我的研究";
  const upgraded=normalizeWatchlists(original);
  assert.equal(upgraded.length,7);
  assert.deepEqual(upgraded.slice(0,6),original);
  assert.equal(upgraded[6].name,"盤後交易股票");
  const current=structuredClone(upgraded);
  current[6].stocks=afterHoursWatchlistStocks([row("2327","2026-09-04T14:30:00")],"2026-09-04");
  assert.deepEqual(diffWatchlists(upgraded,current),[]);
  const changed=applyWatchlistOperations(current,[{type:"removeStock",folderId:"watchlist-7",ticker:"2327"},
    {type:"renameFolder",folderId:"watchlist-1",name:"長期觀察"}]);
  assert.equal(changed[6].stocks[0].ticker,"2327");
  assert.equal(changed[0].name,"長期觀察");
});

test("Friday's automatic folder survives the weekend and switches on Monday 14:30, with holidays deferred", () => {
  const session=time=>resolveIntradaySignalCutoverDate(new Date(Date.parse(time)-345*60_000));
  assert.equal(session("2026-09-05T12:00:00+08:00"),"2026-09-04");
  assert.equal(session("2026-09-07T14:29:59+08:00"),"2026-09-04");
  assert.equal(session("2026-09-07T14:30:00+08:00"),"2026-09-07");
  assert.equal(resolveIntradaySignalCutoverDate(new Date(Date.parse("2026-09-07T14:30:00+08:00")-345*60_000),new Set(["2026-09-07"])),"2026-09-04");
});
