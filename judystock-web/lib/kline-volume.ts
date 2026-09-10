export function hubLotsToShares(value: unknown) {
  const lots = Number(value);
  return Number.isFinite(lots) && lots > 0 ? lots * 1_000 : 0;
}

export function sumMinuteVolumesToShares(values: unknown[], unit: "lots" | "shares") {
  const total = values.reduce<number>((sum, value) => {
    const volume = Number(value);
    return Number.isFinite(volume) && volume > 0 ? sum + volume : sum;
  }, 0);
  return unit === "lots" ? total * 1_000 : total;
}

export function preferCompleteCumulativeVolume(...values: unknown[]) {
  return values.reduce<number>((largest, value) => {
    const volume = Number(value);
    return Number.isFinite(volume) && volume > largest ? volume : largest;
  }, 0);
}

export function hasSparseMinuteVolume(values: unknown[], minimumCoverage = 0.6) {
  if (values.length < 3) return false;
  const positive = values.filter((value) => Number.isFinite(Number(value)) && Number(value) > 0).length;
  return positive / values.length < minimumCoverage;
}
