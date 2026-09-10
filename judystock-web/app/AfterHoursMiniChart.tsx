"use client";

import { memo, useEffect, useRef, useState } from "react";

export const AfterHoursMiniChart = memo(function AfterHoursMiniChart({ ticker, name, onOpen }: {
  ticker: string;
  name: string;
  onOpen: (ticker: string, name: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setLoaded(true);
        observer.disconnect();
      }
    }, { rootMargin: "120px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return <div className="watchlist-mini-chart" ref={container}>
    <header><strong>五分 K · 最近兩個交易日 · 盤中訊號</strong><button type="button" onClick={() => onOpen(ticker, name)}>完整 K 線 ↗</button></header>
    {loaded ? <iframe title={`${ticker} ${name} 最近兩個交易日五分 K 與盤中訊號`}
      src={`/api/kline-embed/${encodeURIComponent(ticker)}?interval=5m&name=${encodeURIComponent(name)}&view=watchlist&uiRev=20260905-kline-signal-toggle-v35`}
      loading="lazy" /> : <div className="watchlist-mini-placeholder">捲動至此處載入五分 K 線</div>}
  </div>;
});
