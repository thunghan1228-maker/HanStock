const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1_000;

export function technicalMarketDateAnchor(now = Date.now()) {
  const taipei = new Date(now + TAIPEI_OFFSET_MS);
  const anchor = new Date(Date.UTC(taipei.getUTCFullYear(), taipei.getUTCMonth(), taipei.getUTCDate()));
  const weekday = taipei.getUTCDay();
  const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();

  if (weekday === 6) anchor.setUTCDate(anchor.getUTCDate() - 1);
  else if (weekday === 0) anchor.setUTCDate(anchor.getUTCDate() - 2);
  else if (weekday === 1 && minutes < 8 * 60 + 40) anchor.setUTCDate(anchor.getUTCDate() - 3);

  return anchor;
}

export function completedDailyStrategyDateAnchor(now = Date.now()) {
  const taipei = new Date(now + TAIPEI_OFFSET_MS);
  const anchor = new Date(Date.UTC(taipei.getUTCFullYear(), taipei.getUTCMonth(), taipei.getUTCDate()));
  const minutes = taipei.getUTCHours() * 60 + taipei.getUTCMinutes();

  // The after-hours screener must never evaluate an unfinished daily candle.
  // Keep the previous weekday until the exchange's daily bar has had time to
  // settle after the 13:30 close; missing holidays naturally fall back to the
  // latest candle present in the fetched history.
  if (taipei.getUTCDay() === 0 || taipei.getUTCDay() === 6 || minutes < 14 * 60) {
    anchor.setUTCDate(anchor.getUTCDate() - 1);
  }
  while (anchor.getUTCDay() === 0 || anchor.getUTCDay() === 6) {
    anchor.setUTCDate(anchor.getUTCDate() - 1);
  }
  return anchor;
}
