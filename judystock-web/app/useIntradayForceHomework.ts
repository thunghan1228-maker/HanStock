"use client";

import { useEffect, useMemo, useState } from "react";
import { rankIntradayForceHomework, type IntradayForceHomeworkRow } from "../lib/intraday-force-homework";

type Payload = {
  ok?: boolean;
  tradeDate?: string;
  updatedAt?: number;
  rows?: IntradayForceHomeworkRow[];
  progress?: { status?: string; processed?: number; available?: number; total?: number; cycle?: number } | null;
};

export function useIntradayForceHomework(requestedTradeDate?: string) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const tradeDate = /^\d{4}-\d{2}-\d{2}$/.test(requestedTradeDate ?? "") ? requestedTradeDate! : "";

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const load = async () => {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      try {
        // 收集器由盤中訊號中心維持單一排程；這裡只讀永久排名快照。
        // 同一頁的兩個 Hook 不再各自搶掃描鎖，避免有效資料長時間停在 0。
        const params = new URLSearchParams({ scope: "market" });
        if (tradeDate) params.set("tradeDate", tradeDate);
        const response = await fetch(`/api/intraday-large-force-values?${params}`, {
          cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" },
        });
        const next = await response.json() as Payload;
        if (!response.ok || !next.ok || !Array.isArray(next.rows)) throw new Error("homework-unavailable");
        if (!controller.signal.aborted) {
          const rows = next.rows ?? [];
          const updatedAt = Number(next.updatedAt) || 0;
          const freshEnough = updatedAt > 0 && Date.now() - updatedAt <= 2 * 60_000;
          // 單一輪詢短暫只回部分股票時，保留同交易日、五分鐘內的上一輪
          // 有效列並以新列覆蓋；避免畫面從 20 檔閃成 12 檔或 0 檔。
          setPayload((current) => {
            if (current?.tradeDate !== next.tradeDate || !Array.isArray(current?.rows)) return next;
            const recentPrevious = current.rows.filter((row) => Number(row.barTs) >= Date.now() - 5 * 60_000);
            const merged = new Map(recentPrevious.map((row) => [row.ticker, row]));
            rows.forEach((row) => merged.set(row.ticker, row));
            return { ...next, rows: [...merged.values()] };
          });
          setError(rows.length >= 40 && freshEnough ? "" : "盤中大戶力資料仍在更新，尚未完成前20多與前20空");
          setLoading(false);
        }
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === "AbortError") && !controller.signal.aborted) {
          setError("盤中大戶力排名連線中斷，稍後自動重試");
          setLoading(false);
        }
      } finally { inFlight = false; }
    };
    let timer: number | null = null;
    const start = () => {
      if (timer !== null) return;
      void load();
      timer = window.setInterval(() => void load(), 10_000);
    };
    const stop = () => {
      if (timer === null) return;
      window.clearInterval(timer);
      timer = null;
    };
    const syncVisibility = () => {
      if (document.visibilityState === "visible") start();
      else stop();
    };
    syncVisibility();
    window.addEventListener("focus", syncVisibility);
    document.addEventListener("visibilitychange", syncVisibility);
    return () => { controller.abort(); stop(); window.removeEventListener("focus", syncVisibility); document.removeEventListener("visibilitychange", syncVisibility); };
  }, [tradeDate]);

  const ranked = useMemo(() => rankIntradayForceHomework(payload?.rows, 20), [payload?.rows]);
  return { ...ranked, tradeDate: payload?.tradeDate ?? tradeDate, updatedAt: payload?.updatedAt ?? 0, progress: payload?.progress ?? null, loading, error };
}
