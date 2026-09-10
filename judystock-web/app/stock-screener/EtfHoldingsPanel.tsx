"use client";

import { useEffect, useMemo, useState } from "react";
import StockTradingBadges from "../StockTradingBadges";

type View = "latest" | "five-days" | "consensus" | "weight" | "consecutive";
type EtfDelta = { code: string; netShares: number; netLots: number };
type Holding = { code: string; shares: number; weight: number; marketValue: number; currency: string };
type DailyDelta = { date: string; netShares: number };
type RadarRow = {
  ticker: string;
  name: string;
  latestNetShares: number;
  fiveDayNetShares: number;
  latestNetLots: number;
  fiveDayNetLots: number;
  latestEtfs: EtfDelta[];
  fiveDayEtfs: EtfDelta[];
  latestActionEtfCount: number;
  fiveDayActionEtfCount: number;
  holdingEtfCount: number;
  totalWeight: number;
  averageWeight: number;
  holdings: Holding[];
  consecutiveDirection: "buy" | "sell" | "flat";
  consecutiveDays: number;
  daily: DailyDelta[];
};
type RadarPayload = {
  ok: boolean;
  dataDate: string;
  holdingsDate: string | null;
  holdingsAvailable: boolean;
  holdingsError?: string | null;
  tradingDays: number;
  availableDates: string[];
  etfs: string[];
  etfCount: number;
  rowCount: number;
  rows: RadarRow[];
  source: string;
  disclaimer: string;
};
type TechnicalPayload = { rows?: Array<{ code?: string; close?: number }> };

const etfNames: Record<string, string> = {
  "00403A": "主動統一升級50",
  "00980A": "主動野村臺灣優選",
  "00981A": "主動統一台股增長",
  "00982A": "主動群益台灣強棒",
  "00983A": "主動中信ARK創新",
  "00984A": "主動安聯台灣高息",
  "00985A": "主動野村台灣50",
  "00991A": "主動復華未來50",
  "00992A": "主動群益科技創新",
};

const signed = (value: number, digits = 0) => `${value > 0 ? "+" : ""}${value.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const amount = (shares: number, close: number | undefined) => close ? shares * close / 1_000_000 : null;
const memberships = (row: RadarRow) => [...new Set([
  ...row.holdings.map((item) => item.code),
  ...row.latestEtfs.map((item) => item.code),
  ...row.fiveDayEtfs.map((item) => item.code),
])];

export default function EtfHoldingsPanel({ returnTo = "/stock-screener" }: { returnTo?: string }) {
  const [view, setView] = useState<View>("latest");
  const [selected, setSelected] = useState("全部");
  const [query, setQuery] = useState("");
  const [backtestDate, setBacktestDate] = useState("");
  const [payload, setPayload] = useState<RadarPayload | null>(null);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [focusedTicker, setFocusedTicker] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ days: "5" });
    if (backtestDate) params.set("date", backtestDate);
    Promise.all([
      fetch(`/api/active-etf-radar?${params}`, { cache: "no-store", signal: controller.signal })
        .then(async (response) => {
          if (!response.ok) throw new Error("主動式 ETF 正式資料尚未完成");
          return response.json() as Promise<RadarPayload>;
        }),
      fetch("/api/technical-market", { cache: "force-cache", signal: controller.signal })
        .then((response) => response.ok ? response.json() as Promise<TechnicalPayload> : { rows: [] })
        .catch(() => ({ rows: [] })),
    ]).then(([radar, technical]) => {
      if (controller.signal.aborted) return;
      setPayload(radar);
      setPrices(Object.fromEntries((technical.rows ?? []).flatMap((row) => {
        const code = String(row.code ?? "").trim().toUpperCase();
        return code && typeof row.close === "number" ? [[code, row.close]] : [];
      })));
      setFocusedTicker((current) => current || radar.rows[0]?.ticker || "");
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "主動式 ETF 資料讀取失敗");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [backtestDate, reloadKey]);

  const rows = payload?.rows ?? [];
  const etfs = payload?.etfs ?? [];
  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return rows.filter((row) => {
      const belongs = selected === "全部" || memberships(row).includes(selected);
      return belongs && (!keyword || row.ticker.toLowerCase().includes(keyword) || row.name.toLowerCase().includes(keyword));
    });
  }, [query, rows, selected]);
  const focused = filtered.find((row) => row.ticker === focusedTicker) ?? filtered[0] ?? null;
  const latest = view === "latest";
  const flowValue = (row: RadarRow) => latest ? row.latestNetShares : row.fiveDayNetShares;
  const flowEtfs = (row: RadarRow) => latest ? row.latestEtfs : row.fiveDayEtfs;
  const flowMetric = (row: RadarRow) => amount(flowValue(row), prices[row.ticker]) ?? flowValue(row) / 1000;
  const increase = [...filtered].filter((row) => flowValue(row) > 0).sort((a, b) => flowMetric(b) - flowMetric(a));
  const decrease = [...filtered].filter((row) => flowValue(row) < 0).sort((a, b) => flowMetric(a) - flowMetric(b));
  const consensus = [...filtered].filter((row) => row.holdingEtfCount > 0).sort((a, b) => b.holdingEtfCount - a.holdingEtfCount || b.totalWeight - a.totalWeight);
  const weighted = [...filtered].filter((row) => row.holdingEtfCount > 0).sort((a, b) => b.averageWeight - a.averageWeight);
  const consecutive = [...filtered].filter((row) => row.consecutiveDays >= 2).sort((a, b) => b.consecutiveDays - a.consecutiveDays || Math.abs(b.latestNetShares) - Math.abs(a.latestNetShares));
  const totalIncrease = filtered.reduce((sum, row) => sum + Math.max(0, amount(row.latestNetShares, prices[row.ticker]) ?? 0), 0);
  const totalDecrease = filtered.reduce((sum, row) => sum + Math.min(0, amount(row.latestNetShares, prices[row.ticker]) ?? 0), 0);

  const openKline = (row: RadarRow) => {
    const url = new URL("/kline", window.location.origin);
    url.searchParams.set("ticker", row.ticker);
    url.searchParams.set("name", row.name);
    url.searchParams.set("interval", "5m");
    url.searchParams.set("returnTo", returnTo);
    if (innerWidth <= 820 || matchMedia("(pointer: coarse)").matches) location.assign(url.toString());
    else window.open(url.toString(), "_blank", "noopener,noreferrer");
  };

  const flowTable = (items: RadarRow[], tone: "buy" | "sell") => (
    <div className="etf-flow-list">
      <div className="etf-flow-header"><span>排行</span><span>個股</span><span>增減張數</span><span>估算金額</span><span>ETF</span></div>
      {items.slice(0, 10).map((row, index) => {
        const estimated = amount(flowValue(row), prices[row.ticker]);
        return <div className="etf-flow-row" key={`${view}-${tone}-${row.ticker}`}>
          <span className="etf-order">{String(index + 1).padStart(2, "0")}</span>
          <button onClick={() => openKline(row)} title={`開啟 ${row.ticker} ${row.name} 五分鐘 K 線`} aria-label={`開啟 ${row.ticker} ${row.name} 五分鐘 K 線`}><b>{row.ticker}</b><small>{row.name}</small><StockTradingBadges ticker={row.ticker} compact /></button>
          <strong className={tone === "buy" ? "positive" : "negative"}>{signed(flowValue(row) / 1000, 1)} 張</strong>
          <strong className={tone === "buy" ? "positive" : "negative"}>{estimated === null ? "—" : `${signed(estimated, 1)} 百萬`}</strong>
          <span>{flowEtfs(row).length} 檔</span>
        </div>;
      })}
      {items.length === 0 && <div className="etf-empty">目前沒有符合條件的正式資料</div>}
    </div>
  );

  const wideRows = view === "consensus" ? consensus : view === "weight" ? weighted : consecutive;

  return <section className="etf-console screener-etf-panel" aria-label="主動式 ETF 持股雷達">
    <header className="etf-console-head">
      <div><span className="eyebrow">ACTIVE ETF RADAR</span><h2>主動式 ETF 持股雷達</h2><p>正式每日持股、增減、共同持有與權重排行；點股票可查看跨 ETF 明細。</p></div>
      <div className={`etf-data-state ${error ? "is-error" : ""}`}><i />{loading ? "正式資料讀取中" : error ? "正式資料尚未完成" : `資料日 ${payload?.dataDate ?? "—"} · ${payload?.etfCount ?? 0} 檔 ETF`}</div>
    </header>

    <div className="etf-toolbar">
      <label className="etf-search"><span>搜尋個股</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="輸入代號或名稱，例如 2330" /></label>
      <label className="etf-search etf-date-search"><span>回看資料日</span><input type="date" value={backtestDate} max={payload?.dataDate} onChange={(event) => setBacktestDate(event.target.value)} /></label>
      {backtestDate && <button className="etf-reset-date" onClick={() => setBacktestDate("")}>回到最新</button>}
      <div className="etf-filter"><span>ETF 篩選</span><div><button className={selected === "全部" ? "active" : ""} onClick={() => setSelected("全部")}>全部</button>{etfs.map((code) => <button key={code} className={selected === code ? "active" : ""} onClick={() => setSelected(code)} title={`${code} ${etfNames[code] ?? "主動式 ETF"}`}><strong>{code}</strong><small>{etfNames[code] ?? "主動式 ETF"}</small></button>)}</div></div>
    </div>

    {error ? <div className="etf-live-error"><b>{error}</b><span>系統不會以空資料或示範數字覆蓋正式快照。</span><button onClick={() => setReloadKey((value) => value + 1)}>重新讀取</button></div> : <>
      <div className="etf-summary-grid">
        <article><span>最新一日加碼</span><strong className="positive">+{totalIncrease.toFixed(1)}</strong><small>百萬元・估算</small></article>
        <article><span>最新一日減碼</span><strong className="negative">-{Math.abs(totalDecrease).toFixed(1)}</strong><small>百萬元・估算</small></article>
        <article><span>共同持有最多</span><strong>{consensus[0]?.holdingEtfCount ?? 0}</strong><small>檔 ETF</small></article>
        <article><span>正式持股股票</span><strong>{payload?.rowCount ?? 0}</strong><small>檔</small></article>
      </div>
      <div className="etf-view-tabs" role="tablist">
        {([['latest','最新一日'],['five-days','近 5 日累計'],['consensus','共同持有'],['weight','持股權重'],['consecutive','連續加減碼']] as [View,string][]).map(([key,label]) => <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)}>{label}</button>)}
      </div>
      {view === "latest" || view === "five-days" ? <div className="etf-flow-grid"><section className="etf-flow-card buy"><h3>▲ {view === "latest" ? "最新一日" : "近 5 日"}加碼排行</h3>{flowTable(increase, "buy")}</section><section className="etf-flow-card sell"><h3>▼ {view === "latest" ? "最新一日" : "近 5 日"}減碼排行</h3>{flowTable(decrease, "sell")}</section></div> : <div className="etf-wide-table" role="region" tabIndex={0}>
        <div className="etf-wide-header"><span>排行</span><span>股票</span><span>{view === "consecutive" ? "連續動作" : "共同持有"}</span><span>平均權重</span><span>近 5 日張數</span><span>持有 ETF</span></div>
        {wideRows.map((row, index) => <div className="etf-wide-row" key={`${view}-${row.ticker}`}><span className="etf-order">{String(index + 1).padStart(2, "0")}</span><button onClick={() => openKline(row)} title={`開啟 ${row.ticker} ${row.name} 五分鐘 K 線`} aria-label={`開啟 ${row.ticker} ${row.name} 五分鐘 K 線`}><b>{row.ticker}</b><small>{row.name}</small><StockTradingBadges ticker={row.ticker} compact /></button><strong>{view === "consecutive" ? `${row.consecutiveDirection === "buy" ? "加碼" : "減碼"} ${row.consecutiveDays} 日` : `${row.holdingEtfCount} 檔`}</strong><strong>{row.averageWeight.toFixed(2)}%</strong><strong className={row.fiveDayNetLots < 0 ? "negative" : "positive"}>{signed(row.fiveDayNetLots, 1)} 張</strong><span className="etf-code-list">{memberships(row).join(" · ")}</span></div>)}
        {wideRows.length === 0 && <div className="etf-empty">找不到符合條件的正式資料</div>}
      </div>}
      {focused && <section className="etf-stock-detail"><header><div><span>個股跨 ETF 明細</span><h3>{focused.ticker} {focused.name}</h3></div><button onClick={() => openKline(focused)}>開啟五分 K ↗</button></header><div className="etf-detail-grid"><article><span>目前共同持有</span><strong>{focused.holdingEtfCount} 檔</strong><small>合計權重 {focused.totalWeight.toFixed(2)}%</small></article><article><span>最新一日</span><strong className={focused.latestNetShares < 0 ? "negative" : "positive"}>{signed(focused.latestNetLots, 1)} 張</strong><small>{focused.latestEtfs.length} 檔有異動</small></article><article><span>近 5 日</span><strong className={focused.fiveDayNetShares < 0 ? "negative" : "positive"}>{signed(focused.fiveDayNetLots, 1)} 張</strong><small>{focused.fiveDayActionEtfCount} 檔有異動</small></article></div><div className="etf-detail-columns"><div><h4>目前持股與權重</h4>{focused.holdings.length ? focused.holdings.map((item) => <p key={item.code}><b>{item.code}</b><span>{item.shares.toLocaleString("zh-TW")} 股</span><strong>{item.weight.toFixed(2)}%</strong></p>) : <p><span>該資料日沒有正式持股紀錄</span></p>}</div><div><h4>每日合計異動</h4>{focused.daily.map((item) => <p key={item.date}><b>{item.date}</b><span className={item.netShares < 0 ? "negative" : "positive"}>{signed(item.netShares / 1000, 1)} 張</span></p>)}</div></div></section>}
      <footer className="etf-source-note">來源：FinMind 主動式 ETF 每日持股明細及持股異動。持股異動包含申購贖回影響，不等同經理人主動買賣純額；金額為最新收盤價估算。</footer>
    </>}
  </section>;
}
