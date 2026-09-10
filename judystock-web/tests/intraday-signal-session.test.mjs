import assert from "node:assert/strict";
import test from "node:test";
import {
  closedTradingDatesFromTwseSchedule,
  INTRADAY_SIGNAL_CUTOVER_MINUTE,
  resolveIntradaySignalCutoverDate,
  resolveIntradaySignalDisplayDate,
} from "../lib/intraday-signal-session.ts";

test("keeps Friday's intraday signals visible for the full weekend", () => {
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-08-21"], new Date("2026-08-22T14:00:00+08:00")),
    "2026-08-21",
  );
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-08-21"], new Date("2026-08-23T20:00:00+08:00")),
    "2026-08-21",
  );
});

test("switches from the previous session to today's preparation state at 08:45 Taipei time", () => {
  assert.equal(INTRADAY_SIGNAL_CUTOVER_MINUTE, 525);
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-08-21"], new Date("2026-08-24T08:44:59+08:00")),
    "2026-08-21",
  );
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-08-21"], new Date("2026-08-24T08:45:00+08:00")),
    "2026-08-24",
  );
});

test("never switches to the new session before 08:45 even if an early row already exists", () => {
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-08-24", "2026-08-21"], new Date("2026-08-24T08:30:00+08:00")),
    "2026-08-21",
  );
});

test("keeps the previous trading session through exchange holidays", () => {
  const closed = closedTradingDatesFromTwseSchedule([
    { Name: "農曆春節前最後交易日", Date: "1150211" },
    { Name: "市場無交易，僅辦理結算交割作業", Date: "1150212" },
    { Name: "市場無交易，僅辦理結算交割作業", Date: "1150213" },
    { Name: "農曆除夕及春節", Date: "1150216" },
    { Name: "農曆除夕及春節", Date: "1150217" },
    { Name: "農曆除夕及春節", Date: "1150218" },
    { Name: "農曆除夕及春節", Date: "1150219" },
    { Name: "農曆除夕及春節", Date: "1150220" },
    { Name: "農曆春節後開始交易日", Date: "1150223" },
  ]);
  assert.equal(closed.has("2026-02-11"), false);
  assert.equal(closed.has("2026-02-23"), false);
  assert.equal(resolveIntradaySignalCutoverDate(new Date("2026-02-20T12:00:00+08:00"), closed), "2026-02-11");
  assert.equal(
    resolveIntradaySignalDisplayDate(["2026-02-20", "2026-02-11"], new Date("2026-02-20T12:00:00+08:00"), closed),
    "2026-02-11",
  );
  assert.equal(resolveIntradaySignalCutoverDate(new Date("2026-02-23T08:44:59+08:00"), closed), "2026-02-11");
  assert.equal(resolveIntradaySignalCutoverDate(new Date("2026-02-23T08:45:00+08:00"), closed), "2026-02-23");
});
