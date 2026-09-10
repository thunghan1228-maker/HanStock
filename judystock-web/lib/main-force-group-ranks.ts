export type MainForceGroupRankingRow = {
  rank: number;
  name: string;
  change?: string;
};

export type MainForceGroupRankings = {
  strong?: MainForceGroupRankingRow[];
  weak?: MainForceGroupRankingRow[];
};

export type MainForceRankableSignal = {
  ticker: string;
  kind: string;
  note: string;
};

export type MainForceGroupRank = {
  group: string;
  rank: number;
  direction: "漲幅" | "跌幅";
  change: string | null;
};

export const MAIN_FORCE_GROUP_RANK_MARKER = "族群同步";
export const MAIN_FORCE_GROUP_RANK_LIMIT = 10;
export const INSTANT_LARGE_GROUP_RANK_LIMIT = 20;

function signalGroupLimit(kind: string) {
  return kind === "instantLargeBuy" || kind === "instantLargeSell"
    ? INSTANT_LARGE_GROUP_RANK_LIMIT
    : MAIN_FORCE_GROUP_RANK_LIMIT;
}

function signalSide(kind: string) {
  if ([
    "daytradeEarlyBuy50",
    "fourGateBullish",
    "mainForceTurnBullish",
    "mainForceStrongBullish",
    "fiveMinuteOnePlusTwoLong",
    "riverBull",
    "triangleNearBreakout",
    "triangleBreakoutPendingVolume",
    "triangleVolumeBreakout",
  ].includes(kind)) return { ranking: "strong" as const, direction: "漲幅" as const };
  if ([
    "daytradeEarlySell50",
    "fourGateBearish",
    "mainForceTurnBearish",
    "mainForceStrongBearish",
    "fiveMinuteTwelveShort",
    "riverBear",
  ].includes(kind)) return { ranking: "weak" as const, direction: "跌幅" as const };
  return null;
}

function signalSides(kind: string) {
  // 特大買／賣單也必須與族群方向一致，不能因為剛好落在反向前十而顯示。
  if (kind === "intradayExtraLargeBuy") return [{ ranking: "strong" as const, direction: "漲幅" as const }];
  if (kind === "intradayExtraLargeSell") return [{ ranking: "weak" as const, direction: "跌幅" as const }];
  if (kind === "instantLargeBuy") return [{ ranking: "strong" as const, direction: "漲幅" as const }];
  if (kind === "instantLargeSell") return [{ ranking: "weak" as const, direction: "跌幅" as const }];
  const side = signalSide(kind);
  return side ? [side] : [];
}

export function hasMatchingTopGroup(
  signal: Pick<MainForceRankableSignal, "ticker" | "kind">,
  groupsByTicker: ReadonlyMap<string, string[]>,
  rankings: MainForceGroupRankings | null | undefined,
) {
  return Boolean(findMainForceGroupRank(signal, groupsByTicker.get(signal.ticker.toUpperCase()) ?? [], rankings));
}

/**
 * 永久保存的盤中訊號已在觸發當下寫入族群前十標記。當即時族群排行
 * 暫時無法取得時，仍可用這個不可變的觸發快照顯示訊號；但必須再次
 * 驗證訊號方向，避免沿用舊版曾寫入的反向特大單標記。
 */
export function hasCapturedMatchingTopGroup(
  signal: Pick<MainForceRankableSignal, "kind" | "note">,
) {
  const captured = extractMainForceGroupRank(signal.note);
  if (!captured) return false;
  return captured.rank <= signalGroupLimit(signal.kind)
    && signalSides(signal.kind).some((side) => side.direction === captured.direction);
}

/**
 * 舊版族群瞬間大單已由後端前後 20 族群監看器篩過，但早期保存格式
 * 尚未寫入「族群同步」標記。這類歷史列可沿用原始監看結果；只要列中
 * 已出現族群標記，就仍必須通過目前的方向與前 20 名驗證，避免錯標回流。
 */
export function hasCapturedOrLegacyInstantLargeGroup(
  signal: Pick<MainForceRankableSignal, "kind" | "note">,
) {
  if (signal.kind !== "instantLargeBuy" && signal.kind !== "instantLargeSell") return false;
  if (!signal.note.includes(MAIN_FORCE_GROUP_RANK_MARKER)) return true;
  return hasCapturedMatchingTopGroup(signal);
}

export function findMainForceGroupRank(
  signal: Pick<MainForceRankableSignal, "kind">,
  groups: string[],
  rankings: MainForceGroupRankings | null | undefined,
) {
  const sides = signalSides(signal.kind);
  if (sides.length === 0 || groups.length === 0) return null;
  const groupNames = new Set(groups.filter((group) => group && group !== "未分類"));
  const matched = sides.flatMap((side) => (rankings?.[side.ranking] ?? [])
    .filter((row) => row.rank >= 1 && row.rank <= signalGroupLimit(signal.kind) && groupNames.has(row.name))
    .map((row) => ({ row, side })))
    .sort((left, right) => left.row.rank - right.row.rank)[0];
  if (!matched) return null;
  return {
    group: matched.row.name,
    rank: matched.row.rank,
    direction: matched.side.direction,
    change: matched.row.change ?? null,
  } satisfies MainForceGroupRank;
}

export function findWeakestGroupRank(
  groups: string[],
  rankings: MainForceGroupRankings | null | undefined,
) {
  if (groups.length === 0) return null;
  const groupNames = new Set(groups.filter((group) => group && group !== "未分類"));
  const matched = (rankings?.weak ?? [])
    .filter((row) => row.rank >= 1 && groupNames.has(row.name))
    .sort((left, right) => left.rank - right.rank)[0];
  if (!matched) return null;
  return {
    group: matched.name,
    rank: matched.rank,
    direction: "跌幅",
    change: matched.change ?? null,
  } satisfies MainForceGroupRank;
}

/**
 * 均線空原始候選只可保留在正式弱勢前十族群內的股票。前十族群
 * 各自的前三弱股由 focus-ranking 同源資料另外併入；未隸屬正式
 * 67 族群的股票絕不可為了湊足數量而補入。
 */
export function selectRiverBearSignalsWithFallback<T extends MainForceRankableSignal>(
  signals: T[],
  groupsByTicker: ReadonlyMap<string, string[]>,
  rankings: MainForceGroupRankings | null | undefined,
) {
  return signals.filter((signal) => hasMatchingTopGroup(signal, groupsByTicker, rankings));
}

export function stripMainForceGroupRank(note: string) {
  return note
    .split("｜")
    .map((part) => part.trim())
    .filter((part) => !part.startsWith(`${MAIN_FORCE_GROUP_RANK_MARKER} `))
    .join("｜");
}

export function extractMainForceGroupRank(note: string) {
  const part = note.split("｜").map((value) => value.trim()).find((value) => value.startsWith(`${MAIN_FORCE_GROUP_RANK_MARKER} `));
  if (!part) return null;
  const matched = part.match(/^族群同步\s+(.+?)\s+(漲幅|跌幅)第\s*(\d+)\s*名(?:\s+([+-]?\d+(?:\.\d+)?%))?$/u);
  if (!matched) return null;
  const rank = Number(matched[3]);
  if (!Number.isInteger(rank) || rank < 1 || rank > INSTANT_LARGE_GROUP_RANK_LIMIT) return null;
  return {
    group: matched[1],
    direction: matched[2] as MainForceGroupRank["direction"],
    rank,
    change: matched[4] ?? null,
  } satisfies MainForceGroupRank;
}

export function annotateMainForceGroupRanks<T extends MainForceRankableSignal>(
  signals: T[],
  groupsByTicker: ReadonlyMap<string, string[]>,
  rankings: MainForceGroupRankings | null | undefined,
) {
  return signals.map((signal) => {
    // 特大買／賣單先前允許雙向族群標籤；本版改為嚴格同向，所以必須重算。
    const refreshDirectionalExtraLarge = signal.kind === "intradayExtraLargeSell" || signal.kind === "intradayExtraLargeBuy";
    if (!refreshDirectionalExtraLarge && extractMainForceGroupRank(signal.note)) return signal;
    const cleanSignal = refreshDirectionalExtraLarge ? { ...signal, note: stripMainForceGroupRank(signal.note) } : signal;
    const annotation = findMainForceGroupRank(cleanSignal, groupsByTicker.get(cleanSignal.ticker.toUpperCase()) ?? [], rankings);
    if (!annotation) return cleanSignal;
    const change = annotation.change ? ` ${annotation.change}` : "";
    return {
      ...cleanSignal,
      note: `${stripMainForceGroupRank(cleanSignal.note)}｜${MAIN_FORCE_GROUP_RANK_MARKER} ${annotation.group} ${annotation.direction}第 ${annotation.rank} 名${change}`,
    };
  });
}
