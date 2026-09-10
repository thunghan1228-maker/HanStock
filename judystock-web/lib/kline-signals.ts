import { detectLongSignals } from "./intraday-strategy/longStrategy.ts";
import { detectShortSignals, SIGNAL_LABELS, type Bar5m } from "./intraday-strategy/shortStrategy.ts";

export type KlineReplaySignal = {
  id: number;
  tradeDate: string;
  ticker: string;
  name: string;
  groupName: string;
  kind: string;
  label: string;
  barTs: number;
  price: number;
  ma20Down: boolean | null;
  note: string | null;
  notified: boolean;
  createdAt: string;
  source: "kline-replay";
};

const TAIPEI_OFFSET = 8 * 3_600_000;
const dayOf = (ts: number) => new Date(ts + TAIPEI_OFFSET).toISOString().slice(0, 10);

function candleTimestamp(row: Record<string, unknown>, now: number) {
  const ts = Number(row.ts);
  if (Number.isFinite(ts) && ts > 0) return ts;
  const match = String(row.date ?? "").match(/^(?:(\d{4})[/-])?(\d{2})[/-](\d{2})[ T](\d{2}):(\d{2})$/);
  if (!match) return NaN;
  const date = new Date(Date.UTC(Number(match[1] ?? new Date(now + TAIPEI_OFFSET).getUTCFullYear()), Number(match[2]) - 1, Number(match[3]), Number(match[4]) - 8, Number(match[5])));
  // Month/day labels around New Year belong to the most recent past year.
  if (!match[1] && date.getTime() > now + 86400_000) date.setUTCFullYear(date.getUTCFullYear() - 1);
  return date.getTime();
}

/** Replay the original engines against the same repaired OHLC series as the chart.
 * Each trading day gets its own counters, with prior bars retained for MA warmup.
 * This produces chart markers only; it does not send alerts or overwrite history.
 */
export function replayKlineSignals(ticker: string, candles: Record<string, unknown>[], now = Date.now()): KlineReplaySignal[] {
  const byTimestamp = new Map<number, Bar5m>();
  for (const row of candles) {
    const ts = candleTimestamp(row, now);
    const prices = [row.open, row.high, row.low, row.close].map(Number);
    if (!Number.isFinite(ts) || ts <= 0 || ts > now || !prices.every(price => Number.isFinite(price) && price > 0)) continue;
    const time = new Date(ts + TAIPEI_OFFSET);
    const minute = time.getUTCHours() * 60 + time.getUTCMinutes();
    if (minute < 540 || minute >= 810 || minute % 5 !== 0 || ts % 60_000 !== 0) continue;
    byTimestamp.set(ts, { ts, open: prices[0], high: prices[1], low: prices[2], close: prices[3], volume: Math.max(0, Number(row.volume) || 0) });
  }
  const bars = [...byTimestamp.values()].sort((a, b) => a.ts - b.ts);
  const signals: KlineReplaySignal[] = [];
  let start = 0;
  for (let end = 1; end <= bars.length; end++) {
    if (end < bars.length && dayOf(bars[end].ts) === dayOf(bars[end - 1].ts)) continue;
    const first = new Date(bars[start].ts + TAIPEI_OFFSET);
    // Without the actual opening candle, a later bar must never become the 905 reference.
    if (first.getUTCHours() === 9 && first.getUTCMinutes() === 0) {
      const history = bars.slice(0, end);
      const detected = [...detectShortSignals(history), ...detectLongSignals(history)];
      for (const signal of detected) {
        const note = signal.seq !== undefined ? String(signal.seq) : "note" in signal ? signal.note ?? null : null;
        const key = `${ticker}|${signal.kind}|${signal.ts}`;
        let hash = 2166136261;
        for (let i = 0; i < key.length; i++) hash = Math.imul(hash ^ key.charCodeAt(i), 16777619);
        signals.push({
          id: -((hash >>> 0) || 1), ticker, name: ticker, groupName: "",
          tradeDate: dayOf(signal.ts), kind: signal.kind, barTs: signal.ts,
          price: signal.price, ma20Down: signal.ma20Down, note,
          label: signal.kind === "ma20turn" ? note === "down" ? "20MA↓" : "20MA↑" : signal.label ?? SIGNAL_LABELS[signal.kind as keyof typeof SIGNAL_LABELS] ?? signal.kind,
          notified: false, createdAt: new Date(signal.ts).toISOString(), source: "kline-replay",
        });
      }
    }
    start = end;
  }
  return signals.sort((a, b) => a.barTs - b.barTs || a.id - b.id);
}

export function mergeKlineSignals(stored: Record<string, unknown>[], replayed: KlineReplaySignal[], ticker: string, sinceTs?: number, date?: string) {
  const merged = new Map<string, Record<string, unknown>>();
  const metadata = stored.find(row => row.ticker === ticker);
  for (const row of replayed) merged.set(`${row.ticker}|${row.kind}|${row.barTs}`, { ...row, name: metadata?.name || row.name, groupName: metadata?.groupName || row.groupName });
  // Real persisted observations win, even if their intrabar count differs from replay.
  for (const row of stored) merged.set(`${row.ticker}|${row.kind}|${row.barTs}`, row);
  return [...merged.values()].filter(row => row.ticker === ticker
    && (!sinceTs || Number(row.barTs) >= sinceTs)
    && (!date || row.tradeDate === date))
    .sort((a, b) => Number(a.barTs) - Number(b.barTs) || Number(a.id ?? 0) - Number(b.id ?? 0));
}
