export type FiveMinutePatternMinuteBar = {
  ts: number;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close: number;
  volume?: number | null;
};

export type FiveMinutePatternHistoryContext = {
  previousTradeDate: string;
  previousHigh: number;
  previousCloses: number[];
  openingBar?: FiveMinutePatternMinuteBar;
};

export type FiveMinutePatternSignal = {
  tradeDate: string;
  ticker: string;
  name: string;
  kind: "fiveMinuteTwelveShort" | "fiveMinuteOnePlusTwoLong";
  label: string;
  barTs: number;
  price: number;
  note: string;
};

type FiveMinuteBar = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;
const SESSION_START_MINUTE = 9 * 60;
const SESSION_END_MINUTE = 13 * 60 + 30;
const FIVE_MINUTES_MS = 5 * 60_000;
export const VERIFIED_905_SOURCE_MARKER = "905來源 09:00完整五分K";

function taipeiParts(timestamp: number) {
  const date = new Date(timestamp + TAIPEI_OFFSET_MS);
  return {
    tradeDate: `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`,
    minute: date.getUTCHours() * 60 + date.getUTCMinutes(),
  };
}

function finite(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function formatPrice(value: number) {
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

export function aggregateFiveMinutePatternBars(rows: FiveMinutePatternMinuteBar[], tradeDate: string) {
  const buckets = new Map<number, FiveMinuteBar>();
  for (const row of [...rows].sort((left, right) => Number(left.ts) - Number(right.ts))) {
    const ts = Number(row.ts);
    const close = finite(row.close);
    if (!Number.isFinite(ts) || close === null) continue;
    const parts = taipeiParts(ts);
    if (parts.tradeDate !== tradeDate || parts.minute < SESSION_START_MINUTE || parts.minute > SESSION_END_MINUTE) continue;
    const bucketMinute = parts.minute === SESSION_END_MINUTE ? SESSION_END_MINUTE - 5 : Math.floor(parts.minute / 5) * 5;
    const bucketTs = ts - (parts.minute - bucketMinute) * 60_000 - (ts % 60_000);
    const high = finite(row.high) ?? close;
    const low = finite(row.low) ?? close;
    const open = finite(row.open) ?? close;
    const current = buckets.get(bucketTs);
    buckets.set(bucketTs, current ? {
      ...current,
      high: Math.max(current.high, high),
      low: Math.min(current.low, low),
      close,
    } : { ts: bucketTs, open, high, low, close });
  }
  return [...buckets.values()].sort((left, right) => left.ts - right.ts);
}

function movingAverage20(previousCloses: number[], session: FiveMinuteBar[]) {
  const rolling = previousCloses.filter((value) => Number.isFinite(value) && value > 0).slice(-19);
  if (rolling.length < 19) return [] as Array<number | null>;
  return session.map((bar) => {
    rolling.push(bar.close);
    if (rolling.length > 20) rolling.shift();
    return rolling.length === 20 ? rolling.reduce((sum, value) => sum + value, 0) / 20 : null;
  });
}

function onePlusTwoLong(
  ticker: string,
  name: string,
  tradeDate: string,
  bars: FiveMinuteBar[],
  context: FiveMinutePatternHistoryContext,
) {
  const opening = bars[0];
  if (!opening) return null;
  // 「1+2」必須同時站上昨日高與 905 高；只有過其中一條不成立。
  const matched = bars.slice(1).find((bar) => bar.close > context.previousHigh && bar.close > opening.high);
  if (!matched) return null;
  const defense = Math.max(context.previousHigh, opening.high);
  return {
    tradeDate,
    ticker,
    name,
    kind: "fiveMinuteOnePlusTwoLong",
    label: "1+2多（五分K）",
    barTs: matched.ts,
    price: matched.close,
    note: `五分K同時站上昨日高 ${formatPrice(context.previousHigh)} 與 905高 ${formatPrice(opening.high)}｜防守 ${formatPrice(defense)}｜只過其中一高不算 1+2｜${VERIFIED_905_SOURCE_MARKER}`,
  } satisfies FiveMinutePatternSignal;
}

function twelveShort(
  ticker: string,
  name: string,
  tradeDate: string,
  bars: FiveMinuteBar[],
  context: FiveMinutePatternHistoryContext,
) {
  if (bars.length < 5) return null;
  const opening = bars[0];
  const ma20 = movingAverage20(context.previousCloses, bars);
  if (ma20.length !== bars.length) return null;

  // 第一步：905 後先破 905 低。
  const broke905LowIndex = bars.findIndex((bar, index) => index > 0 && bar.low < opening.low);
  if (broke905LowIndex < 0) return null;

  // 第二步：「1高」是破惡前的反彈最高；破惡必須同時跌破 MA20 且 MA20 下彎。
  let breakEvilIndex = -1;
  let oneHigh = 0;
  for (let index = broke905LowIndex + 1; index < bars.length; index += 1) {
    const previousMa20 = ma20[index - 1];
    const currentMa20 = ma20[index];
    if (previousMa20 === null || currentMa20 === null) continue;
    const crossedBelow = bars[index - 1].close >= previousMa20 && bars[index].close < currentMa20;
    const maTurningDown = currentMa20 < previousMa20;
    if (!crossedBelow || !maTurningDown) continue;
    const candidates = bars.slice(broke905LowIndex, index).map((bar) => bar.high);
    oneHigh = Math.max(...candidates);
    // 反彈已過 905 高，就不是圖中定義的 1 高。
    if (!Number.isFinite(oneHigh) || oneHigh >= opening.high) return null;
    breakEvilIndex = index;
    break;
  }
  if (breakEvilIndex < 0) return null;

  // 第三步：破惡後必須真的出現第二次反彈；2 高未過 1 高，回落時才成立。
  let reboundStarted = false;
  let twoHigh = 0;
  for (let index = breakEvilIndex + 1; index < bars.length; index += 1) {
    const bar = bars[index];
    const previous = bars[index - 1];
    if (bar.high >= oneHigh) return null;
    if (bar.high > previous.high || bar.close > previous.close) reboundStarted = true;
    if (!reboundStarted) continue;
    twoHigh = Math.max(twoHigh, bar.high);
    const previousMa20 = ma20[index - 1];
    const currentMa20 = ma20[index];
    const resumedDown = bar.close < previous.close && bar.low < previous.low;
    const structureStillBearish = currentMa20 !== null && previousMa20 !== null
      && currentMa20 < previousMa20 && bar.close < currentMa20;
    if (!resumedDown || !structureStillBearish || twoHigh >= oneHigh) continue;
    return {
      tradeDate,
      ticker,
      name,
      kind: "fiveMinuteTwelveShort",
      label: "12空（五分K）",
      barTs: bar.ts,
      price: bar.close,
      note: `先破 905低 ${formatPrice(opening.low)}｜1高 ${formatPrice(oneHigh)}｜破惡：跌破 MA20 且均線下彎｜2高 ${formatPrice(twoHigh)} 不過 1高｜防守先看 1高、順利破底後改守 2高`,
    } satisfies FiveMinutePatternSignal;
  }
  return null;
}

export function calculateFiveMinutePatternSignals(
  ticker: string,
  name: string,
  minuteBars: FiveMinutePatternMinuteBar[],
  context: FiveMinutePatternHistoryContext | null | undefined,
  tradeDate: string,
  evaluatedAt = Date.now(),
) {
  if (!context || context.previousCloses.length < 19 || !Number.isFinite(context.previousHigh)) return [];
  const expectedOpeningTs = Date.parse(`${tradeDate}T09:00:00+08:00`);
  // 905 高低只能來自真正的 09:00～09:04 五分 K。歷史端若缺早盤資料，
  // 不可把午盤收到的第一根 K 棒冒充 905 K。
  if (!context.openingBar || Number(context.openingBar.ts) !== expectedOpeningTs) return [];
  // bars1m/batch 偏向回傳最新資料，午盤可能不含 09:00；固定把歷史端
  // 已完成的 905 K 補回，避免把稍晚的五分 K 誤認成第一根。
  const bars = aggregateFiveMinutePatternBars([
    ...(context.openingBar ? [context.openingBar] : []),
    ...minuteBars,
  ], tradeDate).filter((bar) => bar.ts + FIVE_MINUTES_MS <= evaluatedAt);
  if (bars.length < 2) return [];
  return [
    onePlusTwoLong(ticker, name, tradeDate, bars, context),
    twelveShort(ticker, name, tradeDate, bars, context),
  ].filter((signal): signal is FiveMinutePatternSignal => Boolean(signal));
}
