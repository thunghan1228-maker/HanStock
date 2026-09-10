export const SIGNAL_DETECTION_KEY = "hanstock-signal-detection-v1";
export const SIGNAL_DETECTION_LABELS = {
  blackDragon: "創高的黑龍", fiveMinuteTwelveShort: "12空（五分K）",
  fiveMinuteOnePlusTwoLong: "1+2多（五分K）", instantLarge: "族群瞬間大單",
  mainForce: "主力累計", fourGate: "四項精選", extraLargeSell: "盤中特大賣單",
  extraLargeBuy: "盤中特大買單", largeForce: "盤中大戶力", riverBull: "均線多（日線）",
  riverBear: "均線空（日線）", dailyStrategies: "日線 11 策略",
} as const;
export type SignalDetectionPreferences = Partial<Record<keyof typeof SIGNAL_DETECTION_LABELS, boolean>>;
export function readSignalDetectionPreferences(raw: string | null): SignalDetectionPreferences {
  try {
    const value = JSON.parse(raw ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.keys(SIGNAL_DETECTION_LABELS).flatMap((key) =>
      typeof value[key] === "boolean" ? [[key, value[key]]] : []));
  } catch { return {}; }
}
export function signalDetectionEnabled(preferences: SignalDetectionPreferences, mode: string) {
  return !Object.hasOwn(SIGNAL_DETECTION_LABELS, mode)
    || preferences[mode as keyof SignalDetectionPreferences] !== false;
}
