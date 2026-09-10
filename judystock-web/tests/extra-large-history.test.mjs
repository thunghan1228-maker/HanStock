import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { hasQualifiedExtraLargeTriggerForce, qualifyExtraLargeSignalByTriggerBars } from "../lib/intraday-extra-large-sell.ts";

// Exercise the API's shared fast-snapshot/history repair path with the actual
// production helper and a bounded, in-memory substitute for its D1 calls.
const source = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
const start = source.indexOf("async function qualifyStoredExtraLargeSignals(");
const end = source.indexOf("async function storedPayload(", start);
assert.ok(start >= 0 && end > start);
const compiled = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;

function repairWith(records, saved, fail = false) {
  return new Function("hasQualifiedExtraLargeTriggerForce", "qualifyExtraLargeSignalByTriggerBars", "readIntradayForceForTickers", "appendEarlySellChipRankAnnotations", "taipeiStoredBarTimestamp", `${compiled}\nreturn qualifyStoredExtraLargeSignals;`)(
    hasQualifiedExtraLargeTriggerForce,
    qualifyExtraLargeSignalByTriggerBars,
    async () => { if (fail) throw new Error("unavailable"); return records; },
    async (rows) => saved.push(...rows),
    (time) => Date.parse(time.replace(" ", "T") + ":00+08:00"),
  );
}

test("snapshot/history repair excludes old conflicting rows, preserves other signals, and persists only verified directions", async () => {
  const base = { tradeDate: "2026-09-04", barTs: Date.parse("2026-09-04T13:29:00+08:00"), kind: "intradayExtraLargeSell", note: "前日大單淨額 1.43 億｜族群同步 化學 跌幅第 10 名 0.00%" };
  const conflict = { ...base, ticker: "1727" };
  const sameDirection = { ...base, ticker: "2609" };
  const other = { ...base, ticker: "2330", kind: "mainForceZero" };
  const records = [
    { ticker: "1727", barTime: "2026-09-04 13:29", buyAmount: 955152200, sellAmount: 176699200 },
    { ticker: "2609", barTime: "2026-09-04 13:29", buyAmount: 10000000, sellAmount: 176699200 },
  ];
  const saved = [];
  const rows = await repairWith(records, saved)([conflict, sameDirection, other]);
  assert.deepEqual(rows.map((row) => row.ticker).sort(), ["2330", "2609"]);
  assert.deepEqual(saved.map((row) => row.ticker), ["2609"]);
  assert.ok(hasQualifiedExtraLargeTriggerForce(saved[0]));
  assert.match(saved[0].note, /族群同步 化學/);
  assert.doesNotMatch(saved[0].note, /觸發當時盤中大戶力/);
  assert.equal(rows.find((row) => row.ticker === "2330"), other);
});

test("a transient history-data failure never revives an unverified sell signal", async () => {
  const unknown = { ticker: "1727", tradeDate: "2026-09-04", barTs: 1788499740000, kind: "intradayExtraLargeSell", note: "舊訊號" };
  const confirmed = { ...unknown, ticker: "2609", note: "觸發當時大單淨額 -1.00 億｜觸發當時盤中大戶力 -10.0%" };
  const saved = [];
  assert.deepEqual(await repairWith([], saved, true)([unknown, confirmed]), [confirmed]);
  assert.deepEqual(saved, []);
});
