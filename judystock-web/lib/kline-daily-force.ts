export type DailyCandleSlot = { date: string; x: number; width: number };

function dailyDate(value: string) {
  const match = value.match(/^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:$|[ T])/);
  return match ? `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}` : "";
}

/** Use the rendered price candles, which have their own date and viewport geometry.
 * Volume panels use different DOM nodes on daily/intraday layouts and may be hidden.
 */
export function readDailyCandleSlots(frame: HTMLIFrameElement, plot: HTMLDivElement): DailyCandleSlot[] {
  const frameBox = frame.getBoundingClientRect();
  const plotBox = plot.getBoundingClientRect();
  const frameScale = frame.offsetWidth ? frameBox.width / frame.offsetWidth : 1;
  const plotScale = plot.offsetWidth ? plotBox.width / plot.offsetWidth : 1;
  const width = plot.clientWidth * plotScale;
  if (!(width > 0) || !(frameScale > 0)) return [];
  const frameLeft = frameBox.left + frame.clientLeft * frameScale;
  const plotLeft = plotBox.left + plot.clientLeft * plotScale;
  const slots: DailyCandleSlot[] = [];
  for (const candle of Array.from(frame.contentDocument?.querySelectorAll<SVGGElement>("g[data-hanstock-bar-date]") ?? [])) {
    const date = dailyDate(candle.getAttribute("data-hanstock-bar-date") ?? "");
    const rect = candle.querySelector<SVGRectElement>("rect")?.getBoundingClientRect();
    if (!date || !rect || !(rect.width > 0)) continue;
    slots.push({ date,
      x: (frameLeft + (rect.left + rect.width / 2) * frameScale - plotLeft) / width * 1000,
      width: rect.width * frameScale / width * 1000 });
  }
  return slots;
}

export function alignDailyForce(slots: DailyCandleSlot[], points: Array<{ date: string; net: number }>) {
  let total = 0;
  const dated = new Map(points.filter(point => dailyDate(point.date) && Number.isFinite(point.net))
    .map(point => [dailyDate(point.date), { ...point, date: dailyDate(point.date) }]));
  const history = [...dated.values()].sort((a, b) => a.date.localeCompare(b.date))
    .map(point => ({ ...point, cumulative: total += point.net }));
  const min = Math.min(0, ...history.map(point => point.cumulative));
  const max = Math.max(0, ...history.map(point => point.cumulative));
  const range = Math.max(1, max - min);
  const byDate = new Map(history.map(point => [point.date, point]));
  const aligned = slots.map(slot => {
    const point = byDate.get(slot.date);
    return point ? { ...slot, ...point, y: 108 - ((point.cumulative - min) / range) * 96 } : null;
  });
  const segments: Array<{ from: NonNullable<typeof aligned[number]>; to: NonNullable<typeof aligned[number]>; missingDates: string[] }> = [];
  let previous = -1;
  aligned.forEach((point, index) => {
    if (!point) return;
    if (previous >= 0) segments.push({ from: aligned[previous]!, to: point,
      missingDates: slots.slice(previous + 1, index).map(slot => slot.date) });
    previous = index;
  });
  return {
    total,
    segments,
    maxAbs: Math.max(1, ...history.map(point => Math.abs(point.net))),
    // Missing dates stay empty, and never shift the following bar to an earlier candle.
    aligned,
  };
}

/** A sparse/failed response must not remove observations already read for this ticker. */
export function retainDailyForcePoints<T extends { date: string; net: number }>(current: T[], incoming: T[]): T[] {
  const points = new Map(current.map(point => [dailyDate(point.date), point]));
  for (const point of incoming) {
    if (dailyDate(point.date) && Number.isFinite(point.net)) points.set(dailyDate(point.date), point);
  }
  return [...points.values()].sort((a, b) => a.date.localeCompare(b.date));
}
