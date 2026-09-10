"use client";

import { useEffect, useRef, useState } from "react";

type Bar = { ts: number; open: number; high: number; low: number; close: number; volume: number };
type CandlesResponse = { ok: boolean; bars?: Bar[]; error?: string };

declare global {
  interface Window {
    LightweightCharts?: typeof import("lightweight-charts");
  }
}

const SCRIPT_SRC = "https://cdn.jsdelivr.net/npm/lightweight-charts@4/dist/lightweight-charts.standalone.production.js";
let scriptLoadPromise: Promise<void> | null = null;

function loadLightweightCharts() {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.LightweightCharts) return Promise.resolve();
  if (scriptLoadPromise) return scriptLoadPromise;
  scriptLoadPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${SCRIPT_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("lightweight-charts load failed")));
      return;
    }
    const script = document.createElement("script");
    script.src = SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("lightweight-charts load failed"));
    document.head.appendChild(script);
  });
  return scriptLoadPromise;
}

// Lightweight Charts renders numeric time as UTC; bar.ts is a UTC epoch ms
// value for Taipei wall-clock minutes, so shift by +8h to display correctly.
function chartTime(tsMs: number) {
  return Math.floor(tsMs / 1000) + 8 * 3600;
}

function average(values: number[], index: number, period: number) {
  if (index + 1 < period) return null;
  let sum = 0;
  for (let i = index - period + 1; i <= index; i++) sum += values[i];
  return sum / period;
}

export default function KlineCandleChart({ ticker, interval, name }: { ticker: string; interval: "1m" | "5m" | "1d"; name: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<{
    candle: import("lightweight-charts").ISeriesApi<"Candlestick">;
    volume: import("lightweight-charts").ISeriesApi<"Histogram">;
    ma5: import("lightweight-charts").ISeriesApi<"Line">;
    ma20: import("lightweight-charts").ISeriesApi<"Line">;
  } | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    let resizeObserver: ResizeObserver | null = null;

    void loadLightweightCharts().then(() => {
      if (!active || !containerRef.current || !window.LightweightCharts) return;
      const chart = window.LightweightCharts.createChart(containerRef.current, {
        layout: { background: { color: "#0b0c0f" }, textColor: "#c8d0da" },
        grid: { vertLines: { color: "#20262e" }, horzLines: { color: "#20262e" } },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#2c323a" },
        rightPriceScale: { borderColor: "#2c323a" },
        crosshair: { mode: 0 },
        autoSize: true,
      });
      const candle = chart.addCandlestickSeries({
        upColor: "#ff5b5f", downColor: "#22c55e", borderVisible: false,
        wickUpColor: "#ff5b5f", wickDownColor: "#22c55e",
        priceScaleId: "right",
      });
      candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.28 } });
      const volume = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
      const ma5 = chart.addLineSeries({ color: "#ffd45d", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      const ma20 = chart.addLineSeries({ color: "#55a8ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      chartRef.current = chart;
      seriesRef.current = { candle, volume, ma5, ma20 };

      resizeObserver = new ResizeObserver(() => chart.applyOptions({}));
      resizeObserver.observe(containerRef.current);
    }).catch(() => {
      if (active) { setStatus("error"); setMessage("圖表元件載入失敗，請重新整理再試一次"); }
    });

    return () => {
      active = false;
      resizeObserver?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await fetch(`/api/kline-candles?ticker=${encodeURIComponent(ticker)}&interval=${interval}`, { cache: "no-store", signal: AbortSignal.timeout(20_000) });
        const payload = await response.json() as CandlesResponse;
        if (!active) return;
        if (!payload.ok || !Array.isArray(payload.bars) || payload.bars.length === 0) {
          setStatus(payload.ok ? "empty" : "error");
          setMessage(payload.ok ? "目前還沒有可顯示的K棒資料" : "K棒資料暫時無法取得，稍後會自動重試");
          return;
        }
        const bars = payload.bars;
        const closes = bars.map((bar) => bar.close);
        const series = seriesRef.current;
        if (series) {
          series.candle.setData(bars.map((bar) => ({ time: chartTime(bar.ts) as import("lightweight-charts").UTCTimestamp, open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
          series.volume.setData(bars.map((bar) => ({ time: chartTime(bar.ts) as import("lightweight-charts").UTCTimestamp, value: bar.volume, color: bar.close >= bar.open ? "rgba(255,91,95,.5)" : "rgba(34,197,94,.5)" })));
          series.ma5.setData(bars.flatMap((bar, index) => {
            const value = average(closes, index, 5);
            return value === null ? [] : [{ time: chartTime(bar.ts) as import("lightweight-charts").UTCTimestamp, value }];
          }));
          series.ma20.setData(bars.flatMap((bar, index) => {
            const value = average(closes, index, 20);
            return value === null ? [] : [{ time: chartTime(bar.ts) as import("lightweight-charts").UTCTimestamp, value }];
          }));
          chartRef.current?.timeScale().fitContent();
        }
        setStatus("ready");
        setMessage("");
      } catch {
        if (active) { setStatus("error"); setMessage("K棒資料暫時無法取得，稍後會自動重試"); }
      } finally {
        inFlight = false;
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [ticker, interval]);

  return (
    <div className="self-kline-chart" style={{ position: "relative", width: "100%", height: "100%", minHeight: 360 }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      {status !== "ready" && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#929aa6", fontSize: 14, pointerEvents: "none" }}>
          {status === "loading" ? `載入 ${ticker} ${name} K 棒資料中…` : message}
        </div>
      )}
    </div>
  );
}
