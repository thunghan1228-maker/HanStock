/** Symmetric log scale keeps small flows visible without clipping large trades. */
export function createForceChartScale(values: readonly (number | null)[], top: number, height: number) {
  const absolute = values.filter((value): value is number =>
    typeof value === "number" && Number.isFinite(value) && value !== 0,
  ).map(Math.abs).sort((a, b) => a - b);
  const maximum = absolute.at(-1) ?? 1;
  const pivot = absolute[Math.floor((absolute.length - 1) / 2)] || 1;
  const zeroY = top + height / 2;
  const radius = Math.max(1, height / 2 - 4);
  const denominator = Math.asinh(maximum / pivot);
  return {
    zeroY,
    y(value: number) {
      if (!Number.isFinite(value) || value === 0) return zeroY;
      const scaled = Math.asinh(Math.abs(value) / pivot) / denominator * radius;
      return zeroY - Math.sign(value) * Math.min(radius, Math.max(2.5, scaled));
    },
  };
}
