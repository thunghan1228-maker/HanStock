export type SnapshotCandle = { date: string; open: number; high: number; low: number; close: number; volume?: number; [key: string]: unknown };
export type KlineSnapshot = { candles: SnapshotCandle[]; [key: string]: unknown };

/** Intraday labels are exchange-local, never the server's local timezone. */
export function cleanKlineSnapshot(snapshot: KlineSnapshot, interval?: string, now = Date.now()): KlineSnapshot {
  if (interval !== "1m" && interval !== "5m") return snapshot;
  const year = new Date(now + 8 * 3600_000).getUTCFullYear();
  const candles = snapshot.candles.flatMap(row => {
    const match = row.date?.match(/^(?:(\d{4})[/-])?(\d{2})[/-](\d{2}) (\d{2}):(\d{2})$/);
    if (!match) return [row];
    const minute = Number(match[5]);
    // A five-minute candle must start on its own grid. Do not promote quote-only
    // minute placeholders into OHLC bars or merge their prices into real bars.
    if (interval === "5m" && minute % 5 !== 0) return [];
    let ts = Date.parse(`${match[1] ?? year}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00+08:00`);
    if (!match[1] && Number(match[2]) > new Date(now + 8 * 3600_000).getUTCMonth() + 1) {
      ts = Date.parse(`${year - 1}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00+08:00`);
    }
    if (!Number.isFinite(ts) || ts > now) return [];
    return [{ ...row, ts }];
  });
  candles.sort((a,b) => Number(a.ts ?? 0) - Number(b.ts ?? 0) || a.date.localeCompare(b.date));
  return { ...snapshot, candles };
}

/** Reuse complete observations when a provider temporarily returns a partial window. */
export function mergeKlineSnapshot(saved: KlineSnapshot | null, incoming: KlineSnapshot, interval?: string): KlineSnapshot {
  if (saved) saved = cleanKlineSnapshot(saved, interval);
  incoming = cleanKlineSnapshot(incoming, interval);
  const byDate = new Map((saved?.candles ?? []).map(bar => [bar.date, bar]));
  for (const row of incoming.candles) {
    if (!row?.date || ![row.open, row.high, row.low, row.close].every(value => Number.isFinite(value) && value > 0)) continue;
    const old = byDate.get(row.date), next = { ...old, ...row };
    if (old?.mainForceAvailable && !row.mainForceAvailable) {
      for (const field of ["mainNetVolume", "mainBuyAmount", "mainSellAmount", "mainNetAmount", "mainTickCount", "mainForceAvailable"]) {
        if (field in old) next[field] = old[field]; else delete next[field];
      }
    }
    if (old && row.date.includes(" ") && Number.isFinite(old.volume) && Number.isFinite(row.volume) && old.volume! > row.volume!) {
      for (const field of ["open", "high", "low", "close", "volume"] as const) next[field] = old[field];
    }
    byDate.set(row.date, next);
  }
  const time = (bar: SnapshotCandle) => typeof bar.ts === "number" && bar.ts > 0 ? bar.ts : Date.parse(bar.date.replace(/^(\d{2}\/\d{2}) /, `${new Date().getFullYear()}/$1 `));
  const candles = [...byDate.values()].sort((a, b) => time(a) - time(b) || a.date.localeCompare(b.date));
  // Keep a month of intraday history and long daily MA warmup, with bounded storage.
  const sessions = [...new Set(candles.map(row => row.date.split(" ")[0]))];
  const daily = candles.every(row => !row.date.includes(" "));
  const keep = new Set(sessions.slice(daily ? -1000 : -31));
  return { ...saved, ...incoming, candles: candles.filter(row => keep.has(row.date.split(" ")[0])) };
}
