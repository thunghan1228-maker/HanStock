export const INTRADAY_LARGE_FORCE_BUY_KIND = "intradayLargeForceBuy" as const;
export const INTRADAY_LARGE_FORCE_SELL_KIND = "intradayLargeForceSell" as const;

export const INTRADAY_LARGE_FORCE_THRESHOLD_PCT = 12;
export const INTRADAY_LARGE_FORCE_STRONG_THRESHOLD_PCT = 28;
export const INTRADAY_LARGE_FORCE_MIN_TURNOVER_AMOUNT = 100_000_000;
export const INTRADAY_LARGE_FORCE_MIN_NET_AMOUNT = 30_000_000;

// AJ 多空精篩：正式盤中大戶力本身採更嚴格的 ±12%，這裡仍保留
// AJ 畫面可確認的 ±10% 條件，並再疊加成交額與族群強弱反轉。
export const AJ_LARGE_FORCE_MIN_PCT = 10;
export const AJ_LARGE_FORCE_MAX_PCT = -10;
export const AJ_LARGE_FORCE_MIN_TURNOVER_AMOUNT = 300_000_000;
export const AJ_RECENT_STRONG_GROUP_RANK_LIMIT = 20;
export const AJ_CURRENT_WEAK_GROUP_RANK_START = 21;
export const AJ_RECENT_WEAK_GROUP_RANK_START = 48;
export const AJ_CURRENT_STRONG_GROUP_RANK_LIMIT = 20;

export type IntradayLargeForceMinuteBar = {
  ts: number;
  close: number;
  volume: number;
  main_buy_amount?: number | null;
  main_sell_amount?: number | null;
  main_force_available?: boolean | null;
};

export type IntradayLargeForceSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: typeof INTRADAY_LARGE_FORCE_BUY_KIND | typeof INTRADAY_LARGE_FORCE_SELL_KIND;
  label: string;
  barTs: number;
  price: number;
  note: string;
};

export type IntradayLargeForceValue = {
  tradeDate: string;
  barTs: number;
  forcePct: number;
  price: number;
  buyAmount: number;
  sellAmount: number;
  netAmount: number;
  turnoverAmount: number;
};

type IntradaySignalCandidate = {
  tradeDate: string;
  ticker: string;
  kind: string;
  barTs: number;
};

type InstantLargeSignalCandidate = {
  tradeDate: string;
  ticker: string;
  kind: string;
  barTs: number;
  note: string;
};

export type AjLargeForceGroupTransition = {
  ticker: string;
  qualifyingGroup: string | null;
  currentRank: number | null;
  bestRecentRank: number | null;
  recentRanks: number[];
  bullishQualifyingGroup: string | null;
  bullishCurrentRank: number | null;
  worstRecentRank: number | null;
  bullishRecentRanks: number[];
};

type AjLargeForceSignalCandidate = IntradaySignalCandidate & {
  note: string;
};

function amountMultiplier(unit: string) {
  return unit === "億" ? 100_000_000 : unit === "萬" ? 10_000 : 1;
}

export function parseIntradayLargeForceNote(note: string) {
  const forceMatch = note.match(/盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u);
  const turnoverMatch = note.match(/成交額\s+([0-9,.]+)\s*(億|萬|元)/u);
  const forcePct = forceMatch ? Number(forceMatch[1]) : null;
  const amount = turnoverMatch ? Number(turnoverMatch[1].replace(/,/g, "")) : null;
  return {
    forcePct: forcePct !== null && Number.isFinite(forcePct) ? forcePct : null,
    turnoverAmount: amount !== null && Number.isFinite(amount) && turnoverMatch
      ? amount * amountMultiplier(turnoverMatch[2])
      : null,
  };
}

/**
 * 「盤中大戶力」獨立頁籤的 AJ 多空精篩。空方使用同一族群近三日
 * 曾進前 20、今日跌出前 20；多方則要求同一族群近三日曾落後 20、
 * 今日進入前 20。兩邊都必須同時通過大戶力與成交額門檻。
 */
export function filterAjIntradayLargeForceSignals<T extends AjLargeForceSignalCandidate>(
  signals: T[],
  transitionsByTicker: Record<string, AjLargeForceGroupTransition | undefined>,
) {
  return signals.filter((signal) => {
    const { forcePct, turnoverAmount } = parseIntradayLargeForceNote(signal.note);
    const transition = transitionsByTicker[signal.ticker.trim().toUpperCase()];
    const passesSharedAmount = turnoverAmount !== null
      && turnoverAmount >= AJ_LARGE_FORCE_MIN_TURNOVER_AMOUNT;
    if (signal.kind === INTRADAY_LARGE_FORCE_BUY_KIND) {
      return forcePct !== null
        && forcePct >= AJ_LARGE_FORCE_MIN_PCT
        && passesSharedAmount
        && transition?.bullishQualifyingGroup !== null
        && typeof transition?.bullishQualifyingGroup === "string"
        && typeof transition.bullishCurrentRank === "number"
        && transition.bullishCurrentRank <= AJ_CURRENT_STRONG_GROUP_RANK_LIMIT
        && typeof transition.worstRecentRank === "number"
        && transition.worstRecentRank >= AJ_RECENT_WEAK_GROUP_RANK_START;
    }
    if (signal.kind !== INTRADAY_LARGE_FORCE_SELL_KIND) return false;
    return forcePct !== null
      && forcePct <= AJ_LARGE_FORCE_MAX_PCT
      && passesSharedAmount
      && transition?.qualifyingGroup !== null
      && typeof transition?.qualifyingGroup === "string"
      && typeof transition.currentRank === "number"
      && transition.currentRank >= AJ_CURRENT_WEAK_GROUP_RANK_START
      && typeof transition.bestRecentRank === "number"
      && transition.bestRecentRank <= AJ_RECENT_STRONG_GROUP_RANK_LIMIT;
  });
}

/**
 * HanStock 目前先以既有盤中訊號建立候選池，再用大戶力門檻做第二層
 * 篩選。候選池裡同一股票即使因不同戰法重複出現，也只視為同一個候選；
 * 大戶力則保留多、空各自第一次達標的時間。AJ 的族群與成交額前置條件
 * 仍在回測，不在證據不足時直接拿來刪除正式訊號。
 */
export function filterCandidateIntradayLargeForceSignals<T extends IntradaySignalCandidate>(
  largeForceSignals: T[],
  candidateSignals: IntradaySignalCandidate[],
) {
  const candidateKeys = new Set(candidateSignals
    .filter((signal) => signal.kind !== INTRADAY_LARGE_FORCE_BUY_KIND && signal.kind !== INTRADAY_LARGE_FORCE_SELL_KIND)
    .map((signal) => `${signal.tradeDate}:${signal.ticker.trim().toUpperCase()}`));
  const earliestByDirection = new Map<string, T>();
  for (const signal of largeForceSignals) {
    const ticker = signal.ticker.trim().toUpperCase();
    if (!candidateKeys.has(`${signal.tradeDate}:${ticker}`)) continue;
    const key = `${signal.tradeDate}:${ticker}:${signal.kind}`;
    const previous = earliestByDirection.get(key);
    if (!previous || signal.barTs < previous.barTs) earliestByDirection.set(key, signal);
  }
  return [...earliestByDirection.values()].sort((left, right) => left.barTs - right.barTs || left.ticker.localeCompare(right.ticker));
}

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const FORMAL_START_MINUTE = SESSION_START_MINUTE + 5;
const SESSION_END_MINUTE = 13 * 60 + 30;

function finiteNumber(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function timestampMs(value: unknown) {
  const number = finiteNumber(value);
  if (number === null || number <= 0) return null;
  return number < 1_000_000_000_000 ? number * 1_000 : number;
}

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function formatAmount(value: number, showSign = false) {
  const absolute = Math.abs(value);
  const sign = showSign ? value > 0 ? "+" : value < 0 ? "-" : "" : "";
  if (absolute >= 100_000_000) return `${sign}${(absolute / 100_000_000).toFixed(2)} 億`;
  if (absolute >= 10_000) return `${sign}${(absolute / 10_000).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 萬`;
  return `${sign}${Math.round(absolute).toLocaleString("zh-TW")} 元`;
}

function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

type Direction = "buy" | "sell";
type TriggerSnapshot = {
  direction: Direction;
  tradeDate: string;
  ts: number;
  price: number;
  buyAmount: number;
  sellAmount: number;
  netAmount: number;
  turnoverAmount: number;
  forcePct: number;
};

/**
 * 回傳指定交易日目前最新的盤中大戶力，不套用 ±12% 訊號門檻。
 * 盤中訊號中心用這個值替每一檔股票補上同口徑的方向標籤。
 */
export function calculateIntradayLargeForceValue(
  inputBars: IntradayLargeForceMinuteBar[],
  requestedTradeDate?: string,
  cutoffTimestamp?: number,
): IntradayLargeForceValue | null {
  const normalized = inputBars.flatMap((bar) => {
    const ts = timestampMs(bar.ts);
    if (ts === null) return [];
    const { tradeDate, minute } = taipeiParts(ts);
    if (minute < SESSION_START_MINUTE || minute > SESSION_END_MINUTE) return [];
    return [{ ...bar, ts, tradeDate }];
  });
  const tradeDate = requestedTradeDate
    ?? normalized.map((bar) => bar.tradeDate).sort().at(-1);
  if (!tradeDate) return null;

  let cumulativeBuy = 0;
  let cumulativeSell = 0;
  let cumulativeTurnover = 0;
  let latestBarTs = 0;
  const cutoff = timestampMs(cutoffTimestamp);
  const cutoffMinute = cutoff === null ? null : Math.floor(cutoff / 60_000);
  for (const bar of normalized
    .filter((row) => row.tradeDate === tradeDate && (cutoffMinute === null || Math.floor(row.ts / 60_000) <= cutoffMinute))
    .sort((left, right) => left.ts - right.ts)) {
    const close = finiteNumber(bar.close);
    const volume = finiteNumber(bar.volume);
    const buy = finiteNumber(bar.main_buy_amount);
    const sell = finiteNumber(bar.main_sell_amount);
    if (close === null || close <= 0 || volume === null || volume < 0 || bar.main_force_available === false || (buy === null && sell === null)) continue;
    cumulativeBuy += Math.max(0, buy ?? 0);
    cumulativeSell += Math.max(0, sell ?? 0);
    cumulativeTurnover += close * volume * 1_000;
    latestBarTs = bar.ts;
  }
  // Hub 尚未完成逐筆分類時可能先回 0/0。這是「未取得」，不是 0% 大戶力。
  if (latestBarTs <= 0 || cumulativeTurnover <= 0 || cumulativeBuy + cumulativeSell <= 0) return null;
  return {
    tradeDate,
    barTs: latestBarTs,
    forcePct: (cumulativeBuy - cumulativeSell) / cumulativeTurnover * 100,
    price: normalized
      .filter((row) => row.tradeDate === tradeDate && row.ts === latestBarTs)
      .map((row) => finiteNumber(row.close) ?? 0)
      .at(-1) ?? 0,
    buyAmount: cumulativeBuy,
    sellAmount: cumulativeSell,
    netAmount: cumulativeBuy - cumulativeSell,
    turnoverAmount: cumulativeTurnover,
  };
}

const INSTANT_TRIGGER_FORCE_MARKER = "觸發當時盤中大戶力";

export function qualifyInstantLargeSignalByTriggerForce<T extends InstantLargeSignalCandidate>(
  signal: T,
  forcePct: number | null,
): T | null {
  if (signal.kind !== "instantLargeBuy" && signal.kind !== "instantLargeSell") return signal;
  if (forcePct === null || !Number.isFinite(forcePct)) return null;
  if (signal.kind === "instantLargeBuy" ? forcePct <= 0 : forcePct >= 0) return null;
  const cleanedNote = signal.note
    .replace(/(?:｜)?觸發當時盤中大戶力\s+[+-]?\d+(?:\.\d+)?%/gu, "")
    .replace(/^｜|｜$/gu, "");
  return {
    ...signal,
    note: [cleanedNote, `${INSTANT_TRIGGER_FORCE_MARKER} ${signedPercent(forcePct)}`].filter(Boolean).join("｜"),
  };
}

export function hasQualifiedInstantLargeTriggerForce(signal: InstantLargeSignalCandidate) {
  if (signal.kind !== "instantLargeBuy" && signal.kind !== "instantLargeSell") return true;
  const matched = signal.note.match(/觸發當時盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u);
  const forcePct = matched ? Number(matched[1]) : Number.NaN;
  return Number.isFinite(forcePct) && (signal.kind === "instantLargeBuy" ? forcePct > 0 : forcePct < 0);
}

/**
 * 盤中大戶力使用既有逐筆大單分類後的 1 分 K 金額：
 * （累計大單買進－累計大單賣出）÷ 累計成交額。
 *
 * 為避免開盤分母過小與單分鐘雜訊，09:05 後才判斷，並要求成交額、
 * 淨額與大戶力門檻連續兩根 1 分 K 同方向通過。每檔每日每方向只保留
 * 第一次確認，供彈窗與歷史查詢共用。
 */
export function calculateIntradayLargeForceSignals(
  ticker: string,
  name: string,
  inputBars: IntradayLargeForceMinuteBar[],
) {
  const grouped = new Map<string, IntradayLargeForceMinuteBar[]>();
  for (const bar of inputBars) {
    const ts = timestampMs(bar.ts);
    if (ts === null) continue;
    const { tradeDate, minute } = taipeiParts(ts);
    if (minute < SESSION_START_MINUTE || minute > SESSION_END_MINUTE) continue;
    const rows = grouped.get(tradeDate) ?? [];
    rows.push({ ...bar, ts });
    grouped.set(tradeDate, rows);
  }

  const results: IntradayLargeForceSignal[] = [];
  for (const [tradeDate, bars] of grouped) {
    let cumulativeBuy = 0;
    let cumulativeSell = 0;
    let cumulativeTurnover = 0;
    let previousDirection: Direction | null = null;
    let consecutive = 0;
    const triggers = new Map<Direction, TriggerSnapshot>();

    for (const bar of [...bars].sort((left, right) => left.ts - right.ts)) {
      const close = finiteNumber(bar.close);
      const volume = finiteNumber(bar.volume);
      const buy = finiteNumber(bar.main_buy_amount);
      const sell = finiteNumber(bar.main_sell_amount);
      if (close === null || close <= 0 || volume === null || volume < 0 || bar.main_force_available === false || (buy === null && sell === null)) {
        previousDirection = null;
        consecutive = 0;
        continue;
      }
      cumulativeBuy += Math.max(0, buy ?? 0);
      cumulativeSell += Math.max(0, sell ?? 0);
      cumulativeTurnover += close * volume * 1_000;

      const { minute } = taipeiParts(bar.ts);
      const netAmount = cumulativeBuy - cumulativeSell;
      const forcePct = cumulativeTurnover > 0 ? netAmount / cumulativeTurnover * 100 : 0;
      const passesAmount = cumulativeTurnover >= INTRADAY_LARGE_FORCE_MIN_TURNOVER_AMOUNT
        && Math.abs(netAmount) >= INTRADAY_LARGE_FORCE_MIN_NET_AMOUNT;
      const direction: Direction | null = minute >= FORMAL_START_MINUTE && passesAmount
        ? forcePct >= INTRADAY_LARGE_FORCE_THRESHOLD_PCT
          ? "buy"
          : forcePct <= -INTRADAY_LARGE_FORCE_THRESHOLD_PCT
            ? "sell"
            : null
        : null;

      if (direction === null) {
        previousDirection = null;
        consecutive = 0;
        continue;
      }
      consecutive = direction === previousDirection ? consecutive + 1 : 1;
      previousDirection = direction;
      if (consecutive < 2 || triggers.has(direction)) continue;
      triggers.set(direction, {
        direction,
        tradeDate,
        ts: bar.ts,
        price: close,
        buyAmount: cumulativeBuy,
        sellAmount: cumulativeSell,
        netAmount,
        turnoverAmount: cumulativeTurnover,
        forcePct,
      });
    }

    for (const snapshot of triggers.values()) {
      const bullish = snapshot.direction === "buy";
      const strong = Math.abs(snapshot.forcePct) >= INTRADAY_LARGE_FORCE_STRONG_THRESHOLD_PCT;
      results.push({
        tradeDate: snapshot.tradeDate,
        ticker: ticker.trim().toUpperCase(),
        name: name || ticker,
        kind: bullish ? INTRADAY_LARGE_FORCE_BUY_KIND : INTRADAY_LARGE_FORCE_SELL_KIND,
        label: bullish
          ? strong ? "盤中大戶強力買進" : "盤中大戶偏買"
          : strong ? "盤中大戶強力賣出" : "盤中大戶偏賣",
        barTs: snapshot.ts,
        price: snapshot.price,
        note: [
          `盤中大戶力 ${signedPercent(snapshot.forcePct)}`,
          `大戶買進 ${formatAmount(snapshot.buyAmount)}`,
          `大戶賣出 ${formatAmount(snapshot.sellAmount)}`,
          `大戶淨額 ${formatAmount(snapshot.netAmount, true)}`,
          `成交額 ${formatAmount(snapshot.turnoverAmount)}`,
          `連續 2 根 1 分 K 確認`,
          `提醒門檻 ±${INTRADAY_LARGE_FORCE_THRESHOLD_PCT}%`,
        ].join("｜"),
      });
    }
  }

  return results.sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker));
}
