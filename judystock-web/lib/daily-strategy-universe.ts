export type DailyStrategyDirection = "bull" | "bear";

export type DailyStrategyFocusOrder = {
  bull: string[];
  bear: string[];
};

export function passesDailyStrategyDirectionChange(direction: DailyStrategyDirection, changePct: unknown) {
  const change = Number(changePct);
  if (!Number.isFinite(change)) return false;
  return direction === "bull"
    ? change >= 0 && change <= 7
    : change <= 0 && change >= -7;
}

export function matchesCurrentDailyStrategyGroup(
  direction: DailyStrategyDirection,
  groupName: unknown,
  groupRank: unknown,
  focusOrder: DailyStrategyFocusOrder,
) {
  const name = String(groupName ?? "").trim();
  const rank = Number(groupRank);
  if (!name || !Number.isInteger(rank) || rank < 1 || rank > 10) return false;
  const rankedGroups = direction === "bull" ? focusOrder.bull : focusOrder.bear;
  return rankedGroups.length === 10 && rankedGroups[rank - 1] === name;
}
