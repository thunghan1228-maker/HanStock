/** Display two observed trading sessions, retaining the full source for MA/force calculations.
 * Self-contained because the shared chart runtime embeds this same function.
 */
export function selectWatchlistCandleWindow<T extends { date?: string }>(candles: T[], view: string | null, intraday: boolean): T[] {
  if (view !== "watchlist" || !intraday || !candles.length) return candles;
  const sessions = [...new Set(candles.map(candle => candle.date?.split(/[ T]/)[0]).filter(Boolean))];
  const firstSession = sessions.slice(-2)[0];
  const start = candles.findIndex(candle => candle.date?.split(/[ T]/)[0] === firstSession);
  return start >= 0 ? candles.slice(start) : candles;
}
