export function taipeiDateLabel(value: unknown) {
  const ts = Number(value);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(ts));
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}/${get("month")}/${get("day")}`;
}

export function latestTradingDateLabel(bars: Array<{ ts?: unknown }>, now = Date.now()) {
  const today = taipeiDateLabel(now);
  return bars
    .map((bar) => taipeiDateLabel(bar.ts))
    .filter((date) => date && date <= today)
    .sort()
    .at(-1) ?? "";
}
