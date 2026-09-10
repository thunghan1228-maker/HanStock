export type KlineSignalGlyph = {
  id: string; date: string; kind: string; note?: string; label: string;
  side: "up" | "down"; source: "native" | "overlay";
  x: number; anchorY: number;
};

/** Deduplicate events; break15kLow remains independent of candle/side groups. */
export function groupKlineSignals(items: KlineSignalGlyph[]) {
  const unique = new Map<string, KlineSignalGlyph>();
  for (const item of items) {
    const key = `${item.date}|${item.kind}|${item.kind === "ma20turn" ? item.note ?? item.side : ""}`;
    const previous = unique.get(key);
    if (!previous || item.source === "overlay") unique.set(key, item);
  }
  const groups = new Map<string, { date: string; side: "up" | "down"; x: number; anchorY: number; items: KlineSignalGlyph[] }>();
  for (const item of unique.values()) {
    const key = item.kind === "break15kLow" ? `break15kLow|${item.id}` : `${item.date}|${item.side}`;
    const group = groups.get(key) ?? { date: item.date, side: item.side, x: item.x, anchorY: item.anchorY, items: [] };
    group.items.push(item);
    groups.set(key, group);
  }
  // Keep ordinary groups nearest the candle, even when the market overlay arrives first.
  return [...groups.values()].sort((a, b) => a.x - b.x || a.side.localeCompare(b.side)
    || Number(a.items[0].kind === "break15kLow") - Number(b.items[0].kind === "break15kLow"));
}
