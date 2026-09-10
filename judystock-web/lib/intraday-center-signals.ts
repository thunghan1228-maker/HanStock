type CenterSignal = { kind?: string; riverSignalType?: string; strategyKind?: string };

// 已淘汰的訊號：不再進入盤中即時訊號中心、提醒佇列或任何即時訊號分類。
export function isActiveIntradayCenterSignal(signal: CenterSignal) {
  if ([
    "triangleNearBreakout",
    "triangleBreakoutPendingVolume",
    "triangleVolumeBreakout",
    "fiveMinuteTwelveShort",
    "fiveMinuteOnePlusTwoLong",
  ].includes(signal.kind ?? "")) return false;
  if (signal.strategyKind === "blackDragon") return false;
  return signal.riverSignalType !== "river"
    && signal.riverSignalType !== "daily-strategy"
    && signal.kind !== "riverBull"
    && signal.kind !== "riverBear";
}
