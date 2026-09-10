export type ImmediateSignalBaseline = {
  ticker: string;
  name: string;
  estimatedNextDaySellAmount: number;
  mainForceDataAvailable?: boolean;
};

export type ImmediateSignalMinuteBar = {
  ts: number;
  close: number;
  main_buy_amount?: number | null;
  main_sell_amount?: number | null;
};

export type MinuteBarImmediateSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "daytradeEarlyBuy50" | "daytradeEarlySell50";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const SESSION_END_MINUTE = 13 * 60 + 30;
export const IMMEDIATE_SIGNAL_MIN_PREVIOUS_PRESSURE = 100_000_000;
export const IMMEDIATE_SIGNAL_PRESSURE_RATIO = 200;

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function fourGatePressureThreshold(timestamp: number) {
  const minute = taipeiParts(timestamp).minute;
  if (minute < 9 * 60 + 30) return 50;
  if (minute < 10 * 60) return 70;
  if (minute < 11 * 60) return 90;
  return 120;
}

function formatAmount(amount: number) {
  if (amount >= 100_000_000) return `${(amount / 100_000_000).toFixed(2)} 億`;
  return `${(amount / 10_000).toFixed(1)} 萬`;
}

/**
 * Rebuild the two "今日即時" cumulative large-order signals from live 1-minute
 * bars when the upstream tick collector stops advancing. The signal is emitted
 * at the first minute that each direction crosses 200% of the previous day's
 * estimated next-day selling pressure, matching the primary feed semantics.
 */
export function calculateImmediateSignalsFromMinuteBars(
  tradeDate: string,
  baselines: ImmediateSignalBaseline[],
  barsByTicker: Map<string, ImmediateSignalMinuteBar[]>,
) {
  const signals: MinuteBarImmediateSignal[] = [];
  let latestBarAt = 0;

  for (const baseline of baselines) {
    const pressure = Number(baseline.estimatedNextDaySellAmount);
    if (baseline.mainForceDataAvailable === false || !Number.isFinite(pressure) || pressure <= IMMEDIATE_SIGNAL_MIN_PREVIOUS_PRESSURE) continue;
    const bars = [...(barsByTicker.get(baseline.ticker) ?? [])]
      .filter((bar) => {
        const parts = taipeiParts(Number(bar.ts));
        return Number.isFinite(bar.ts) && parts.tradeDate === tradeDate && parts.minute >= SESSION_START_MINUTE && parts.minute <= SESSION_END_MINUTE;
      })
      .sort((left, right) => left.ts - right.ts);
    let cumulativeBuy = 0;
    let cumulativeSell = 0;
    let buyEmitted = false;
    let sellEmitted = false;

    for (const bar of bars) {
      latestBarAt = Math.max(latestBarAt, bar.ts);
      cumulativeBuy += Math.max(0, Number(bar.main_buy_amount) || 0);
      cumulativeSell += Math.max(0, Number(bar.main_sell_amount) || 0);
      const price = Number(bar.close);
      if (!Number.isFinite(price)) continue;
      const buyRatio = cumulativeBuy / pressure * 100;
      const sellRatio = cumulativeSell / pressure * 100;

      if (!buyEmitted && buyRatio >= IMMEDIATE_SIGNAL_PRESSURE_RATIO) {
        buyEmitted = true;
        signals.push({
          tradeDate,
          ticker: baseline.ticker,
          name: baseline.name,
          kind: "daytradeEarlyBuy50",
          label: "盤中大單買進達前日預估隔日賣壓 200%",
          barTs: bar.ts,
          price,
          note: `前日預估隔日賣壓 ${formatAmount(pressure)}｜盤中大單買進 ${formatAmount(cumulativeBuy)}｜比例 ${buyRatio.toFixed(1)}%｜1 分 K 即時備援補算`,
        });
      }
      if (!sellEmitted && sellRatio >= IMMEDIATE_SIGNAL_PRESSURE_RATIO) {
        sellEmitted = true;
        signals.push({
          tradeDate,
          ticker: baseline.ticker,
          name: baseline.name,
          kind: "daytradeEarlySell50",
          label: "盤中大單賣出達前日預估隔日賣壓 200%",
          barTs: bar.ts,
          price,
          note: `前日預估隔日賣壓 ${formatAmount(pressure)}｜盤中大單賣出 ${formatAmount(cumulativeSell)}｜比例 ${sellRatio.toFixed(1)}%｜1 分 K 即時備援補算`,
        });
      }
    }
  }

  return {
    signals: signals.sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker)),
    latestBarAt,
  };
}

/**
 * Rebuild every minute-level source candidate used by the four-gate strategy
 * when the tick collector is delayed. Four-gate deliberately uses its own
 * 50/70/90/120% thresholds and must not depend on the separate 200% list.
 */
export function calculateFourGateSourceSignalsFromMinuteBars(
  tradeDate: string,
  baselines: ImmediateSignalBaseline[],
  barsByTicker: Map<string, ImmediateSignalMinuteBar[]>,
) {
  const signals: MinuteBarImmediateSignal[] = [];
  for (const baseline of baselines) {
    const pressure = Number(baseline.estimatedNextDaySellAmount);
    if (baseline.mainForceDataAvailable === false || !Number.isFinite(pressure) || pressure <= IMMEDIATE_SIGNAL_MIN_PREVIOUS_PRESSURE) continue;
    const bars = [...(barsByTicker.get(baseline.ticker) ?? [])]
      .filter((bar) => {
        const parts = taipeiParts(Number(bar.ts));
        return Number.isFinite(bar.ts) && parts.tradeDate === tradeDate && parts.minute >= SESSION_START_MINUTE && parts.minute <= SESSION_END_MINUTE;
      })
      .sort((left, right) => left.ts - right.ts);
    let cumulativeBuy = 0;
    let cumulativeSell = 0;
    for (const bar of bars) {
      cumulativeBuy += Math.max(0, Number(bar.main_buy_amount) || 0);
      cumulativeSell += Math.max(0, Number(bar.main_sell_amount) || 0);
      const price = Number(bar.close);
      if (!Number.isFinite(price)) continue;
      const threshold = fourGatePressureThreshold(bar.ts);
      const buyRatio = cumulativeBuy / pressure * 100;
      const sellRatio = cumulativeSell / pressure * 100;
      if (buyRatio >= threshold) {
        signals.push({
          tradeDate,
          ticker: baseline.ticker,
          name: baseline.name,
          kind: "daytradeEarlyBuy50",
          label: "盤中大單買進達四項分時門檻",
          barTs: bar.ts,
          price,
          note: `前日預估隔日賣壓 ${formatAmount(pressure)}｜盤中大單買進 ${formatAmount(cumulativeBuy)}｜比例 ${buyRatio.toFixed(1)}%｜1 分 K 四項備援補算`,
        });
      }
      if (sellRatio >= threshold) {
        signals.push({
          tradeDate,
          ticker: baseline.ticker,
          name: baseline.name,
          kind: "daytradeEarlySell50",
          label: "盤中大單賣出達四項分時門檻",
          barTs: bar.ts,
          price,
          note: `前日預估隔日賣壓 ${formatAmount(pressure)}｜盤中大單賣出 ${formatAmount(cumulativeSell)}｜比例 ${sellRatio.toFixed(1)}%｜1 分 K 四項備援補算`,
        });
      }
    }
  }
  return signals.sort((left, right) => left.barTs - right.barTs || left.ticker.localeCompare(right.ticker));
}
