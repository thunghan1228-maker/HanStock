export const BLACK_DRAGON_SCAN_START_MINUTE = 11 * 60;
export const BLACK_DRAGON_SELECTION_VERSION = "black-dragon-intraday-same-bar-verified-open-v4";

function taipeiMinute(timestamp: number) {
  const date = new Date(timestamp + 8 * 60 * 60_000);
  return date.getUTCHours() * 60 + date.getUTCMinutes();
}

// Backfill may continue after the close, but must not run before 11:00 Taipei.
export function isBlackDragonScanAllowed(timestamp = Date.now()) {
  return Number.isFinite(timestamp) && taipeiMinute(timestamp) >= BLACK_DRAGON_SCAN_START_MINUTE;
}

export function isBlackDragonSignalWindow(timestamp: number) {
  return isBlackDragonScanAllowed(timestamp) && taipeiMinute(timestamp) <= 13 * 60 + 30;
}
