import type { FourGateMinuteBar } from "./four-gate-signals";

export const INTRADAY_EXTRA_LARGE_SELL_KIND = "intradayExtraLargeSell" as const;
export const INTRADAY_EXTRA_LARGE_SELL_LABEL = "盤中大單賣出達前日大單淨額";
export const INTRADAY_EXTRA_LARGE_BUY_KIND = "intradayExtraLargeBuy" as const;
export const INTRADAY_EXTRA_LARGE_BUY_LABEL = "盤中大單買進達前日大單淨額";
// 特大買／賣單共用：前一交易日大單淨額絕對值必須嚴格大於五千萬元。
export const INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT = 50_000_000;
export const INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE = 10;

export type PreviousLargeNetBaseline = {
  ticker: string;
  name: string;
  dataDate: string;
  netLargeAmount: number;
  turnoverAmount?: number;
  fallbackPrice?: number;
};

export type IntradayExtraLargeSellSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: typeof INTRADAY_EXTRA_LARGE_SELL_KIND;
  label: typeof INTRADAY_EXTRA_LARGE_SELL_LABEL;
  barTs: number;
  price: number;
  note: string;
};

export type IntradayExtraLargeBuySignal = Omit<IntradayExtraLargeSellSignal, "kind" | "label"> & {
  kind: typeof INTRADAY_EXTRA_LARGE_BUY_KIND;
  label: typeof INTRADAY_EXTRA_LARGE_BUY_LABEL;
};

type ExtraLargeCandidate = { kind: string; tradeDate: string; barTs: number; note: string };
type ExtraLargeMinuteBar = FourGateMinuteBar & { main_force_available?: boolean | null };

export function extraLargeTriggerForce(signal: ExtraLargeCandidate) {
  const net = signal.note.match(/觸發當時大單淨額\s+([+-]?[\d,.]+)\s*(億|萬|元)/u);
  const amount = net ? Number(net[1].replaceAll(",", "")) * (net[2] === "億" ? 100_000_000 : net[2] === "萬" ? 10_000 : 1) : Number.NaN;
  const pct = signal.note.match(/觸發當時盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u);
  const forcePct = pct ? Number(pct[1]) : null;
  return { netAmount: Number.isFinite(amount) ? amount : null, forcePct };
}

export function hasQualifiedExtraLargeTriggerForce(signal: ExtraLargeCandidate) {
  if (signal.kind !== INTRADAY_EXTRA_LARGE_SELL_KIND && signal.kind !== INTRADAY_EXTRA_LARGE_BUY_KIND) return true;
  const { netAmount, forcePct } = extraLargeTriggerForce(signal);
  if (netAmount === null || netAmount === 0) return false;
  const buy = signal.kind === INTRADAY_EXTRA_LARGE_BUY_KIND;
  return (buy ? netAmount > 0 : netAmount < 0)
    && (forcePct === null || (Number.isFinite(forcePct) && (buy ? forcePct >= 0 : forcePct <= 0)));
}

// 與大戶力共用口徑：同交易日截至觸發分鐘的累計買額－累計賣額。
// 舊庫存只有買賣金額，仍可確認正負；沒有成交額時不可捏造百分比。
function* extraLargeSnapshots(tradeDate: string, bars: ExtraLargeMinuteBar[], cutoff = Infinity) {
  const uniqueBars = new Map<number, ExtraLargeMinuteBar>();
  for (const bar of bars) {
    const ts = finiteNumber(bar.ts);
    if (ts === null || ts > cutoff) continue;
    const parts = taipeiParts(ts);
    if (parts.tradeDate !== tradeDate || parts.minute < SESSION_START_MINUTE || parts.minute > SESSION_END_MINUTE) continue;
    if (!uniqueBars.has(ts)) uniqueBars.set(ts, bar);
  }
  let buyAmount = 0;
  let sellAmount = 0;
  let turnoverAmount = 0;
  let turnoverComplete = true;
  for (const bar of [...uniqueBars.values()].sort((a, b) => a.ts - b.ts)) {
    const buy = bar.main_buy_amount == null ? null : finiteNumber(bar.main_buy_amount);
    const sell = bar.main_sell_amount == null ? null : finiteNumber(bar.main_sell_amount);
    if (bar.main_force_available === false || buy === null || sell === null || buy < 0 || sell < 0) continue;
    buyAmount += buy;
    sellAmount += sell;
    const turnover = Number(bar.close) * Number(bar.volume) * 1_000;
    if (Number(bar.close) > 0 && Number(bar.volume) >= 0 && Number.isFinite(turnover)
      && (turnover > 0 || buy + sell === 0)) turnoverAmount += turnover;
    else turnoverComplete = false;
    const netAmount = buyAmount - sellAmount;
    yield { bar, buyAmount, sellAmount, netAmount,
      forcePct: turnoverComplete && turnoverAmount > 0 ? netAmount / turnoverAmount * 100 : null };
  }
}

function extraLargeForceNote(snapshot: { netAmount: number; forcePct: number | null }) {
  const net = `${snapshot.netAmount > 0 ? "+" : "-"}${formatAmount(Math.abs(snapshot.netAmount))}`;
  const pct = snapshot.forcePct;
  const percentage = pct === null ? "" : `｜觸發當時盤中大戶力 ${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
  return `觸發當時大單淨額 ${net}${percentage}`;
}

export function qualifyExtraLargeSignalByTriggerBars<T extends ExtraLargeCandidate>(signal: T, bars: ExtraLargeMinuteBar[]): T | null {
  if (signal.kind !== INTRADAY_EXTRA_LARGE_SELL_KIND && signal.kind !== INTRADAY_EXTRA_LARGE_BUY_KIND) return signal;
  const snapshots = [...extraLargeSnapshots(signal.tradeDate, bars, signal.barTs)];
  const snapshot = snapshots.at(-1);
  // 不可以使用較早且已過時的資料來證明觸發當分鐘的方向。
  if (!snapshot || Math.floor(snapshot.bar.ts / 60_000) !== Math.floor(signal.barTs / 60_000)) return null;
  if (signal.kind === INTRADAY_EXTRA_LARGE_BUY_KIND ? snapshot.netAmount <= 0 : snapshot.netAmount >= 0) return null;
  const note = signal.note
    .replace(/(?:｜)?觸發當時大單淨額\s+[+-]?[\d,.]+\s*(?:億|萬|元)/gu, "")
    .replace(/(?:｜)?觸發當時盤中大戶力\s+[+-]?\d+(?:\.\d+)?%/gu, "");
  return { ...signal, note: `${note}｜${extraLargeForceNote(snapshot)}` };
}

export function mergePreviousLargeNetBaselines(...sources: PreviousLargeNetBaseline[][]) {
  const merged = new Map<string, PreviousLargeNetBaseline>();
  for (const source of sources) {
    for (const baseline of source) {
      const ticker = String(baseline.ticker ?? "").trim().toUpperCase();
      const netLargeAmount = Number(baseline.netLargeAmount);
      if (!ticker || !baseline.dataDate || !Number.isFinite(netLargeAmount)) continue;
      const normalized = { ...baseline, ticker, netLargeAmount };
      const current = merged.get(ticker);
      // The full-market endpoint can temporarily return an empty bar set while
      // its previous-day backfill is unavailable.  Never let that synthetic
      // zero erase a valid permanent baseline collected earlier.
      if (!current || (Number(current.netLargeAmount) === 0 && netLargeAmount !== 0)) merged.set(ticker, normalized);
    }
  }
  return [...merged.values()];
}

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const SESSION_END_MINUTE = 13 * 60 + 30;

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function formatAmount(value: number) {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 億`;
  if (value >= 10_000) return `${(value / 10_000).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 萬`;
  return `${Math.round(value).toLocaleString("zh-TW")} 元`;
}

function baselineNetFundingRate(baseline: PreviousLargeNetBaseline) {
  const net = finiteNumber(baseline.netLargeAmount);
  const turnover = finiteNumber(baseline.turnoverAmount);
  return net !== null && turnover !== null && turnover > 0 ? net / turnover * 100 : null;
}

function baselineAbsoluteNetFundingRate(baseline: PreviousLargeNetBaseline) {
  const net = finiteNumber(baseline.netLargeAmount);
  const turnover = finiteNumber(baseline.turnoverAmount);
  return net !== null && turnover !== null && turnover > 0 ? Math.abs(net) / turnover * 100 : null;
}

export function extraLargeSellCandidateTickers(baselines: PreviousLargeNetBaseline[]) {
  return [...new Set(baselines.flatMap((baseline) => {
    const net = finiteNumber(baseline.netLargeAmount);
    const netFundingRate = baselineNetFundingRate(baseline);
    return net !== null
      && net > INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      && netFundingRate !== null
      && netFundingRate > INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE
      ? [baseline.ticker.trim().toUpperCase()]
      : [];
  }))];
}

/**
 * 特大買單是特大賣單的鏡像：以前日大單淨賣超為門檻，盤中累計買進
 * 回補同等金額才發訊號。資金占比一律使用淨額絕對值，方便閱讀比較。
 */
export function extraLargeBuyCandidateTickers(baselines: PreviousLargeNetBaseline[]) {
  return [...new Set(baselines.flatMap((baseline) => {
    const net = finiteNumber(baseline.netLargeAmount);
    const netFundingRate = baselineAbsoluteNetFundingRate(baseline);
    return net !== null
      && net < -INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      && netFundingRate !== null
      && netFundingRate > INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE
      ? [baseline.ticker.trim().toUpperCase()]
      : [];
  }))];
}

export function extraLargeSellFilteredTickers(baselines: PreviousLargeNetBaseline[]) {
  return [...new Set(baselines.flatMap((baseline) => {
    const net = finiteNumber(baseline.netLargeAmount);
    const netFundingRate = baselineNetFundingRate(baseline);
    return net !== null && net > 0 && (
      net <= INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      || netFundingRate === null
      || netFundingRate <= INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE
    )
      ? [baseline.ticker.trim().toUpperCase()]
      : [];
  }))];
}

export function extraLargeSellPreviousNetAmount(note: string) {
  const matched = note.match(/前日大單淨額\s+([0-9][0-9,.]*)\s*(億|萬|元)/);
  if (!matched) return null;
  const amount = Number(matched[1].replaceAll(",", ""));
  if (!Number.isFinite(amount)) return null;
  return amount * (matched[2] === "億" ? 100_000_000 : matched[2] === "萬" ? 10_000 : 1);
}

export function extraLargeBuyPreviousNetAmount(note: string) {
  const matched = note.match(/前日大單淨額\s+-([0-9][0-9,.]*)\s*(億|萬|元)/);
  if (!matched) return null;
  const amount = Number(matched[1].replaceAll(",", ""));
  if (!Number.isFinite(amount)) return null;
  return amount * (matched[2] === "億" ? 100_000_000 : matched[2] === "萬" ? 10_000 : 1);
}

export function extraLargeSellNetFundingRate(note: string) {
  const matched = note.match(/大單淨額（隔日沖）資金占比\s+([0-9][0-9,.]*)%/);
  if (!matched) return null;
  const rate = Number(matched[1].replaceAll(",", ""));
  return Number.isFinite(rate) ? rate : null;
}

export function calculateIntradayExtraLargeSellSignals(
  tradeDate: string,
  baselines: PreviousLargeNetBaseline[],
  barsByTicker: ReadonlyMap<string, FourGateMinuteBar[]>,
) {
  const results: IntradayExtraLargeSellSignal[] = [];
  const emitted = new Set<string>();

  for (const baseline of baselines) {
    const ticker = baseline.ticker.trim().toUpperCase();
    const threshold = finiteNumber(baseline.netLargeAmount);
    const netFundingRate = baselineNetFundingRate(baseline);
    const key = `${tradeDate}:${ticker}`;
    if (!ticker
      || emitted.has(key)
      || !threshold
      || threshold <= INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      || netFundingRate === null
      || netFundingRate <= INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE
      || baseline.dataDate >= tradeDate) continue;

    for (const snapshot of extraLargeSnapshots(tradeDate, barsByTicker.get(ticker) ?? [])) {
      const { bar, sellAmount: cumulativeSell, netAmount } = snapshot;
      if (cumulativeSell < threshold || netAmount >= 0) continue;
      const barPrice = finiteNumber(bar.close);
      const price = barPrice !== null && barPrice > 0 ? barPrice : finiteNumber(baseline.fallbackPrice);
      if (price === null || price <= 0) break;
      emitted.add(key);
      results.push({
        tradeDate,
        ticker,
        name: baseline.name || ticker,
        kind: INTRADAY_EXTRA_LARGE_SELL_KIND,
        label: INTRADAY_EXTRA_LARGE_SELL_LABEL,
        barTs: Math.trunc(bar.ts),
        price,
        note: `前日大單淨額 ${formatAmount(threshold)}｜大單淨額（隔日沖）資金占比 ${netFundingRate.toFixed(2)}%\n盤中大單賣出累計 ${formatAmount(cumulativeSell)}｜達成比例 ${(cumulativeSell / threshold * 100).toFixed(1)}%｜${extraLargeForceNote(snapshot)}`,
      });
      break;
    }
  }

  return results.sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker));
}

export function calculateIntradayExtraLargeBuySignals(
  tradeDate: string,
  baselines: PreviousLargeNetBaseline[],
  barsByTicker: ReadonlyMap<string, FourGateMinuteBar[]>,
) {
  const results: IntradayExtraLargeBuySignal[] = [];
  const emitted = new Set<string>();

  for (const baseline of baselines) {
    const ticker = baseline.ticker.trim().toUpperCase();
    const previousNet = finiteNumber(baseline.netLargeAmount);
    const threshold = previousNet === null ? null : Math.abs(previousNet);
    const netFundingRate = baselineAbsoluteNetFundingRate(baseline);
    const key = `${tradeDate}:${ticker}`;
    if (!ticker
      || emitted.has(key)
      || !threshold
      || previousNet === null
      || previousNet >= -INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT
      || netFundingRate === null
      || netFundingRate <= INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE
      || baseline.dataDate >= tradeDate) continue;

    for (const snapshot of extraLargeSnapshots(tradeDate, barsByTicker.get(ticker) ?? [])) {
      const { bar, buyAmount: cumulativeBuy, netAmount } = snapshot;
      if (cumulativeBuy < threshold || netAmount <= 0) continue;
      const barPrice = finiteNumber(bar.close);
      const price = barPrice !== null && barPrice > 0 ? barPrice : finiteNumber(baseline.fallbackPrice);
      if (price === null || price <= 0) break;
      emitted.add(key);
      results.push({
        tradeDate,
        ticker,
        name: baseline.name || ticker,
        kind: INTRADAY_EXTRA_LARGE_BUY_KIND,
        label: INTRADAY_EXTRA_LARGE_BUY_LABEL,
        barTs: Math.trunc(bar.ts),
        price,
        note: `前日大單淨額 -${formatAmount(threshold)}｜大單淨額（隔日沖）資金占比 ${netFundingRate.toFixed(2)}%\n盤中大單買進累計 ${formatAmount(cumulativeBuy)}｜達成比例 ${(cumulativeBuy / threshold * 100).toFixed(1)}%｜${extraLargeForceNote(snapshot)}`,
      });
      break;
    }
  }

  return results.sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker));
}
