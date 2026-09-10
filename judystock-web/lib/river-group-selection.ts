export type RiverGroupMember = {
  code: string;
  name: string;
  score: number;
  changePct?: number;
};

export type RiverGroupSelection = RiverGroupMember & {
  direction: "bull" | "bear";
  groupName: string;
  groupRank: number;
  stockRank: number;
  groupScore: number;
};

export function selectRiverGroupCandidates(
  groups: Map<string, Array<{ code: string; name: string }>>,
  scores: Map<string, number>,
  groupLimit = 10,
  stockLimit = 3,
) {
  const rankedGroups = [...groups].flatMap(([groupName, members]) => {
    const scored = members.flatMap((member) => {
      const score = scores.get(member.code);
      return Number.isFinite(score) ? [{ ...member, score: Number(score) }] : [];
    });
    if (!scored.length) return [];
    return [{
      groupName,
      groupScore: scored.reduce((sum, member) => sum + member.score, 0) / scored.length,
      members: scored,
    }];
  });

  const selectSide = (direction: "bull" | "bear") => {
    const multiplier = direction === "bull" ? -1 : 1;
    const selectedCodes = new Set<string>();
    return [...rankedGroups]
      .sort((left, right) => multiplier * (left.groupScore - right.groupScore))
      .slice(0, groupLimit)
      .flatMap((group, groupIndex) => {
        const members = [...group.members]
          .sort((left, right) => multiplier * (left.score - right.score))
          .filter((member) => !selectedCodes.has(member.code))
          .slice(0, stockLimit);
        members.forEach((member) => selectedCodes.add(member.code));
        return members.map((member, stockIndex) => ({
          ...member,
          direction,
          groupName: group.groupName,
          groupRank: groupIndex + 1,
          stockRank: stockIndex + 1,
          groupScore: Math.round(group.groupScore * 10) / 10,
        }));
      });
  };

  return { bull: selectSide("bull"), bear: selectSide("bear"), coveredGroups: rankedGroups.length };
}

export function selectRankedRiverGroupCandidates(
  groups: Map<string, Array<{ code: string; name: string }>>,
  scores: Map<string, number>,
  rankedGroupNames: { bull: string[]; bear: string[] },
  changes = new Map<string, number>(),
  groupLimit = 10,
  stockLimit = 6,
  intradayEligibility = new Map<string, { bull: boolean; bear: boolean }>(),
  primaryGroups = new Map<string, string>(),
) {
  const selectSide = (direction: "bull" | "bear", names: string[]) => {
    const selectedCodes = new Set<string>();
    const rankedGroups = names.slice(0, groupLimit).map((groupName, groupIndex) => {
      const scored = (groups.get(groupName) ?? []).flatMap((member) => {
        if (primaryGroups.size > 0 && primaryGroups.get(member.code) !== groupName) return [];
        const score = scores.get(member.code);
        const changePct = changes.get(member.code);
        if (!Number.isFinite(score)) return [];
        if (!Number.isFinite(changePct)) return [];
        if (intradayEligibility.size > 0 && intradayEligibility.get(member.code)?.[direction] !== true) return [];
        if (direction === "bull" && (Number(changePct) < 0 || Number(changePct) > 7)) return [];
        if (direction === "bear" && (Number(changePct) > 0 || Number(changePct) < -7)) return [];
        return [{ ...member, score: Number(score), changePct: Number.isFinite(changePct) ? Number(changePct) : undefined }];
      }).sort((left, right) => {
        const changeOrder = direction === "bull"
          ? Number(right.changePct) - Number(left.changePct)
          : Number(left.changePct) - Number(right.changePct);
        if (changeOrder !== 0) return changeOrder;
        return direction === "bull" ? right.score - left.score : left.score - right.score;
      });
      const groupScore = scored.length ? scored.reduce((sum, member) => sum + member.score, 0) / scored.length : 50;
      return { groupName, groupIndex, groupScore, scored };
    });
    const selected = rankedGroups.flatMap(({ groupName, groupIndex, groupScore, scored }) => {
      const members = scored
        .filter((member) => !selectedCodes.has(member.code))
        .slice(0, stockLimit);
      members.forEach((member) => selectedCodes.add(member.code));
      return members.map((member, stockIndex) => ({
        ...member,
        direction,
        groupName,
        groupRank: groupIndex + 1,
        stockRank: stockIndex + 1,
        groupScore: Math.round(groupScore * 10) / 10,
      }));
    });

    // 前十強／後十弱主族群內，每群依個股漲跌幅最多取六檔。
    // 正負 7% 濾網內不足時不跨群、也不拿區間外股票補滿。
    return selected;
  };

  return {
    bull: selectSide("bull", rankedGroupNames.bull),
    bear: selectSide("bear", rankedGroupNames.bear),
    coveredGroups: groups.size,
  };
}
