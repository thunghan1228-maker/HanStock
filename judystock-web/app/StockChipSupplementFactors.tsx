"use client";

import { useEffect, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";

type SavedMainForce = {
  weekEndDate: string;
  brokerBranchScore: number;
};

type ActiveEtfFlow = {
  ok: boolean;
  dataDate: string;
  latestNetLots: number;
  fiveDayNetLots: number;
  etfCount: number;
  score: number;
  label: string;
};

function signed(value: number) { return `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function tone(value: number | null) { return value === null ? "pending" : value > 0 ? "positive" : value < 0 ? "negative" : "neutral"; }

export function BrokerBranchDetailFactor({ code }: { code: string }) {
  const [record, setRecord] = useState<SavedMainForce | null>(null);
  useEffect(() => {
    try {
      const cached = JSON.parse(window.localStorage.getItem(`hanstock-weekly-main-force-${code}`) ?? "[]") as SavedMainForce[];
      setRecord(Array.isArray(cached) ? cached.at(-1) ?? null : null);
    } catch { setRecord(null); }
    let controller: AbortController | null = null;
    const load = () => {
      controller?.abort();
      controller = new AbortController();
      fetch(`/api/weekly-main-force-history?ticker=${encodeURIComponent(code)}`, { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() as Promise<{ history?: SavedMainForce[] }> : null)
        .then((payload) => {
          if (!Array.isArray(payload?.history)) return;
          setRecord(payload.history.at(-1) ?? null);
          try { window.localStorage.setItem(`hanstock-weekly-main-force-${code}`, JSON.stringify(payload.history)); } catch { /* storage may be unavailable */ }
        })
        .catch(() => undefined);
    };
    load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 30_000);
    return () => { controller?.abort(); timer.cancel(); };
  }, [code]);
  const score = record?.brokerBranchScore ?? null;
  return <span className={tone(score)}><b>主力</b><strong>{score === null ? "讀取中" : signed(score)}</strong><small>{record ? `${record.weekEndDate} 券商分點週分` : "正在讀取券商分點集中度"}</small></span>;
}

export function ActiveEtfDetailFactor({ code }: { code: string }) {
  const [flow, setFlow] = useState<ActiveEtfFlow | null>(null);
  const [pending, setPending] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setPending(true);
    fetch(`/api/active-etf-flow?ticker=${encodeURIComponent(code)}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<ActiveEtfFlow> : null)
      .then((payload) => setFlow(payload?.ok ? payload : null))
      .catch(() => undefined)
      .finally(() => { if (!controller.signal.aborted) setPending(false); });
    return () => controller.abort();
  }, [code]);
  const score = flow?.score ?? null;
  return <span className={tone(score)}><b>ETF 持股</b><strong>{score === null ? (pending ? "讀取中" : "待最新資料") : signed(score)}</strong><small>{flow ? `${flow.dataDate}｜當日 ${signed(flow.latestNetLots)} 張｜五日 ${signed(flow.fiveDayNetLots)} 張｜${flow.etfCount} 檔｜FinMind` : "主動式 ETF 每日持股異動"}</small></span>;
}
