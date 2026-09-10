"use client";
import { useEffect, useState } from "react";
import { rankTdccWeeklyChanges, type TdccWeeklyRow } from "../lib/tdcc-weekly-score";

type Payload = { ok?: boolean; dataDate?: string; previousDate?: string | null; rows?: TdccWeeklyRow[] };
export function TdccLargeHolderCard({ code }: { code: string }) {
  const [payload, setPayload] = useState<Payload | null>(null);
  useEffect(() => { const c = new AbortController(); fetch('/api/tdcc-radar', { cache: 'no-store', signal: c.signal }).then(r => r.ok ? r.json() : null).then(setPayload).catch(() => setPayload(null)); return () => c.abort(); }, []);
  const row = payload?.rows?.find((item) => item.code === code);
  const score = row ? rankTdccWeeklyChanges(payload?.rows ?? []).get(code) ?? null : null;
  return <span className={row ? "" : "pending"}><b>集保大戶週分數</b><strong>{score === null ? "待資料" : `${score > 0 ? '+' : ''}${score.toFixed(1)}`}</strong><small>{row ? `持股 ${row.largeHolderPct.toFixed(2)}%｜週變化 ${row.weeklyChangePp === null ? '—' : `${row.weeklyChangePp > 0 ? '+' : ''}${row.weeklyChangePp.toFixed(2)} 個百分點`}` : '等候兩期集保資料'}</small></span>;
}
