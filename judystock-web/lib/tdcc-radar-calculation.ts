export function calculateTdccWeeklyChangePp(currentPct: number, previousPct: number) {
  return Math.round((currentPct - previousPct) * 100) / 100;
}
