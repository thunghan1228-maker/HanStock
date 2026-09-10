import {
  averageOfficialFlow,
  calculateCombinedChipScore,
  calculateOfficialChipScore,
  type ChipWeights,
  type MarketRankingSeriesPoint,
} from "./chip-scoring.ts";

export const MAIN_FORCE_CHIP_RANK_MARKER = "前日綜合Top100";
export const MAIN_FORCE_CHIP_RANK_LIMIT = 100;

export type MainForceChipRankDirection = "增加" | "減少";

export type MarketRankingChipSnapshot = {
  dataDate: string;
  rows: Array<{
    code: string;
    name: string;
    market: "twse" | "tpex" | "etf";
    series: MarketRankingSeriesPoint[];
  }>;
};

export type MainForceChipRank = {
  rank: number;
  score: number;
  dataDate: string;
  direction: MainForceChipRankDirection;
};

export type MainForceChipRankings = {
  increasing: ReadonlyMap<string, MainForceChipRank>;
  decreasing: ReadonlyMap<string, MainForceChipRank>;
};

export type MainForceChipRankableSignal = {
  tradeDate: string;
  ticker: string;
  kind: string;
  note: string;
};

export function normalizeChipRankingDate(value: string) {
  const matched = value.trim().match(/^(\d{4})[-/](\d{2})[-/](\d{2})$/);
  return matched ? `${matched[1]}-${matched[2]}-${matched[3]}` : "";
}

export function buildPreviousSessionChipTopRanks(
  snapshot: MarketRankingChipSnapshot,
  weights: ChipWeights,
  signalTradeDate: string,
) {
  const dataDate = normalizeChipRankingDate(snapshot.dataDate);
  const normalizedSignalDate = normalizeChipRankingDate(signalTradeDate);
  if (!dataDate || !normalizedSignalDate || dataDate >= normalizedSignalDate) {
    return { increasing: new Map(), decreasing: new Map() } satisfies MainForceChipRankings;
  }

  const scored = snapshot.rows.flatMap((row) => {
    if (!row.code || !Array.isArray(row.series) || row.series.length === 0) return [];
    const latestDate = normalizeChipRankingDate(row.series[0]?.date ?? "");
    if (latestDate !== dataDate) return [];
    const todayScore = calculateOfficialChipScore(averageOfficialFlow(row.series, 1), weights);
    const fiveDayAverage = calculateOfficialChipScore(averageOfficialFlow(row.series, 5), weights);
    return [{
      ticker: row.code.trim().toUpperCase(),
      score: calculateCombinedChipScore(todayScore, fiveDayAverage),
    }];
  });

  const buildRanking = (direction: MainForceChipRankDirection) => {
    const ranked = scored
      .filter((row) => direction === "增加" ? row.score > 0 : row.score < 0)
      .sort((left, right) => direction === "增加"
        ? right.score - left.score || left.ticker.localeCompare(right.ticker)
        : left.score - right.score || left.ticker.localeCompare(right.ticker))
      .slice(0, MAIN_FORCE_CHIP_RANK_LIMIT);
    return new Map(ranked.map((row, index) => [row.ticker, {
      rank: index + 1,
      score: row.score,
      dataDate,
      direction,
    }]));
  };

  return {
    increasing: buildRanking("增加"),
    decreasing: buildRanking("減少"),
  } satisfies MainForceChipRankings;
}

function signalChipDirection(kind: string): MainForceChipRankDirection | null {
  if ([
    "daytradeEarlyBuy50",
    "fourGateBullish",
    "mainForceTurnBullish",
    "mainForceStrongBullish",
    "triangleNearBreakout",
    "triangleBreakoutPendingVolume",
    "triangleVolumeBreakout",
  ].includes(kind)) return "增加";
  if ([
    "daytradeEarlySell50",
    "intradayExtraLargeSell",
    "intradayExtraLargeBuy",
    "fourGateBearish",
    "mainForceTurnBearish",
    "mainForceStrongBearish",
  ].includes(kind)) return "減少";
  return null;
}

export function stripMainForceChipRank(note: string) {
  return note
    .split("｜")
    .map((part) => part.trim())
    .filter((part) => !part.startsWith(`${MAIN_FORCE_CHIP_RANK_MARKER} `))
    .join("｜");
}

export function extractMainForceChipRank(note: string) {
  const part = note.split("｜").map((value) => value.trim()).find((value) => value.startsWith(`${MAIN_FORCE_CHIP_RANK_MARKER} `));
  if (!part) return null;
  const matched = part.match(/^前日綜合Top100\s+(\d{4}-\d{2}-\d{2})\s+(增加|減少)第\s*(\d+)\s*名\s+([+-]?\d+(?:\.\d+)?)\s*分$/u);
  const legacy = matched ? null : part.match(/^前日綜合Top100\s+(\d{4}-\d{2}-\d{2})\s+第\s*(\d+)\s*名\s+([+-]?\d+(?:\.\d+)?)\s*分$/u);
  if (legacy) {
    const rank = Number(legacy[2]);
    const score = Number(legacy[3]);
    if (!Number.isInteger(rank) || rank < 1 || rank > MAIN_FORCE_CHIP_RANK_LIMIT || !Number.isFinite(score)) return null;
    return {
      dataDate: legacy[1],
      rank,
      score,
      direction: score < 0 ? "減少" : "增加",
      legacy: true,
    } satisfies MainForceChipRank & { legacy: true };
  }
  if (!matched) return null;
  const rank = Number(matched[3]);
  const score = Number(matched[4]);
  if (!Number.isInteger(rank) || rank < 1 || rank > MAIN_FORCE_CHIP_RANK_LIMIT || !Number.isFinite(score)) return null;
  return { dataDate: matched[1], direction: matched[2] as MainForceChipRankDirection, rank, score } satisfies MainForceChipRank;
}

export function annotateMainForceChipRanks<T extends MainForceChipRankableSignal>(
  signals: T[],
  rankings: MainForceChipRankings,
) {
  return signals.map((signal) => {
    const direction = signalChipDirection(signal.kind);
    if (!direction) return signal;
    const existing = extractMainForceChipRank(signal.note);
    if (existing && !("legacy" in existing) && existing.direction === direction) return signal;
    const annotation = (direction === "增加" ? rankings.increasing : rankings.decreasing)
      .get(signal.ticker.trim().toUpperCase());
    if (!annotation) {
      if (!existing) return signal;
      return { ...signal, note: stripMainForceChipRank(signal.note) };
    }
    const score = `${annotation.score > 0 ? "+" : ""}${annotation.score.toFixed(1)}`;
    return {
      ...signal,
      note: `${stripMainForceChipRank(signal.note)}｜${MAIN_FORCE_CHIP_RANK_MARKER} ${annotation.dataDate} ${annotation.direction}第 ${annotation.rank} 名 ${score} 分`,
    };
  });
}
