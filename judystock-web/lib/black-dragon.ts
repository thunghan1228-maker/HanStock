import { calculateMaArrangement } from "./ma-arrangement.ts";
import { MA_SCORE_PERIODS } from "./ma-score-ranking.ts";
import { blackDragonNewHighPeriods, blackDragonReferenceHighs, type BlackDragonReferenceHighs } from "./black-dragon-highs.ts";

export const BLACK_DRAGON_MODEL_VERSION = "black-dragon-prior-five-high-v6";
export const BLACK_DRAGON_RECENT_SESSIONS = 5;

export type BlackDragonSourceRow = {
  code: string;
  name?: string;
  groupName?: string;
  market: "twse" | "tpex";
  date?: string;
  open: number | null;
  high?: number | null;
  close: number | null;
  changePct?: number | null;
  maScore: number | null;
  newHighPeriods?: number[];
  referenceHighs?: BlackDragonReferenceHighs;
  maLabel?: string;
  volume?: number | null;
  averageVolume20d?: number | null;
  volumeRatio20d?: number | null;
  turnoverAmount?: number | null;
  blackBodyPct?: number | null;
};

export type BlackDragonRow = BlackDragonSourceRow & {
  name: string;
  open: number;
  high: number;
  close: number;
  maScore: number;
  newHighPeriods: number[];
  referenceHighs: BlackDragonReferenceHighs;
  maLabel: string;
  volume: number;
  averageVolume20d: number;
  volumeRatio20d: number;
  turnoverAmount: number;
  blackBodyPct: number;
};

export type BlackDragonHistoryBar = {
  date: string;
  open: number;
  high?: number;
  low?: number;
  close: number;
  volume?: number;
};

function isListedOrOtcCommonStock(value: unknown) {
  const code = String(value ?? "").trim().toUpperCase();
  return /^[1-9]\d{3}[A-Z]?$/.test(code) || /^91\d{4}$/.test(code);
}

export function buildBlackDragonRows(source: BlackDragonSourceRow[], minimumScore = 10) {
  return source.flatMap((row): BlackDragonRow[] => {
    const open = Number(row.open);
    const high = Number(row.high);
    const close = Number(row.close);
    const score = Number(row.maScore);
    const volume = Math.max(0, Number(row.volume) || 0);
    const averageVolume20d = Math.max(0, Number(row.averageVolume20d) || 0);
    const volumeRatio20d = Math.max(0, Number(row.volumeRatio20d) || 0);
    const turnoverAmount = Math.max(0, Number(row.turnoverAmount) || close * volume);
    const blackBodyPct = Math.max(0, Number(row.blackBodyPct) || (open > 0 ? (open - close) / open * 100 : 0));
    const newHighPeriods = blackDragonNewHighPeriods(high, row.referenceHighs);
    if (!isListedOrOtcCommonStock(row.code) || !Number.isFinite(open) || !Number.isFinite(close)
      || !Number.isFinite(high) || !Number.isFinite(score) || open <= 0 || close <= 0
      || high < Math.max(open, close) || close >= open || score < Math.max(10, minimumScore)
      || newHighPeriods.length === 0) return [];
    return [{
      ...row,
      name: row.name?.trim() || row.code,
      open,
      high,
      close,
      maScore: score,
      newHighPeriods,
      referenceHighs: row.referenceHighs!,
      maLabel: row.maLabel?.trim() || "資料不足",
      volume,
      averageVolume20d,
      volumeRatio20d,
      turnoverAmount,
      blackBodyPct,
    }];
  }).sort((a, b) => String(b.date ?? "").localeCompare(String(a.date ?? ""))
    || b.volumeRatio20d - a.volumeRatio20d || b.turnoverAmount - a.turnoverAmount
    || b.maScore - a.maScore || a.code.localeCompare(b.code, "zh-TW"));
}

function average(values: number[]) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function findRecentBlackDragonSignal(input: {
  code: string;
  name?: string;
  market: "twse" | "tpex";
  bars: BlackDragonHistoryBar[];
  completedThrough: string;
  recentSessions?: number;
}) {
  const completedThrough = input.completedThrough.replaceAll("-", "/");
  const ordered = [...new Map(input.bars.map((bar) => ({ ...bar, date: bar.date.replaceAll("-", "/") }))
    .filter((bar) => /^\d{4}\/\d{2}\/\d{2}$/.test(bar.date) && bar.date <= completedThrough)
    .map((bar) => [bar.date, bar])).values()].sort((a, b) => a.date.localeCompare(b.date));
  const firstCandidate = Math.max(0, ordered.length - Math.max(1, input.recentSessions ?? BLACK_DRAGON_RECENT_SESSIONS));

  for (let index = ordered.length - 1; index >= firstCandidate; index -= 1) {
    const current = ordered[index];
    if (![current.open, current.high, current.close].every((value) => typeof value === "number" && Number.isFinite(value) && value > 0)
      || Number(current.high) < Math.max(current.open, current.close)
      || current.close >= current.open || index + 1 < Math.max(...MA_SCORE_PERIODS)) continue;
    const closes = ordered.slice(0, index + 1).map((bar) => bar.close);
    const maValues = Object.fromEntries(MA_SCORE_PERIODS.map((period) => [
      period,
      average(closes.slice(index - period + 1, index + 1)),
    ])) as Record<number, number>;
    const previousMaValues = Object.fromEntries(MA_SCORE_PERIODS.map((period) => [
      period,
      index >= period ? average(closes.slice(index - period, index)) : maValues[period],
    ])) as Record<number, number>;
    const arrangement = calculateMaArrangement({ close: current.close, maValues, previousMaValues });
    if (!arrangement || arrangement.score < 10) continue;
    const referenceHighs = blackDragonReferenceHighs(ordered.slice(0, index));
    const newHighPeriods = blackDragonNewHighPeriods(Number(current.high), referenceHighs);
    if (newHighPeriods.length === 0) continue;
    const previousClose = ordered[index - 1]?.close;
    const previousVolumes = ordered.slice(Math.max(0, index - 20), index)
      .map((bar) => Number(bar.volume)).filter((value) => Number.isFinite(value) && value > 0);
    const volume = Math.max(0, Number(current.volume) || 0);
    const averageVolume20d = previousVolumes.length >= 20 ? average(previousVolumes.slice(-20)) : 0;
    const volumeRatio20d = averageVolume20d > 0 ? volume / averageVolume20d : 0;
    return {
      code: input.code,
      name: input.name?.trim() || input.code,
      market: input.market,
      date: current.date,
      open: current.open,
      high: Number(current.high),
      close: current.close,
      changePct: previousClose ? Math.round((current.close / previousClose - 1) * 10_000) / 100 : null,
      maScore: arrangement.score,
      newHighPeriods: [...newHighPeriods],
      referenceHighs,
      maLabel: arrangement.label,
      volume,
      averageVolume20d,
      volumeRatio20d,
      turnoverAmount: current.close * volume,
      blackBodyPct: current.open > 0 ? (current.open - current.close) / current.open * 100 : 0,
    } satisfies BlackDragonRow;
  }
  return null;
}
