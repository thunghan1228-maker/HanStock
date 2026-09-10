import type { RiverMinuteBar } from "./river-intraday.ts";

/** Merge saved observations with the provider's often-truncated current window. */
export function mergeBlackDragonCandleWindows(saved: Array<Record<string, unknown>>, incoming: Array<Record<string, unknown>>, tradeDate: string) {
  const bars = new Map<number, RiverMinuteBar>();
  for (const raw of [...saved, ...incoming]) {
    const values = [raw.open, raw.high, raw.low, raw.close, raw.volume];
    if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) continue;
    const [open, high, low, close, volume] = values as number[];
    if (Math.min(open, high, low, close) <= 0 || high < Math.max(open, close) || low > Math.min(open, close) || volume < 0) continue;
    const value = raw.ts ?? raw.timestamp;
    let ts = typeof value === "number" && Number.isFinite(value) ? value < 10_000_000_000 ? value * 1000 : value : NaN;
    if (!Number.isFinite(ts)) {
      const date = String(raw.date ?? raw.time ?? "").trim();
      const full = date.match(/^(\d{4})[-/](\d{2})[-/](\d{2})[ T](\d{2}):(\d{2})/);
      const short = date.match(/^(\d{2})\/(\d{2}) (\d{2}):(\d{2})$/);
      if (full) ts = Date.UTC(+full[1], +full[2] - 1, +full[3], +full[4] - 8, +full[5]);
      else if (short && short[1] + "-" + short[2] === tradeDate.slice(5)) ts = Date.UTC(+tradeDate.slice(0,4), +short[1] - 1, +short[2], +short[3] - 8, +short[4]);
    }
    if (!Number.isFinite(ts)) continue;
    const local = new Date(ts + 8 * 60 * 60_000);
    if (local.toISOString().slice(0,10) !== tradeDate) continue;
    const minute = local.getUTCHours() * 60 + local.getUTCMinutes();
    if (minute < 540 || minute > 810) continue;
    bars.set(ts, { ts, open, high, low, close, volume });
  }
  return [...bars.values()].sort((left, right) => left.ts - right.ts);
}
