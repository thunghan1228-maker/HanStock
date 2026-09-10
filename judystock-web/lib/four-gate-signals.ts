export type FourGateSourceSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "daytradeEarlySell50" | "daytradeEarlyBuy50";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

export type FourGateMinuteBar = {
  ts: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  main_buy_amount?: number | null;
  main_sell_amount?: number | null;
};

export type FourGateSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "fourGateBullish" | "fourGateBearish";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const SESSION_END_MINUTE = 13 * 60 + 30;
export const MIN_PREVIOUS_ESTIMATED_SELL_PRESSURE_AMOUNT = 100_000_000;
export const TODAY_IMMEDIATE_MIN_PRESSURE_RATIO = 200;

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function taipeiDate(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function taipeiMinute(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return date.getUTCHours() * 60 + date.getUTCMinutes();
}

export function pressureThresholdForTimestamp(timestamp: number) {
  const minute = taipeiMinute(timestamp);
  if (minute < 9 * 60 + 30) return 50;
  if (minute < 10 * 60) return 70;
  if (minute < 11 * 60) return 90;
  return 120;
}

export function pressureRatioFromNote(note: string) {
  const matched = note.match(/比例\s*([0-9]+(?:\.[0-9]+)?)%/);
  return matched ? Number(matched[1]) : null;
}

export function previousEstimatedSellPressureAmountFromNote(note: string) {
  const matched = note.match(/前日預估(?:隔日)?賣壓(?:金額)?\s*[:：]?\s*([0-9][0-9,.]*)\s*(億|萬|元)/);
  if (!matched) return null;
  const amount = Number(matched[1].replaceAll(",", ""));
  if (!Number.isFinite(amount)) return null;
  return amount * (matched[2] === "億" ? 100_000_000 : matched[2] === "萬" ? 10_000 : 1);
}

export function passesPreviousEstimatedSellPressureFilter(signal: { kind: string; note: string }) {
  if (signal.kind !== "daytradeEarlyBuy50" && signal.kind !== "daytradeEarlySell50") return true;
  const amount = previousEstimatedSellPressureAmountFromNote(signal.note);
  return amount !== null && amount > MIN_PREVIOUS_ESTIMATED_SELL_PRESSURE_AMOUNT;
}

export function passesTodayImmediatePressureRatioFilter(signal: { kind: string; note: string }) {
  if (signal.kind !== "daytradeEarlyBuy50" && signal.kind !== "daytradeEarlySell50") return true;
  const ratio = pressureRatioFromNote(signal.note);
  return ratio !== null && ratio >= TODAY_IMMEDIATE_MIN_PRESSURE_RATIO;
}

function amountNetRatio(bar: FourGateMinuteBar) {
  const buy = finiteNumber(bar.main_buy_amount) ?? 0;
  const sell = finiteNumber(bar.main_sell_amount) ?? 0;
  const total = buy + sell;
  return total > 0 ? (buy - sell) / total * 100 : null;
}

function formatPrice(value: number) {
  const showDecimals = Math.abs(value) < 1_000;
  return value.toLocaleString("zh-TW", {
    minimumFractionDigits: showDecimals ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function formatFourGateSignalNote(note: string) {
  const normalized = note
    .replace(
      /分時\s*([+-]?\d+(?:\.\d+)?)%\s*≥\s*(\d+(?:\.\d+)?)%/g,
      "即時金額 $1% ≥ 前日預估賣壓金額的 $2%",
    )
    .replaceAll("淨額比", "多空淨額比");
  const segments = normalized.split("｜").map((segment) => segment.trim()).filter(Boolean);
  if (segments.length <= 2) return segments.join(" ｜ ");
  return `${segments.slice(0, 2).join(" ｜ ")}\n${segments.slice(2).join(" ｜ ")}`;
}

function isDaytradeSignal(signal: FourGateSourceSignal) {
  return signal.kind === "daytradeEarlyBuy50" || signal.kind === "daytradeEarlySell50";
}

export function fourGateCandidateTickers(signals: FourGateSourceSignal[]) {
  return [...new Set(signals.flatMap((signal) => {
    if (!isDaytradeSignal(signal) || !passesPreviousEstimatedSellPressureFilter(signal) || taipeiDate(signal.barTs) !== signal.tradeDate) return [];
    const minute = taipeiMinute(signal.barTs);
    const pressureRatio = pressureRatioFromNote(signal.note);
    if (minute < SESSION_START_MINUTE || minute > SESSION_END_MINUTE || pressureRatio === null) return [];
    return pressureRatio >= pressureThresholdForTimestamp(signal.barTs) ? [signal.ticker] : [];
  }))];
}

export function calculateFourGateSignals(
  sourceSignals: FourGateSourceSignal[],
  barsByTicker: Map<string, FourGateMinuteBar[]>,
) {
  const emittedDirections = new Set<string>();
  const results: FourGateSignal[] = [];
  const orderedSignals = sourceSignals
    .filter(isDaytradeSignal)
    .sort((left, right) => left.barTs - right.barTs || left.ticker.localeCompare(right.ticker));

  for (const signal of orderedSignals) {
    if (!passesPreviousEstimatedSellPressureFilter(signal)) continue;
    const minute = taipeiMinute(signal.barTs);
    if (minute < SESSION_START_MINUTE || minute > SESSION_END_MINUTE) continue;
    if (taipeiDate(signal.barTs) !== signal.tradeDate) continue;
    const pressureRatio = pressureRatioFromNote(signal.note);
    const pressureThreshold = pressureThresholdForTimestamp(signal.barTs);
    if (pressureRatio === null || pressureRatio < pressureThreshold) continue;

    const bullish = signal.kind === "daytradeEarlyBuy50";
    const directionKey = `${signal.tradeDate}:${signal.ticker}:${bullish ? "bullish" : "bearish"}`;
    if (emittedDirections.has(directionKey)) continue;

    const bars = (barsByTicker.get(signal.ticker) ?? [])
      .filter((bar) => taipeiDate(bar.ts) === signal.tradeDate && taipeiMinute(bar.ts) >= SESSION_START_MINUTE && bar.ts <= signal.barTs)
      .sort((left, right) => left.ts - right.ts);
    const currentBar = bars.find((bar) => bar.ts === signal.barTs);
    const previousBar = bars.find((bar) => bar.ts === signal.barTs - 60_000);
    if (!currentBar || !previousBar) continue;

    const netRatio = amountNetRatio(currentBar);
    const previousNetRatio = amountNetRatio(previousBar);
    if (netRatio === null || previousNetRatio === null) continue;
    const mainForcePassed = bullish
      ? netRatio >= 50 && previousNetRatio > 0
      : netRatio <= -50 && previousNetRatio < 0;
    if (!mainForcePassed) continue;

    const firstFiveBars = bars.filter((bar) => {
      const barMinute = taipeiMinute(bar.ts);
      return barMinute >= SESSION_START_MINUTE && barMinute < SESSION_START_MINUTE + 5;
    });
    if (firstFiveBars.length === 0) continue;
    const firstHigh = Math.max(...firstFiveBars.map((bar) => finiteNumber(bar.high) ?? Number.NEGATIVE_INFINITY));
    const firstLow = Math.min(...firstFiveBars.map((bar) => finiteNumber(bar.low) ?? Number.POSITIVE_INFINITY));
    if (!Number.isFinite(firstHigh) || !Number.isFinite(firstLow)) continue;

    let priceVolume = 0;
    let cumulativeVolume = 0;
    for (const bar of bars) {
      const high = finiteNumber(bar.high);
      const low = finiteNumber(bar.low);
      const close = finiteNumber(bar.close);
      const volume = finiteNumber(bar.volume);
      if (high === null || low === null || close === null || volume === null || volume <= 0) continue;
      priceVolume += ((high + low + close) / 3) * volume;
      cumulativeVolume += volume;
    }
    if (cumulativeVolume <= 0) continue;
    const vwap = priceVolume / cumulativeVolume;
    const price = finiteNumber(signal.price);
    if (price === null) continue;
    const pricePassed = bullish ? price > vwap && price > firstHigh : price < vwap && price < firstLow;
    if (!pricePassed) continue;

    emittedDirections.add(directionKey);
    results.push({
      tradeDate: signal.tradeDate,
      ticker: signal.ticker,
      name: signal.name,
      kind: bullish ? "fourGateBullish" : "fourGateBearish",
      label: bullish ? "強多主力大單（四項通過）" : "強空主力大單（四項通過）",
      barTs: signal.barTs,
      price,
      note: [
        `即時金額 ${pressureRatio.toFixed(1)}% ≥ 前日預估賣壓金額的 ${pressureThreshold}%`,
        `多空淨額比 ${signedPercent(netRatio)}`,
        `現價 ${formatPrice(price)}${bullish ? "＞" : "＜"}VWAP ${formatPrice(vwap)}`,
        `${bullish ? "突破首五高" : "跌破首五低"} ${formatPrice(bullish ? firstHigh : firstLow)}`,
        "連續 2 次",
      ].join("｜"),
    });
  }

  return results.sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker));
}
