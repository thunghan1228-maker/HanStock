"use client";

import { useEffect, useState } from 'react';
import { liveSyncState } from '../../lib/live-sync-status';
import { createVisibilityGatedInterval } from '../../lib/useVisibilityGatedInterval';

export default function LiveSyncStatus({ polledAt = 0, sourceAt = 0, checkedAt = 0, error = false, latestSignalAt = 0, compact = false }: {
  polledAt?: number; sourceAt?: number; checkedAt?: number; error?: boolean; latestSignalAt?: number; compact?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const onVisible = () => { if (document.visibilityState === 'visible') setNow(Date.now()); }; const timer = createVisibilityGatedInterval(onVisible, 5_000); return () => { timer.cancel(); }; }, []);
  const state = liveSyncState({ now, polledAt, sourceAt, checkedAt, error });
  const time = (value: number) => value > 0 ? new Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(value) : '等待資料';
  return <div className={`live-sync-status${state.warning ? ' is-delayed' : ''}${compact ? ' is-compact' : ''}`} role="status">
    <strong>{state.label}</strong>
    <span>最後同步 {time(polledAt)}</span>
    <span>行情截至 {time(sourceAt)}</span>
    {latestSignalAt > 0 && <span>最新訊號成立 {time(latestSignalAt)}</span>}
  </div>;
}
