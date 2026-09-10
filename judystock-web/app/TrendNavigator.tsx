"use client";

import { memo, useMemo, useRef } from "react";
import type { TrendPoint } from "../lib/watchlist-trend";
import { clampTrendWindow, panTrendWindow, resizeTrendWindow, type TrendWindow } from "../lib/trend-viewport";

export const TrendNavigator = memo(function TrendNavigator({ ticker, points, range, onChange }: {
  ticker: string; points: TrendPoint[]; range: TrendWindow; onChange: (range: TrendWindow) => void;
}) {
  const drag = useRef<{ pointerId: number; mode: "start" | "end" | "move"; x: number; width: number; range: TrendWindow } | null>(null);
  const length = points.length, count = range.end - range.start;
  const overview = useMemo(() => {
    const prices = points.map(point => point.close), low = Math.min(...prices), span = Math.max(.01, Math.max(...prices) - low);
    return points.map((point, index) => `${index ? "L" : "M"}${(index / Math.max(1, points.length - 1) * 1000).toFixed(1)},${(34 - (point.close - low) / span * 26).toFixed(1)}`).join(" ");
  }, [points]);
  const valueText = `${points[range.start]?.date ?? ""} 至 ${points[range.end - 1]?.date ?? ""}`;
  return <div className="trend-navigator" aria-label={`${ticker} 走勢時間滑桿`}>
    <div className="trend-navigator-track"
      onPointerDown={event => {
        if (!length || event.button !== 0) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        const handle = (event.target as Element).closest<HTMLElement>("[data-range-handle]")?.dataset.rangeHandle;
        const mode = handle === "start" || handle === "end" ? handle : "move";
        const next = handle ? range : clampTrendWindow({ start: (event.clientX - bounds.left) / bounds.width * length - count / 2, end: (event.clientX - bounds.left) / bounds.width * length + count / 2 }, length);
        drag.current = { pointerId: event.pointerId, mode, x: event.clientX, width: bounds.width, range: next };
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
        if (!handle) onChange(next);
      }} onPointerMove={event => {
        const active = drag.current;
        if (!active || active.pointerId !== event.pointerId) return;
        const offset = (event.clientX - active.x) / active.width * length;
        onChange(active.mode === "move" ? panTrendWindow(active.range, length, offset)
          : resizeTrendWindow(active.range, length, active.mode, active.range[active.mode] + offset));
      }} onPointerUp={event => {
        drag.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
      <svg viewBox="0 0 1000 42" preserveAspectRatio="none" aria-hidden="true"><path d={overview} fill="none" stroke="#788caa" strokeWidth="1.2" vectorEffect="non-scaling-stroke" /></svg>
      <div className="trend-navigator-window" data-range-handle="move" style={{ left: `${range.start / length * 100}%`, width: `${count / length * 100}%` }}
        role="slider" tabIndex={0} aria-label={`${ticker} 左右移動走勢`} aria-valuemin={0} aria-valuemax={Math.max(0, length - count)} aria-valuenow={range.start} aria-valuetext={valueText}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const shift = event.key === "Home" ? -length : event.key === "End" ? length : (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 5 : 1);
          onChange(panTrendWindow(range, length, shift));
        }} />
      {(["start", "end"] as const).map(side => <button type="button" key={side} className="trend-navigator-handle" data-range-handle={side}
        style={{ left: `${range[side] / length * 100}%` }} role="slider"
        aria-label={`${ticker} ${side === "start" ? "開始" : "結束"}時間`} aria-valuemin={side === "start" ? 0 : range.start + Math.min(2, length)}
        aria-valuemax={side === "start" ? range.end - Math.min(2, length) : length} aria-valuenow={range[side]}
        aria-valuetext={points[side === "start" ? range.start : range.end - 1]?.date}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const index = event.key === "Home" ? 0 : event.key === "End" ? length : range[side] + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 5 : 1);
          onChange(resizeTrendWindow(range, length, side, index));
        }}>Ⅱ</button>)}
    </div>
    <div className="trend-navigator-help">拖曳中間移動 · 拉動兩側縮放 · 滾輪縮放</div>
  </div>;
});
