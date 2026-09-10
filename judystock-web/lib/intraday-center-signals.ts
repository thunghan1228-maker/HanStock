type CenterSignal = { kind?: string; riverSignalType?: string; strategyKind?: string };

// Black dragon shares the legacy riverBear kind, but remains an active category.
export function isActiveIntradayCenterSignal(signal: CenterSignal) {
  if (["triangleNearBreakout", "triangleBreakoutPendingVolume", "triangleVolumeBreakout"].includes(signal.kind ?? "")) return false;
  if (signal.strategyKind === "blackDragon") return true;
  return signal.riverSignalType !== "river"
    && signal.riverSignalType !== "daily-strategy"
    && signal.kind !== "riverBull"
    && signal.kind !== "riverBear";
}
