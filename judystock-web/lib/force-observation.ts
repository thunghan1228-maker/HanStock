type StoredForceObservation = {
  netVolume?: number;
  buyAmount?: number;
  sellAmount?: number;
  mainTickCount?: number;
};

// Older caches persisted unavailable bars as all-zero records. A real balanced
// bar still has a tick count or buy/sell amounts, even when its net value is zero.
export function hasStoredForceObservation(record: StoredForceObservation | undefined) {
  return record !== undefined && [record.netVolume, record.buyAmount, record.sellAmount, record.mainTickCount]
    .some(value => typeof value === "number" && Number.isFinite(value) && value !== 0);
}
