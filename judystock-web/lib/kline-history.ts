type IntradayBar = { ts?: unknown; date?: unknown; open?: unknown; high?: unknown; low?: unknown; close?: unknown };

/** Check the actual latest session, including Friday when opened on a weekend.
 * Upstream history_ok can be true even when bars is empty or stops at the open.
 */
export function hasIncompleteIntradaySession(bars: IntradayBar[], interval: "1m" | "5m", now = Date.now()) {
  const today = new Date(now + 8 * 60 * 60_000);
  const todayKey = today.toISOString().slice(0, 10);
  const bySession = new Map<string, Set<number>>();
  for (const bar of bars) {
    if (![bar.open, bar.high, bar.low, bar.close].every((value) => Number(value) > 0 && Number.isFinite(Number(value)))) continue;
    const timestamp = bar.ts == null ? NaN : Number(bar.ts);
    let date: Date;
    if (timestamp > 0 && Number.isFinite(timestamp)) {
      date = new Date(timestamp + 8 * 60 * 60_000);
    } else {
      const match = String(bar.date ?? "").match(/^(?:(\d{4})[/-])?(\d{2})[/-](\d{2})[ T](\d{2}):(\d{2})/);
      if (!match) continue;
      date = new Date(Date.UTC(Number(match[1] ?? today.getUTCFullYear()), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])));
      if (!match[1] && date.getTime() > today.getTime()) date.setUTCFullYear(date.getUTCFullYear() - 1);
    }
    if (!Number.isFinite(date.getTime()) || date.getTime() > today.getTime()) continue;
    const minute = date.getUTCHours() * 60 + date.getUTCMinutes();
    if (minute < 540 || minute > 810) continue;
    const key = date.toISOString().slice(0, 10);
    const minutes = bySession.get(key) ?? new Set<number>();
    minutes.add(minute);
    bySession.set(key, minutes);
  }
  const latest = [...bySession.keys()].sort().at(-1);
  if (!latest) return true;
  const step = interval === "5m" ? 5 : 1;
  // Allow publication lag during trading; historical sessions must reach the close.
  const elapsed = latest === todayKey ? today.getUTCHours() * 60 + today.getUTCMinutes() - 2 : 810;
  const expectedLast = Math.min(810 - step, Math.max(540, Math.floor(elapsed / step) * step - step));
  const minutes = [...bySession.get(latest)!].sort((a, b) => a - b);
  const expectedCount = Math.floor((expectedLast - 540) / step) + 1;
  return minutes[0] > 540 || minutes.at(-1)! < expectedLast || minutes.length < expectedCount * 0.8;
}
