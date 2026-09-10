import assert from "node:assert/strict";
import test from "node:test";
import {
  CHIP_SERVER_REFRESH_INTERVAL_MS,
  chipAutomaticUpdateMessage,
  shouldTriggerChipServerRefresh,
  taipeiMarketClock,
} from "../lib/chip-auto-refresh.ts";

const taipeiTime = (isoWithoutZone) => Date.parse(`${isoWithoutZone}+08:00`);

test("keeps the previous trading day explicit and correct during market hours", () => {
  const now = taipeiTime("2026-08-27T10:06:57");
  assert.deepEqual(taipeiMarketClock(now), { date: "2026-08-27", weekday: "Thu", minutes: 606 });
  const message = chipAutomaticUpdateMessage("2026/08/26", true, now);
  assert.equal(message.label, "盤中沿用上一交易日");
  assert.match(message.detail, /08\/26 是正確的/);
  assert.match(message.detail, /15:10 後/);
});

test("starts server-side retries after 15:10 without relying on the chip page", () => {
  const now = taipeiTime("2026-08-27T15:12:00");
  assert.equal(shouldTriggerChipServerRefresh(0, now), true);
  assert.equal(shouldTriggerChipServerRefresh(now - CHIP_SERVER_REFRESH_INTERVAL_MS + 1, now), false);
  assert.equal(shouldTriggerChipServerRefresh(now - CHIP_SERVER_REFRESH_INTERVAL_MS, now), true);
  const message = chipAutomaticUpdateMessage("2026/08/26", false, now);
  assert.equal(message.label, "盤後資料整理中");
  assert.match(message.detail, /每 5 分鐘主動重試/);
});

test("marks the current official trading day as completed", () => {
  const now = taipeiTime("2026-08-27T16:30:00");
  const message = chipAutomaticUpdateMessage("2026/08/27", true, now);
  assert.equal(message.label, "今日盤後資料已更新");
});
