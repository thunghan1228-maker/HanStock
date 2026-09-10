export type SignalBox = { id: string; x: number; y: number; width: number; height: number; anchorY: number; side: "up" | "down" };
export type SignalBounds = { left: number; right: number; top: number; bottom: number };

/** Pack complete glyph bounds, including counts/arrows, across neighboring candles. */
export function layoutKlineSignals(markers: SignalBox[], bounds: SignalBounds, gap = 4): SignalBox[] {
  const placed: SignalBox[] = [];
  const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));
  for (const marker of markers) {
    const halfW = marker.width / 2, halfH = marker.height / 2;
    const minX = bounds.left + halfW, maxX = Math.max(minX, bounds.right - halfW);
    const minY = bounds.top + halfH, maxY = Math.max(minY, bounds.bottom - halfH);
    const preferredX = clamp(marker.x, minX, maxX), preferredY = clamp(marker.y, minY, maxY);
    const xs = [...new Set([preferredX, minX, maxX, ...placed.flatMap(p => [
      clamp(p.x - p.width / 2 - halfW - gap, minX, maxX),
      clamp(p.x + p.width / 2 + halfW + gap, minX, maxX),
    ])])].sort((a, b) => Math.abs(a - preferredX) - Math.abs(b - preferredX));
    const find = (lo: number, hi: number) => {
      let best: { x: number; y: number; score: number } | undefined;
      if (lo > hi) return best;
      for (const x of xs) {
        const dx = Math.abs(x - preferredX) * 2;
        if (best && dx > best.score) break;
        const forbidden = placed.filter(p => Math.abs(p.x - x) < p.width / 2 + halfW + gap - 0.001)
          .map(p => [p.y - p.height / 2 - halfH - gap, p.y + p.height / 2 + halfH + gap])
          .filter(([a, b]) => b > lo && a < hi).sort((a, b) => a[0] - b[0]);
        let start = lo;
        const consider = (a: number, b: number) => {
          if (a > b) return;
          const y = clamp(preferredY, a, b), score = dx + Math.abs(y - preferredY);
          if (!best || score < best.score) best = { x, y, score };
        };
        for (const [a, b] of forbidden) { consider(start, Math.min(hi, a)); start = Math.max(start, b); if (start > hi) break; }
        consider(start, hi);
      }
      return best;
    };
    const preferred = marker.side === "up"
      ? find(minY, Math.min(maxY, marker.anchorY - halfH - gap))
      : find(Math.max(minY, marker.anchorY + halfH + gap), maxY);
    const position = preferred ?? find(minY, maxY);
    // Overflow goes above the price panel, never into volume or main-force data.
    const y = position?.y ?? Math.min(bounds.top - halfH - gap, ...placed.map(p => p.y - p.height / 2 - halfH - gap));
    placed.push({ ...marker, x: position?.x ?? preferredX, y });
  }
  return placed;
}

/** Fit every signal inside the price panel without resizing the candle viewport. */
export function fitKlineSignals(markers: SignalBox[], bounds: SignalBounds, gap = 4): (SignalBox & { scale: number })[] {
  if (!markers.length) return [];
  const area = Math.max(1, bounds.right - bounds.left) * Math.max(1, bounds.bottom - bounds.top);
  const occupied = markers.reduce((sum, marker) => sum + (marker.width + gap) * (marker.height + gap), 0);
  let scale = Math.min(1, Math.sqrt(area * 0.65 / Math.max(1, occupied)));
  for (let attempt = 0; attempt < 24; attempt++) {
    const placed = layoutKlineSignals(markers.map(marker => ({
      ...marker, width: marker.width * scale, height: marker.height * scale,
      y: marker.anchorY + (marker.side === "up" ? -1 : 1) * (marker.height / 2 + 10) * scale,
    })), bounds, gap * scale);
    if (placed.every(marker => marker.x - marker.width / 2 >= bounds.left - 0.001 &&
      marker.x + marker.width / 2 <= bounds.right + 0.001 &&
      marker.y - marker.height / 2 >= bounds.top - 0.001 &&
      marker.y + marker.height / 2 <= bounds.bottom + 0.001)) {
      return placed.map(marker => ({ ...marker, scale }));
    }
    scale *= 0.85;
  }
  // A bounded grid is a last resort for unusually large glyphs or crowded input.
  const width = bounds.right - bounds.left, height = bounds.bottom - bounds.top;
  const columns = Math.max(1, Math.min(markers.length, Math.ceil(Math.sqrt(markers.length * width / height))));
  const rows = Math.ceil(markers.length / columns), cellWidth = width / columns, cellHeight = height / rows;
  scale = Math.min(1, ...markers.map(marker => Math.min(cellWidth / (marker.width + gap), cellHeight / (marker.height + gap))));
  return markers.map((marker, index) => ({ ...marker,
    x: bounds.left + (index % columns + 0.5) * cellWidth,
    y: bounds.top + (Math.floor(index / columns) + 0.5) * cellHeight,
    width: marker.width * scale, height: marker.height * scale, scale }));
}
