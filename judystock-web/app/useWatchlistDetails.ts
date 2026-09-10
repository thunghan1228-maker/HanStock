"use client";

import { useEffect, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";

export function useWatchlistDetails(tickerKey: string) {
  const [metadata, setMetadata] = useState<Record<string, {name: string; group: string; groups?: string[]}>>({});
  useEffect(() => {
    if (!tickerKey) return;
    const controller = new AbortController();
    const tickers = tickerKey.split(",");
    const batches = Array.from({length: Math.ceil(tickers.length / 50)}, (_, index) => tickers.slice(index * 50, index * 50 + 50));
    void Promise.allSettled(batches.map(async batch => {
      const response = await fetch(`/api/stock-search?metadataOnly=1&tickers=${encodeURIComponent(batch.join(","))}`, {signal: controller.signal});
      if (!response.ok) return;
      const payload = await response.json();
      if (!controller.signal.aborted && Array.isArray(payload.stocks))
        setMetadata(current => ({...current, ...Object.fromEntries(payload.stocks.map((stock: {ticker: string}) => [stock.ticker, stock]))}));
    }));
    return () => controller.abort();
  }, [tickerKey]);
  return {metadata};
}

export function useIntradayForceValues(tickerKey: string, tradeDate: string) {
  const [forces, setForces] = useState<Record<string, {forcePct: number | null; tradeDate: string}>>({});
  useEffect(() => {
    if (!tickerKey || !tradeDate) return;
    const controller = new AbortController();
    let loading = false;
    const tickers = tickerKey.split(",");
    const batches = Array.from({length: Math.ceil(tickers.length / 50)}, (_, index) => tickers.slice(index * 50, index * 50 + 50));
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        await Promise.allSettled(batches.map(async batch => {
          const response = await fetch(`/api/intraday-large-force-values?tradeDate=${tradeDate}&tickers=${encodeURIComponent(batch.join(","))}`, {cache: "no-store", signal: controller.signal});
          if (!response.ok) return;
          const payload = await response.json();
          if (!controller.signal.aborted && Array.isArray(payload.rows))
            setForces(current => ({...current, ...Object.fromEntries(payload.rows.filter(Boolean).map((row: {ticker: string; forcePct: number | null; tradeDate: string}) => [row.ticker, row]))}));
        }));
      } finally { loading = false; }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 15_000);
    window.addEventListener("focus", load);
    return () => { controller.abort(); timer.cancel(); window.removeEventListener("focus", load); };
  }, [tickerKey, tradeDate]);
  return forces;
}
