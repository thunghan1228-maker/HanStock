import assert from "node:assert/strict";
import test from "node:test";
import { matchesPersistedOfficialPrimaryGroup } from "../lib/persisted-signal-validation.ts";

test("keeps a previously validated signal when the primary-group map is temporarily unavailable", () => {
  assert.equal(matchesPersistedOfficialPrimaryGroup(undefined, "PCB"), true);
});

test("keeps a previously validated signal when the official primary group still matches", () => {
  assert.equal(matchesPersistedOfficialPrimaryGroup("PCB", "PCB"), true);
});

test("rejects a stored signal when an available official primary group conflicts", () => {
  assert.equal(matchesPersistedOfficialPrimaryGroup("小電組", "PCB"), false);
});
