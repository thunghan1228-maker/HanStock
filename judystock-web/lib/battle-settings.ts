export type ChipWeightKey = "main" | "foreign" | "trust" | "etf" | "dealer" | "hedge";

export type BattleRuntimeSettings = {
  chipWeights: Record<ChipWeightKey, number>;
  daytradeThresholds: {
    turnoverAmount: number;
    netLargeAmount: number;
    netBuyRate: number;
    suspicionScore: number;
    strongDayChangePct: number;
    strongLateBuyConcentration: number;
  };
  chipAutoRefreshMinutes: number;
};

export const DEFAULT_BATTLE_SETTINGS: BattleRuntimeSettings = {
  chipWeights: {
    main: 35,
    foreign: 25,
    trust: 18,
    etf: 12,
    dealer: 8,
    hedge: 2,
  },
  daytradeThresholds: {
    turnoverAmount: 200_000_000,
    netLargeAmount: 50_000_000,
    netBuyRate: 5,
    suspicionScore: 60,
    strongDayChangePct: 2,
    strongLateBuyConcentration: 5,
  },
  chipAutoRefreshMinutes: 10,
};

const finite = (value: unknown, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(maximum, Math.max(minimum, value));

export function normalizeBattleSettings(value: unknown): BattleRuntimeSettings {
  const input = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const weights = input.chipWeights && typeof input.chipWeights === "object"
    ? input.chipWeights as Record<string, unknown>
    : {};
  const thresholds = input.daytradeThresholds && typeof input.daytradeThresholds === "object"
    ? input.daytradeThresholds as Record<string, unknown>
    : {};

  return {
    chipWeights: {
      main: Math.round(clamp(finite(weights.main, DEFAULT_BATTLE_SETTINGS.chipWeights.main), 0, 100)),
      foreign: Math.round(clamp(finite(weights.foreign, DEFAULT_BATTLE_SETTINGS.chipWeights.foreign), 0, 100)),
      trust: Math.round(clamp(finite(weights.trust, DEFAULT_BATTLE_SETTINGS.chipWeights.trust), 0, 100)),
      etf: Math.round(clamp(finite(weights.etf, DEFAULT_BATTLE_SETTINGS.chipWeights.etf), 0, 100)),
      dealer: Math.round(clamp(finite(weights.dealer, DEFAULT_BATTLE_SETTINGS.chipWeights.dealer), 0, 100)),
      hedge: Math.round(clamp(finite(weights.hedge, DEFAULT_BATTLE_SETTINGS.chipWeights.hedge), 0, 100)),
    },
    daytradeThresholds: {
      turnoverAmount: Math.round(clamp(finite(thresholds.turnoverAmount, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.turnoverAmount), 0, 10_000_000_000)),
      netLargeAmount: Math.round(clamp(finite(thresholds.netLargeAmount, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.netLargeAmount), 0, 10_000_000_000)),
      netBuyRate: clamp(finite(thresholds.netBuyRate, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.netBuyRate), 0, 100),
      suspicionScore: clamp(finite(thresholds.suspicionScore, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.suspicionScore), 0, 100),
      strongDayChangePct: clamp(finite(thresholds.strongDayChangePct, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.strongDayChangePct), -10, 10),
      strongLateBuyConcentration: clamp(finite(thresholds.strongLateBuyConcentration, DEFAULT_BATTLE_SETTINGS.daytradeThresholds.strongLateBuyConcentration), 0, 100),
    },
    chipAutoRefreshMinutes: Math.round(clamp(finite(input.chipAutoRefreshMinutes, DEFAULT_BATTLE_SETTINGS.chipAutoRefreshMinutes), 1, 60)),
  };
}
