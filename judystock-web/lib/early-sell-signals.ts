import { type MainForceGroupRankings } from "./main-force-group-ranks";
import { type AjLargeForceGroupTransition } from "./intraday-large-force";

export type IntradaySignalSource = "fullMarket" | "previousDayDaytrade" | "both" | "fullMarketTriangle" | "fullMarketMainForce" | "fullMarketLargeForce" | "fullMarketFiveMinute" | "top20GroupLargeOrder" | "riverRadar";
export type DaytradeEarlySellSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "daytradeEarlySell50" | "daytradeEarlyBuy50" | "intradayExtraLargeSell" | "intradayExtraLargeBuy" | "intradayLargeForceBuy" | "intradayLargeForceSell" | "instantLargeSell" | "instantLargeBuy" | "fourGateBullish" | "fourGateBearish" | "mainForceTurnBullish" | "mainForceStrongBullish" | "mainForceTurnBearish" | "mainForceStrongBearish" | "triangleNearBreakout" | "triangleBreakoutPendingVolume" | "triangleVolumeBreakout" | "fiveMinuteTwelveShort" | "fiveMinuteOnePlusTwoLong" | "riverBull" | "riverBear";
  label: string;
  barTs: number;
  price: number;
  changePct?: number;
  note: string;
  sourceUniverse?: IntradaySignalSource;
  riverSignalType?: "black-dragon";
  strategyKind?: string;
  demo?: boolean;
};
export type DaytradeEarlySellPayload = {
  ok: boolean;
  tradeDate?: string;
  previousTradeDate?: string;
  thresholdPct?: number;
  window?: string;
  signals?: DaytradeEarlySellSignal[];
  fourGateSignals?: DaytradeEarlySellSignal[];
  mainForceSignals?: DaytradeEarlySellSignal[];
  extraLargeSellSignals?: DaytradeEarlySellSignal[];
  extraLargeBuySignals?: DaytradeEarlySellSignal[];
  largeForceSignals?: DaytradeEarlySellSignal[];
  instantLargeSignals?: DaytradeEarlySellSignal[];
  instantLargeCollector?: {
    prepared?: boolean;
    candidateCount?: number;
    buyCandidateCount?: number;
    sellCandidateCount?: number;
    localRankedGroupCount?: number;
    watchdogState?: string;
    candidateTickCount?: number;
    eligibleTickCount?: number;
    burstThresholdCount?: number;
    persistedSignalCount?: number;
    persistenceErrorCount?: number;
    pendingSignalCount?: number;
    lastCandidateTickAt?: number;
    lastBurstAt?: number;
    lastPersistenceError?: string;
    lastPersistenceRecoveredAt?: number;
  } | null;
  groupRankings?: MainForceGroupRankings;
  summary?: { total: number; uniqueStocks: number; byKind: Record<string, { events: number; stocks: number }> };
  dates?: string[];
  message?: string;
  storageUnavailable?: boolean;
  carryingPreviousSession?: boolean;
  signalFeed?: {
    polledAt?: number;
    upstreamAt?: number;
    fallbackAt?: number;
    degraded?: boolean;
    mode?: "live" | "minute-bars-fallback-pending" | "minute-bars-fallback" | "upstream-delayed" | "stored-live-snapshot";
    scanned?: number;
  };
  largeForceScan?: {
    tradeDate?: string;
    status?: "idle" | "running" | "completed" | "error";
    nextIndex?: number;
    processed?: number;
    available?: number;
    signalCount?: number;
    total?: number;
    cycle?: number;
    updatedAt?: number;
  } | null;
};
export type IntradaySignalStockMeta = { ticker: string; name: string; group: string; groups?: string[]; changePct: number | null };
export type IntradaySignalTechnicalMeta = { maScore: number | null; riverBase: number | null };
export type IntradayLargeForceValueRow = {
  ticker: string;
  name?: string;
  group?: string;
  tradeDate: string;
  forcePct: number | null;
  barTs: number | null;
  price?: number | null;
  buyAmount?: number | null;
  sellAmount?: number | null;
  netAmount?: number | null;
  turnoverAmount?: number | null;
};
export type AjLargeForceFilterPayload = {
  ok?: boolean;
  tradeDate?: string;
  previousDates?: string[];
  live?: boolean;
  rows?: AjLargeForceGroupTransition[];
  error?: string;
};
export type AjLargeForceFilterStatus = "idle" | "loading" | "ready" | "error";
export type IntradaySignalCenterMode = "today" | "blackDragon" | "fiveMinuteTwelveShort" | "fiveMinuteOnePlusTwoLong" | "instantLarge" | "mainForce" | "fourGate" | "extraLargeSell" | "extraLargeBuy" | "largeForce" | "history";
// 黑龍、12空、1+2多已從盤中即時訊號中心完全移除；保留 type 字面值只為相容舊資料與型別。
export const INTRADAY_SIGNAL_CENTER_MODES: IntradaySignalCenterMode[] = ["today", "instantLarge", "mainForce", "fourGate", "extraLargeSell", "extraLargeBuy", "largeForce", "history"];
export type RiverCenterRow = { code: string; name?: string; tradeDate: string; barTs: number; price?: number; changePct?: number; score: number; direction?: "bull" | "bear"; label?: string; signalType?: "river" | "daily-strategy" | "black-dragon"; strategyKind?: string; strategyName?: string; groupName?: string; groupRank?: number; stockRank?: number; groupScore?: number; selectionSource?: "focus-ranking-67"; volumeRatio?: number | null; vwap?: number | null };
export type BlackDragonIntradayCenterRow = RiverCenterRow & { selectionRuleVersion?: string; sessionOpen?: number; signalHigh?: number; referenceHigh5?: number; referenceThrough?: string; maScore?: number; newHighPeriods?: number[]; cumulativeVolume?: number; averageVolume20d?: number; projectedVolumeRatio?: number; cumulativeTurnover?: number; blackBodyPct?: number; tier?: "all" | "selected" | "surge" };
export type BlackDragonIntradayCenterPayload = { ok?: boolean; signalDate?: string; signals?: BlackDragonIntradayCenterRow[]; baseCoverage?: { ready?: number; total?: number; missing?: number } };

export function isFourGateSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "fourGateBullish" || signal.kind === "fourGateBearish";
}

export function isExtraLargeSellSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "intradayExtraLargeSell";
}

export function isExtraLargeBuySignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "intradayExtraLargeBuy";
}

export function isLargeForceSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "intradayLargeForceBuy" || signal.kind === "intradayLargeForceSell";
}

export function isInstantLargeSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "instantLargeBuy" || signal.kind === "instantLargeSell";
}

export function isFiveMinuteTwelveShortSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "fiveMinuteTwelveShort";
}

export function isFiveMinuteOnePlusTwoLongSignal(signal: DaytradeEarlySellSignal) {
  return signal.kind === "fiveMinuteOnePlusTwoLong";
}
export const EARLY_SELL_SEEN_KEY = "hanstock-battle-early-sell-seen-v1";
export const EARLY_SELL_PINNED_KEY = "hanstock-battle-early-sell-pinned-v1";
export const EARLY_SELL_PINNED_QUEUE_KEY = "hanstock-battle-early-sell-pinned-queue-v1";
export const EARLY_SELL_ALERTS_ENABLED_KEY = "hanstock-battle-early-sell-alerts-enabled-v1";
export const EARLY_SELL_TOAST_POSITION_KEY = "hanstock-battle-early-sell-toast-position-v1";
export const EARLY_SELL_TOAST_SIZE_KEY = "hanstock-battle-early-sell-toast-size-v1";
export const EARLY_SELL_CENTER_POSITION_KEY = "hanstock-battle-early-sell-center-position-v1";
export const EARLY_SELL_CENTER_PINNED_KEY = "hanstock-battle-early-sell-center-pinned-v1";
export const EARLY_SELL_LARGE_FORCE_VALUES_KEY = "hanstock-battle-early-sell-large-force-values-v1";

export type FloatingPanelPosition = { x: number; y: number };
export type FloatingPanelSize = { width: number; height: number };

export function taipeiSessionState() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${value("year")}-${value("month")}-${value("day")}`,
    weekday: value("weekday"),
    minutes: Number(value("hour")) * 60 + Number(value("minute")),
  };
}

export function taipeiTradeDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export function intradaySignalKey(signal: DaytradeEarlySellSignal) {
  if (signal.strategyKind === "blackDragon") return `${signal.tradeDate}:${signal.ticker}:blackDragon`;
  return `${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`;
}
