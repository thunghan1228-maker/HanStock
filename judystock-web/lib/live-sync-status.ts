export function liveSyncState(input: { now: number; polledAt: number; sourceAt: number; checkedAt?: number; error?: boolean }) {
  const shifted = new Date(input.now + 8 * 60 * 60_000);
  const minute = shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
  const live = shifted.getUTCDay() >= 1 && shifted.getUTCDay() <= 5 && minute >= 540 && minute <= 815;
  if (input.error || (input.polledAt > 0 && input.now - input.polledAt > 30_000)) return { warning: true, label: '同步延遲，正在重試' };
  if (!input.polledAt) return { warning: true, label: '正在同步' };
  if (live && !input.sourceAt && !input.checkedAt) return { warning: true, label: '正在確認行情來源' };
  if (live && (!input.sourceAt || input.now - input.sourceAt > 3 * 60_000)) return { warning: true, label: '行情來源延遲，持續重試' };
  return { warning: false, label: live ? '持續同步' : '盤後紀錄' };
}
