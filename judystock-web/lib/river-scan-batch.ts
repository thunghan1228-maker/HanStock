/** Scan complete groups so the six-stock limit and original group ranks hold. */
export function riverScanBatch(order: { bull: string[]; bear: string[] }, nextIndex = 0) {
  const total = Math.max(order.bull.length, order.bear.length, 1);
  const index = Number.isInteger(nextIndex) && nextIndex >= 0 && nextIndex < total ? nextIndex : 0;
  return {
    index, total, nextIndex: (index + 1) % total,
    groups: new Set([order.bull[index], order.bear[index]].filter(Boolean)),
  };
}
