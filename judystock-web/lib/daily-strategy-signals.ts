export type DailyStrategyKind =
  | "redSword"
  | "threeRedSwords"
  | "turnoverBlack"
  | "blackDance"
  | "smallBlackDance"
  | "swordDance"
  | "godLowerShadow"
  | "blackPanther"
  | "dispositionBlackDragon"
  | "cross20Up2"
  | "cross20Down2";

export type DailyStrategyCandle = {
  date?: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma5?: number | null;
  ma20?: number | null;
};

export type DailyStrategyMatch = {
  kind: DailyStrategyKind;
  name: string;
  direction: "bull" | "bear";
  summary: string;
};

export const DAILY_STRATEGY_MODEL_VERSION = "daily-strategy-v1.2.0";
export const DAILY_STRATEGY_HISTORY_START_MINUTE = 9 * 60;
export const DAILY_STRATEGY_INTRADAY_START_MINUTE = 11 * 60;
export const DAILY_STRATEGY_INTRADAY_END_MINUTE = 14 * 60 + 30;

export function isDailyStrategyHistoryCoverageWindow(timestamp: number) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return false;
  const taipei = new Date(timestamp + 8 * 60 * 60_000);
  const weekday = taipei.getUTCDay();
  const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
  return weekday >= 1 && weekday <= 5
    && minutes >= DAILY_STRATEGY_HISTORY_START_MINUTE
    && minutes <= DAILY_STRATEGY_INTRADAY_END_MINUTE;
}

export function isDailyStrategySignalWindow(timestamp: number) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return false;
  const taipei = new Date(timestamp + 8 * 60 * 60_000);
  const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();
  return minutes >= DAILY_STRATEGY_INTRADAY_START_MINUTE
    && minutes <= DAILY_STRATEGY_INTRADAY_END_MINUTE;
}

export function isListedOrOtcStockCode(value: unknown) {
  const code = String(value ?? "").trim().toUpperCase();
  return /^[1-9]\d{3}[A-Z]?$/.test(code) || /^91\d{4}$/.test(code);
}

export const DAILY_STRATEGY_CATALOG: ReadonlyArray<Pick<DailyStrategyMatch, "kind" | "name" | "direction">> = [
  { kind: "redSword", name: "紅劍", direction: "bull" },
  { kind: "threeRedSwords", name: "三紅劍", direction: "bull" },
  { kind: "turnoverBlack", name: "換手黑", direction: "bull" },
  { kind: "blackDance", name: "黑飛舞", direction: "bull" },
  { kind: "smallBlackDance", name: "小黑飛舞", direction: "bull" },
  { kind: "swordDance", name: "劍飛舞", direction: "bull" },
  { kind: "godLowerShadow", name: "神下影", direction: "bull" },
  { kind: "blackPanther", name: "黑豹", direction: "bull" },
  { kind: "dispositionBlackDragon", name: "處置黑龍", direction: "bull" },
  { kind: "cross20Up2", name: "穿2", direction: "bull" },
  { kind: "cross20Down2", name: "破2", direction: "bear" },
] as const;

function finite(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function validCandle(candle: DailyStrategyCandle | null | undefined): candle is DailyStrategyCandle {
  return Boolean(candle)
    && finite(candle?.open) !== null
    && finite(candle?.high) !== null
    && finite(candle?.low) !== null
    && finite(candle?.close) !== null
    && finite(candle?.volume) !== null
    && candle!.high >= candle!.low;
}

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function averageVolume(history: DailyStrategyCandle[], count = 20) {
  return average(history.slice(-count).map((candle) => candle.volume).filter((value) => value > 0));
}

function movingAverageWithLive(history: DailyStrategyCandle[], liveClose: number, period: number) {
  if (history.length < period - 1) return null;
  return average([...history.slice(-(period - 1)).map((candle) => candle.close), liveClose]);
}

function isRed(candle: DailyStrategyCandle) { return candle.close > candle.open; }
function isBlack(candle: DailyStrategyCandle) { return candle.close < candle.open; }

function upperShadowRatio(candle: DailyStrategyCandle) {
  const range = candle.high - candle.low;
  return range > 0 ? (candle.high - Math.max(candle.open, candle.close)) / range : 0;
}

function lowerShadowRatio(candle: DailyStrategyCandle) {
  const range = candle.high - candle.low;
  return range > 0 ? (Math.min(candle.open, candle.close) - candle.low) / range : 0;
}

function isRedSwordShape(candle: DailyStrategyCandle) {
  const range = candle.high - candle.low;
  const body = Math.abs(candle.close - candle.open);
  return isRed(candle) && range > 0 && upperShadowRatio(candle) >= .22 && body / range >= .18;
}

function isLimitUp(candle: DailyStrategyCandle, previous: DailyStrategyCandle | undefined) {
  return Boolean(previous?.close && candle.close >= previous.close * 1.095 && candle.close >= candle.high * .995);
}

function near(value: number, target: number | null, tolerance = .018) {
  return target !== null && target > 0 && Math.abs(value / target - 1) <= tolerance;
}

function match(kind: DailyStrategyKind, summary: string): DailyStrategyMatch {
  const definition = DAILY_STRATEGY_CATALOG.find((item) => item.kind === kind)!;
  return { ...definition, summary };
}

export function detectIntradayMa20Cross(input: {
  previousPrice: number;
  currentPrice: number;
  previousMa20: number;
  currentMa20: number;
}) {
  const { previousPrice, currentPrice, previousMa20, currentMa20 } = input;
  if (![previousPrice, currentPrice, previousMa20, currentMa20].every(Number.isFinite)) return null;
  if (previousPrice <= previousMa20 && currentPrice > currentMa20) return match("cross20Up2", "盤中價格由 20MA 下方即時向上穿越");
  if (previousPrice >= previousMa20 && currentPrice < currentMa20) return match("cross20Down2", "盤中價格由 20MA 上方即時向下跌破");
  return null;
}

export function detectIntradayDailyStrategies(input: {
  history: DailyStrategyCandle[];
  live: DailyStrategyCandle;
  sessionProgress?: number;
  activeDisposition?: boolean;
}) {
  const history = input.history.filter(validCandle).sort((a, b) => String(a.date ?? "").localeCompare(String(b.date ?? "")));
  const live = input.live;
  if (!validCandle(live) || history.length < 20) return [] as DailyStrategyMatch[];

  const previous = history.at(-1)!;
  const twoDaysAgo = history.at(-2);
  const threeDaysAgo = history.at(-3);
  const ma5 = movingAverageWithLive(history, live.close, 5);
  const ma20 = movingAverageWithLive(history, live.close, 20);
  const dailyVolume = averageVolume(history);
  const progress = Math.min(1, Math.max(.08, Number(input.sessionProgress) || 1));
  const projectedVolumeRatio = dailyVolume && dailyVolume > 0 ? live.volume / progress / dailyVolume : 0;
  const matches: DailyStrategyMatch[] = [];

  const redSword = isRedSwordShape(live) && ma20 !== null && live.close > ma20 && projectedVolumeRatio >= 1.15;
  if (redSword) matches.push(match("redSword", "帶上影紅 K、量能放大且仍站在 20MA 上方"));

  if (redSword && isRedSwordShape(previous) && twoDaysAgo && isRedSwordShape(twoDaysAgo)
    && live.close / previous.close < 1.07 && previous.close / twoDaysAgo.close < 1.07) {
    matches.push(match("threeRedSwords", "連續三根帶上影紅 K，走勢未急噴或被吞噬"));
  }

  const turnoverBlack = isBlack(live) && ma20 !== null && live.close > ma20
    && projectedVolumeRatio >= 1.15 && [previous, twoDaysAgo].some((candle) => candle && isRed(candle));
  if (turnoverBlack) matches.push(match("turnoverBlack", "上漲段第一根放量黑 K，仍守在 20MA 上方"));

  const previousTwentyHigh = Math.max(...history.slice(-21, -1).map((candle) => candle.high));
  const previousFiveHigh = Math.max(...history.slice(-6, -1).map((candle) => candle.high));
  const previousWasTwentyDayHigh = isBlack(previous) && previous.high >= previousTwentyHigh;
  const previousWasFiveDayHigh = isBlack(previous) && previous.high >= previousFiveHigh;
  const holdsPreviousLow = live.low >= previous.low;
  if (previousWasTwentyDayHigh && near(live.close, ma5) && holdsPreviousLow) {
    matches.push(match("blackDance", "前一日創高黑 K，盤中回到五日線附近且未破黑 K 低點"));
  }
  if (!previousWasTwentyDayHigh && previousWasFiveDayHigh && near(live.close, ma5) && holdsPreviousLow) {
    matches.push(match("smallBlackDance", "前一日創短波段高點，盤中回測五日線且未破低"));
  }

  const previousAverageVolume = averageVolume(history.slice(0, -1));
  const previousRedSword = isRedSwordShape(previous) && Boolean(previousAverageVolume && previous.volume >= previousAverageVolume * 1.1);
  const previousBlackPanther = Boolean(twoDaysAgo && threeDaysAgo && isLimitUp(twoDaysAgo, threeDaysAgo) && isBlack(previous));
  if ((previousRedSword || previousBlackPanther) && near(live.close, ma5) && projectedVolumeRatio <= 1.05) {
    matches.push(match("swordDance", "紅劍或黑豹後量縮回到五日線附近"));
  }

  const liveBody = Math.max(Math.abs(live.close - live.open), (live.high - live.low) * .06);
  const lowerShadow = Math.min(live.open, live.close) - live.low;
  if (lowerShadowRatio(live) >= .38 && lowerShadow >= liveBody * 1.8 && ma5 !== null && live.close >= ma5) {
    matches.push(match("godLowerShadow", "盤中出現長下影並重新站回五日線"));
  }

  if (twoDaysAgo && isLimitUp(previous, twoDaysAgo) && isBlack(live) && projectedVolumeRatio >= 1.2) {
    matches.push(match("blackPanther", "前一日漲停後出現放量長黑，列入守低觀察"));
  }

  if (input.activeDisposition && turnoverBlack) {
    matches.push(match("dispositionBlackDragon", "處置股出現換手黑龍型態，獨立提示"));
  }

  return [...new Map(matches.map((item) => [item.kind, item])).values()];
}

export function detectCompletedDailyStrategies(input: {
  candles: DailyStrategyCandle[];
  activeDisposition?: boolean;
}) {
  const candles = input.candles
    .filter(validCandle)
    .sort((left, right) => String(left.date ?? "").localeCompare(String(right.date ?? "")));
  if (candles.length < 21) return [] as DailyStrategyMatch[];

  const live = candles.at(-1)!;
  const history = candles.slice(0, -1);
  const matches = detectIntradayDailyStrategies({
    history,
    live,
    sessionProgress: 1,
    activeDisposition: input.activeDisposition,
  });
  const previous = history.at(-1)!;
  const currentMa20 = average(candles.slice(-20).map((candle) => candle.close));
  const previousMa20 = average(history.slice(-20).map((candle) => candle.close));
  if (currentMa20 !== null && previousMa20 !== null) {
    const cross = detectIntradayMa20Cross({
      previousPrice: previous.close,
      currentPrice: live.close,
      previousMa20,
      currentMa20,
    });
    if (cross) matches.push(cross);
  }
  return [...new Map(matches.map((item) => [item.kind, item])).values()];
}
