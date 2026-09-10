import assert from "node:assert/strict";
import test from "node:test";
import { selectRankedRiverGroupCandidates, selectRiverGroupCandidates } from "../lib/river-group-selection.ts";

test("six-MA group selection returns ten groups and three stocks per group on each side", () => {
  const groups = new Map(Array.from({ length: 67 }, (_, groupIndex) => [
    `族群${groupIndex + 1}`,
    Array.from({ length: 5 }, (_, stockIndex) => ({ code: `${groupIndex + 1}${stockIndex}`.padStart(4, "0"), name: `股票${groupIndex + 1}-${stockIndex + 1}` })),
  ]));
  const scores = new Map([...groups.values()].flatMap((members, groupIndex) => members.map((member, stockIndex) => [member.code, groupIndex * 10 + stockIndex])));
  const result = selectRiverGroupCandidates(groups, scores);
  assert.equal(result.coveredGroups, 67);
  assert.equal(result.bull.length, 30);
  assert.equal(result.bear.length, 30);
  assert.equal(new Set(result.bull.map((row) => row.groupName)).size, 10);
  assert.equal(new Set(result.bear.map((row) => row.groupName)).size, 10);
  assert.deepEqual(result.bear.slice(0, 3).map((row) => row.stockRank), [1, 2, 3]);
  assert.ok(result.bear.every((row) => row.direction === "bear"));
  assert.ok(result.bull.every((row) => row.direction === "bull"));
});

test("overlapping group memberships are replaced by the next ranked stock instead of shrinking the thirty-stock list", () => {
  const groups = new Map(Array.from({ length: 67 }, (_, groupIndex) => [
    `族群${groupIndex + 1}`,
    [
      { code: "9999", name: "重複股" },
      ...Array.from({ length: 4 }, (_, stockIndex) => ({ code: `${groupIndex + 1}${stockIndex}`.padStart(4, "0"), name: `股票${groupIndex + 1}-${stockIndex + 1}` })),
    ],
  ]));
  const scores = new Map([...groups.values()].flatMap((members, groupIndex) => members.map((member, stockIndex) => [member.code, member.code === "9999" ? -1000 : groupIndex * 10 + stockIndex])));
  const result = selectRiverGroupCandidates(groups, scores);
  assert.equal(result.bear.length, 30);
  assert.equal(new Set(result.bear.map((row) => row.code)).size, 30);
});

test("uses the existing top and bottom ten main groups as the only parent universe", () => {
  const groups = new Map(Array.from({ length: 67 }, (_, groupIndex) => [
    `族群${groupIndex + 1}`,
    Array.from({ length: 4 }, (_, stockIndex) => ({ code: `${groupIndex + 1}${stockIndex}`.padStart(4, "0"), name: `股票${groupIndex + 1}-${stockIndex + 1}` })),
  ]));
  const scores = new Map([...groups.values()].flatMap((members, groupIndex) => members.map((member, stockIndex) => [member.code, groupIndex < 10 ? 80 + stockIndex : groupIndex >= 57 ? 20 - stockIndex : 50])));
  const bullNames = Array.from({ length: 10 }, (_, index) => `族群${index + 1}`);
  const bearNames = Array.from({ length: 10 }, (_, index) => `族群${67 - index}`);
  const changes = new Map([...groups.values()].flatMap((members) => members.map((member) => [member.code, 0])));
  const result = selectRankedRiverGroupCandidates(groups, scores, { bull: bullNames, bear: bearNames }, changes);
  assert.equal(result.bull.length, 40);
  assert.equal(result.bear.length, 40);
  assert.ok(result.bull.every((row) => bullNames.includes(row.groupName)));
  assert.ok(result.bear.every((row) => bearNames.includes(row.groupName)));
  assert.deepEqual(result.bull.slice(0, 4).map((row) => row.stockRank), [1, 2, 3, 4]);
  assert.deepEqual(result.bear.slice(0, 4).map((row) => row.stockRank), [1, 2, 3, 4]);
});

test("keeps the 0-to-7 percent gates and ranks each group by individual change", () => {
  const groups = new Map(Array.from({ length: 67 }, (_, groupIndex) => [
    `族群${groupIndex + 1}`,
    [
      { code: `${groupIndex}1`.padStart(4, "0"), name: "零漲跌" },
      { code: `${groupIndex}2`.padStart(4, "0"), name: "多方區間" },
      { code: `${groupIndex}3`.padStart(4, "0"), name: "空方區間" },
      { code: `${groupIndex}4`.padStart(4, "0"), name: "多方超七" },
      { code: `${groupIndex}5`.padStart(4, "0"), name: "空方超七" },
      { code: `${groupIndex}6`.padStart(4, "0"), name: "多方區間二" },
      { code: `${groupIndex}7`.padStart(4, "0"), name: "空方區間二" },
    ],
  ]));
  const scores = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, [50, 70, 30, 99, 1, 60, 40][index]])));
  const changes = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, [0, 2.5, -3.5, 7.1, -7.1, 6.8, -6.8][index]])));
  const bullNames = Array.from({ length: 10 }, (_, index) => `族群${index + 1}`);
  const result = selectRankedRiverGroupCandidates(groups, scores, { bull: bullNames, bear: bullNames }, changes);
  assert.equal(result.bull.length, 30);
  assert.equal(result.bear.length, 30);
  assert.ok(result.bull.every((row) => Number(row.changePct) >= 0 && Number(row.changePct) <= 7));
  assert.ok(result.bear.every((row) => Number(row.changePct) <= 0 && Number(row.changePct) >= -7));
  assert.deepEqual(result.bull.slice(0, 3).map((row) => row.changePct), [6.8, 2.5, 0]);
  assert.deepEqual(result.bear.slice(0, 3).map((row) => row.changePct), [-6.8, -3.5, 0]);
});

test("takes up to six per group and leaves the total short instead of filling outside the group", () => {
  const groupNames = Array.from({ length: 10 }, (_, index) => `族群${index + 1}`);
  const groups = new Map(groupNames.map((groupName, groupIndex) => [
    groupName,
    Array.from({ length: groupIndex < 5 ? 1 : 10 }, (_, stockIndex) => ({
      code: `${groupIndex}${stockIndex}`.padStart(4, "0"),
      name: `${groupName}-${stockIndex + 1}`,
    })),
  ]));
  const scores = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, 100 - index])));
  const changes = new Map([...groups.values()].flatMap((members) => members.map((member) => [member.code, 2])));
  const result = selectRankedRiverGroupCandidates(groups, scores, { bull: groupNames, bear: [] }, changes);
  assert.equal(result.bull.length, 35);
  assert.equal(new Set(result.bull.map((row) => row.code)).size, 35);
  assert.ok(result.bull.every((row) => groupNames.includes(row.groupName) && Number(row.changePct) >= 0 && Number(row.changePct) <= 7));
  assert.ok(result.bull.every((row) => row.stockRank <= 6));
  assert.ok([...new Set(result.bull.map((row) => row.groupName))].every((groupName) => result.bull.filter((row) => row.groupName === groupName).length <= 6));
});

test("applies the signal-time five-minute MA20 eligibility before taking each group's top six", () => {
  const groupNames = Array.from({ length: 10 }, (_, index) => `族群${index + 1}`);
  const groups = new Map(groupNames.map((groupName, groupIndex) => [
    groupName,
    Array.from({ length: 7 }, (_, stockIndex) => ({
      code: `${groupIndex}${stockIndex}`.padStart(4, "0"),
      name: `${groupName}-${stockIndex + 1}`,
    })),
  ]));
  const scores = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, 100 - index * 10])));
  const changes = new Map([...groups.values()].flatMap((members) => members.map((member) => [member.code, 0])));
  const eligibility = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, { bull: index > 0, bear: index < 6 }])));
  const result = selectRankedRiverGroupCandidates(groups, scores, { bull: groupNames, bear: groupNames }, changes, 10, 6, eligibility);
  assert.equal(result.bull.length, 60);
  assert.equal(result.bear.length, 60);
  assert.ok(result.bull.every((row) => eligibility.get(row.code)?.bull));
  assert.ok(result.bear.every((row) => eligibility.get(row.code)?.bear));
});

test("excludes overlapping or uncatalogued stocks whose official primary group is not the ranked group", () => {
  const groupNames = Array.from({ length: 10 }, (_, index) => `族群${index + 1}`);
  const groups = new Map(groupNames.map((groupName, groupIndex) => [
    groupName,
    [
      { code: groupIndex === 0 ? "5439" : `${groupIndex}0`.padStart(4, "0"), name: "重疊股" },
      { code: groupIndex === 0 ? "8155" : `${groupIndex}1`.padStart(4, "0"), name: "無正式族群股" },
      ...Array.from({ length: 6 }, (_, stockIndex) => ({
        code: `${groupIndex}${stockIndex + 2}`.padStart(4, "0"),
        name: `${groupName}-${stockIndex + 1}`,
      })),
    ],
  ]));
  const scores = new Map([...groups.values()].flatMap((members) => members.map((member, index) => [member.code, 100 - index])));
  const changes = new Map([...groups.values()].flatMap((members) => members.map((member) => [member.code, 2])));
  const eligibility = new Map([...groups.values()].flatMap((members) => members.map((member) => [member.code, { bull: true, bear: true }])));
  const primaryGroups = new Map([...groups].flatMap(([groupName, members]) => members.slice(2).map((member) => [member.code, groupName])));
  primaryGroups.set("5439", "小電組");

  const result = selectRankedRiverGroupCandidates(groups, scores, { bull: groupNames, bear: [] }, changes, 10, 6, eligibility, primaryGroups);
  assert.equal(result.bull.length, 60);
  assert.equal(result.bull.some((row) => row.code === "5439"), false);
  assert.equal(result.bull.some((row) => row.code === "8155"), false);
  assert.ok(result.bull.every((row) => primaryGroups.get(row.code) === row.groupName));
});
