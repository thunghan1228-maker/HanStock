export type TrendInterval = "1m" | "5m" | "1d";
export type TrendCandle = { date: string; close: number; high?: number; low?: number; volume?: number };
export type TrendPoint = { date: string; session: string; close: number; ma5: number | null; ma20: number | null; vwap: number | null };
export type TrendTone = "above" | "below" | "neutral";

export function trendPriceTone(point: Pick<TrendPoint, "close" | "vwap"> | undefined): TrendTone {
  return point?.vwap == null ? "neutral" : point.close > point.vwap ? "above" : point.close < point.vwap ? "below" : "neutral";
}

/** Split exactly at the price/VWAP intersection, including a moving VWAP. */
export function trendPriceSegments(points: TrendPoint[], interval: TrendInterval) {
  const segments: { tone: TrendTone; from: { index: number; price: number }; to: { index: number; price: number } }[] = [];
  for (let index = 1; index < points.length; index++) {
    const a = points[index - 1], b = points[index];
    // Intraday VWAP restarts each session; there is no overnight VWAP segment.
    if (interval !== "1d" && a.session !== b.session) continue;
    const from = { index: index - 1, price: a.close }, to = { index, price: b.close };
    if (a.vwap === null || b.vwap === null) { segments.push({ tone: "neutral", from, to }); continue; }
    const da = a.close - a.vwap, db = b.close - b.vwap;
    if (da * db < 0) {
      const fraction = da / (da - db);
      const crossing = { index: index - 1 + fraction, price: a.close + (b.close - a.close) * fraction };
      segments.push({ tone: da > 0 ? "above" : "below", from, to: crossing }, { tone: db > 0 ? "above" : "below", from: crossing, to });
    } else {
      const difference = da || db;
      segments.push({ tone: difference > 0 ? "above" : difference < 0 ? "below" : "neutral", from, to });
    }
  }
  return segments;
}

/** Keep trading sessions, not calendar days. Calculate MAs before trimming history. */
export function buildWatchlistTrend(candles: TrendCandle[], interval: TrendInterval, now = Date.now()): TrendPoint[] {
  const currentDate = new Date(now + 28_800_000).toISOString().slice(0, 10);
  const currentYear = Number(currentDate.slice(0, 4));
  const byDate = new Map<string, TrendCandle & { session: string }>();
  for (const candle of candles) {
    if (!candle || typeof candle.date !== "string" || !Number.isFinite(candle.close) || candle.close <= 0) continue;
    const match = candle.date.match(/^(?:(\d{4})[/-])?(\d{2})[/-](\d{2})(?:\s+(\d{2}:\d{2}))?/);
    if (!match) continue;
    let year = Number(match[1] ?? currentYear);
    if (!match[1] && `${year}-${match[2]}-${match[3]}` > currentDate) year--;
    const session = `${year}-${match[2]}-${match[3]}`;
    const date = session + (match[4] ? ` ${match[4]}` : "");
    byDate.set(date, { ...candle, date, session });
  }
  const all = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  const sessions = new Set([...new Set(all.map(row => row.session))].slice(-3));
  const visibleStart = interval === "1d" ? Math.max(0, all.length - 120) : all.findIndex(row => sessions.has(row.session));
  let activeSession = "", priceVolume = 0, volume = 0;
  const movingAverage = (index: number, period: number) => index < period - 1 ? null
    : all.slice(index - period + 1, index + 1).reduce((sum, row) => sum + row.close, 0) / period;
  return all.flatMap((row, index) => {
    if (index < visibleStart) return [];
    if (interval !== "1d" && row.session !== activeSession) {
      activeSession = row.session;
      priceVolume = 0;
      volume = 0;
    }
    const amount = Number.isFinite(row.volume) && row.volume! > 0 ? row.volume! : 0;
    const high = Number.isFinite(row.high) ? row.high! : row.close;
    const low = Number.isFinite(row.low) ? row.low! : row.close;
    priceVolume += (high + low + row.close) / 3 * amount;
    volume += amount;
    return [{ date: row.date, session: row.session, close: row.close,
      ma5: movingAverage(index, 5), ma20: movingAverage(index, 20), vwap: volume > 0 ? priceVolume / volume : null }];
  });
}

export function trendMacd(points: TrendPoint[]) {
  let fast = points[0]?.close ?? 0, slow = fast, signal = 0;
  return points.map(point => {
    fast += (point.close - fast) * 2 / 13;
    slow += (point.close - slow) * 2 / 27;
    const dif = fast - slow;
    signal += (dif - signal) * 2 / 10;
    return { dif, signal, histogram: (dif - signal) * 2 };
  });
}

export function watchlistQuoteTone(change: number | null | undefined, percentage: string) {
  const value = typeof change === "number" && Number.isFinite(change) ? change : Number(percentage.replace(/[% ,]/g, ""));
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}
