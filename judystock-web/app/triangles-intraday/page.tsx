"use client";

import Link from "next/link";

import { useCallback, useEffect, useMemo, useState } from "react";
import StockTradingBadges from "../StockTradingBadges";
import { createVisibilityGatedInterval } from "../../lib/useVisibilityGatedInterval";

type TriangleStatus = "放量突破" | "突破待量" | "接近突破";
type TriangleRow = {
  stock_code: string;
  stock_name: string;
  market?: string;
  status: TriangleStatus;
  score: number;
  close: number;
  distance_to_upper_pct: number;
  volume_ratio_20d: number;
};
type Payload = {
  ok: boolean;
  tradeDate?: string;
  generatedAt?: string;
  summary?: { candidate_count?: number; quote_count?: number; matched_count?: number; unavailable_count?: number };
  rows?: TriangleRow[];
  message?: string;
};

function formatTaipei(value?: string) {
  if (!value) return "等待第一輪掃描";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function openKline(ticker: string, name: string) {
  const url = new URL("/kline", window.location.origin);
  url.searchParams.set("ticker", ticker);
  url.searchParams.set("name", name);
  url.searchParams.set("interval", "5m");
  url.searchParams.set("returnTo", "/triangles-intraday");
  const mobile = window.matchMedia?.("(pointer: coarse)").matches || window.innerWidth <= 820;
  if (mobile) window.location.assign(url.toString());
  else window.open(url.toString(), "_blank", "popup=yes,width=1180,height=780,resizable=yes,scrollbars=no");
}

export default function IntradayTrianglePage() {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [status, setStatus] = useState<"全部" | TriangleStatus>("全部");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/triangles-intraday", { cache: "no-store" });
      const data = await response.json() as Payload;
      if (!response.ok || !data.ok) throw new Error(data.message || "盤中三角收斂名單暫時無法取得");
      setPayload(data);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "盤中三角收斂名單暫時無法取得");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    const timer = createVisibilityGatedInterval(onVisible, 30_000);
    return () => { timer.cancel(); };
  }, [load]);

  const rows = payload?.rows ?? [];
  const visibleRows = useMemo(() => status === "全部" ? rows : rows.filter((row) => row.status === status), [rows, status]);
  const statuses: Array<"全部" | TriangleStatus> = ["全部", "放量突破", "突破待量", "接近突破"];
  const count = (value: "全部" | TriangleStatus) => value === "全部" ? rows.length : rows.filter((row) => row.status === value).length;

  return <main className="triangle-live-page">
    <header className="triangle-live-topbar">
      <button type="button" onClick={() => window.history.length > 1 ? window.history.back() : window.location.assign("/")} aria-label="回上一頁">‹</button>
      <div><span>HANSTOCK · INTRADAY DAILY TRIANGLE</span><h1>盤中日線三角收斂</h1><p>以前一日完整日 K 加上今日即時暫定日 K，每 5 分鐘重新判斷。</p></div>
      <Link href="/">回戰鬥版首頁</Link>
    </header>

    <section className="triangle-live-summary">
      <div><span>符合名單</span><strong>{payload?.summary?.matched_count ?? rows.length}</strong><small>接近突破／突破待量／放量突破</small></div>
      <div><span>即時行情覆蓋</span><strong>{payload?.summary?.quote_count ?? "—"}</strong><small>候選 {payload?.summary?.candidate_count ?? "—"} 檔</small></div>
      <div><span>盤中掃描時間</span><strong>{formatTaipei(payload?.generatedAt)}</strong><small>{payload?.tradeDate?.replaceAll("-", "/") ?? "今日"} · 30 秒自動讀取最新結果</small></div>
    </section>

    <nav className="triangle-live-filters" aria-label="盤中三角收斂狀態">
      {statuses.map((item) => <button type="button" className={status === item ? "active" : ""} onClick={() => setStatus(item)} key={item}>{item}<b>{count(item)}</b></button>)}
      <button type="button" className="refresh" onClick={() => { setLoading(true); void load(); }}>↻ 立即更新</button>
    </nav>

    <section className="triangle-live-board" aria-live="polite">
      <div className="triangle-live-row head"><span>狀態</span><span>代號／名稱</span><span>分數</span><span>即時價</span><span>距上緣</span><span>20 日量比</span><span>操作</span></div>
      {loading && !payload && <div className="triangle-live-empty">正在讀取盤中三角收斂名單…</div>}
      {!loading && message && <div className="triangle-live-empty error">{message}</div>}
      {visibleRows.map((row) => <button type="button" className={`triangle-live-row status-${row.status}`} key={row.stock_code} onClick={() => openKline(row.stock_code, row.stock_name)}>
        <span><i />{row.status}</span><span><b>{row.stock_code}</b><strong>{row.stock_name}</strong><small>{row.market || "台股"}</small><StockTradingBadges ticker={row.stock_code} compact /></span><strong>{row.score.toFixed(1)}</strong><span>{row.close.toLocaleString("zh-TW")}</span><span className={row.distance_to_upper_pct <= 0 ? "breakout" : ""}>{row.distance_to_upper_pct > 0 ? "+" : ""}{row.distance_to_upper_pct.toFixed(2)}%</span><span className={row.volume_ratio_20d >= 1.5 ? "volume" : ""}>{row.volume_ratio_20d.toFixed(2)}×</span><em>查看 5 分 K ›</em>
      </button>)}
      {!loading && !message && visibleRows.length === 0 && <div className="triangle-live-empty">這個狀態目前沒有股票；下一輪將自動更新。</div>}
    </section>
    <footer className="triangle-live-note">盤中資料為暫定日 K，會隨即時價格與成交量變動；收盤後仍以官方完整日 K 的盤後名單為正式結果。</footer>
  </main>;
}
