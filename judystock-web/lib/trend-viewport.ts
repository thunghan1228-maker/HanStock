export type TrendWindow = { start: number; end: number };
export type TrendBookmark = { startDate: string; count: number; followLatest: boolean };

/** End is exclusive; a viewport always remains inside the loaded history. */
export function clampTrendWindow(window: TrendWindow, length: number): TrendWindow {
  if (length <= 0) return { start: 0, end: 0 };
  const count = Math.max(Math.min(2, length), Math.min(length, Math.round(window.end - window.start)));
  const start = Math.max(0, Math.min(length - count, Math.round(window.start)));
  return { start, end: start + count };
}

export function zoomTrendWindow(window: TrendWindow, length: number, factor: number, anchor = .5): TrendWindow {
  const current = clampTrendWindow(window, length);
  if (!length || !Number.isFinite(factor) || factor <= 0) return current;
  const fraction = Math.max(0, Math.min(1, anchor));
  const previousCount = current.end - current.start;
  let desiredCount = Math.round(previousCount * factor);
  // Small trackpad deltas must still allow zooming out from a narrow range.
  if (desiredCount === previousCount && factor !== 1) desiredCount += factor > 1 ? 1 : -1;
  const count = Math.max(Math.min(2, length), Math.min(length, desiredCount));
  const point = current.start + (current.end - current.start - 1) * fraction;
  const start = Math.round(point - (count - 1) * fraction);
  return clampTrendWindow({ start, end: start + count }, length);
}

export function panTrendWindow(window: TrendWindow, length: number, offset: number): TrendWindow {
  return clampTrendWindow({ start: window.start + offset, end: window.end + offset }, length);
}

export function resizeTrendWindow(window: TrendWindow, length: number, side: "start" | "end", index: number): TrendWindow {
  const current = clampTrendWindow(window, length), minimum = Math.min(2, length);
  return side === "start"
    ? { start: Math.max(0, Math.min(current.end - minimum, Math.round(index))), end: current.end }
    : { start: current.start, end: Math.min(length, Math.max(current.start + minimum, Math.round(index))) };
}

export function restoreTrendWindow(points: { date: string }[], bookmark: TrendBookmark | null): TrendWindow {
  if (!bookmark) return { start: 0, end: points.length };
  const index = bookmark.followLatest ? points.length - bookmark.count : points.findIndex(point => point.date >= bookmark.startDate);
  const start = index < 0 ? points.length - bookmark.count : index;
  return clampTrendWindow({ start, end: start + bookmark.count }, points.length);
}
