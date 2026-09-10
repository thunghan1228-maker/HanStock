"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { WatchlistStock } from "../lib/watchlists";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import StockTradingBadges from "./StockTradingBadges";
import {
  INTRADAY_TRACKING_CHANGED_EVENT,
  INTRADAY_TRACKING_LOCAL_CHANGED_EVENT,
  INTRADAY_TRACKING_STORAGE_KEY,
  INTRADAY_TRACKING_SYNC_STATUS_EVENT,
  defaultIntradayTrackingSettings,
  normalizeIntradayTrackingSettings,
  type IntradayTrackingSettings,
  type IntradayTrackingSyncStatus,
  type TrackingKind,
} from "../lib/intraday-tracking";

type Signal = { tradeDate: string; ticker: string; name: string; kind: string; label: string; barTs: number; price: number; note: string };
type Payload = { ok?: boolean; tradeDate?: string; carryingPreviousSession?: boolean; signals?: Signal[]; fourGateSignals?: Signal[]; mainForceSignals?: Signal[]; instantLargeSignals?: Signal[]; extraLargeSellSignals?: Signal[]; extraLargeBuySignals?: Signal[]; largeForceSignals?: Signal[] };

const OPTIONS: Array<{ kind: TrackingKind; title: string; detail: string }> = [
  { kind: "mainForce", title: "主力累計翻多／翻空", detail: "含強勢翻多、強勢翻空" },
  { kind: "fourGate", title: "四項精選", detail: "四項同時通過" },
  { kind: "instantLarge", title: "族群瞬間大單", detail: "漲跌幅前 20 族群逐筆大單" },
  { kind: "extraLargeSell", title: "盤中特大賣單", detail: "達前日大單淨額" },
  { kind: "extraLargeBuy", title: "盤中特大買單", detail: "鏡像回補前日淨賣超" },
  { kind: "largeForce", title: "🐋 盤中大戶力", detail: "大戶力達 ±12% 並連續確認" },
  { kind: "immediateBuy200", title: "盤中大單買進 200%", detail: "達前日預估隔日賣壓 200%" },
  { kind: "immediateSell200", title: "盤中大單賣出 200%", detail: "達前日預估隔日賣壓 200%" },
];

function normalizeStocks(value: unknown): WatchlistStock[] {
  return normalizeIntradayTrackingSettings({ stocks: value, selected: [], remindersEnabled: false }).stocks;
}

function isChosen(kind: TrackingKind, signal: Signal) {
  if (kind === "mainForce") return signal.kind.startsWith("mainForce");
  if (kind === "fourGate") return signal.kind === "fourGateBullish" || signal.kind === "fourGateBearish";
  if (kind === "instantLarge") return signal.kind === "instantLargeBuy" || signal.kind === "instantLargeSell";
  if (kind === "extraLargeSell") return signal.kind === "intradayExtraLargeSell";
  if (kind === "extraLargeBuy") return signal.kind === "intradayExtraLargeBuy";
  if (kind === "largeForce") return signal.kind === "intradayLargeForceBuy" || signal.kind === "intradayLargeForceSell";
  if (kind === "immediateBuy200") return signal.kind === "daytradeEarlyBuy50";
  return signal.kind === "daytradeEarlySell50";
}

function labelFor(signal: Signal) {
  if (signal.kind === "instantLargeBuy") return "族群瞬間特大買單敲進";
  if (signal.kind === "instantLargeSell") return "族群瞬間大單連續倒出";
  if (signal.kind === "daytradeEarlyBuy50") return "盤中大單買進達前日預估隔日賣壓 200%";
  if (signal.kind === "daytradeEarlySell50") return "盤中大單賣出達前日預估隔日賣壓 200%";
  return signal.label;
}

function formatTime(timestamp: number) {
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(timestamp));
}

function readTrackerSettings(): IntradayTrackingSettings {
  if (typeof window === "undefined") return defaultIntradayTrackingSettings();
  try {
    return normalizeIntradayTrackingSettings(JSON.parse(window.localStorage.getItem(INTRADAY_TRACKING_STORAGE_KEY) ?? "{}"));
  } catch { return defaultIntradayTrackingSettings(); }
}

function uniqueSignals(payload: Payload) {
  const unique = new Map<string, Signal>();
  for (const signal of [payload.signals, payload.fourGateSignals, payload.mainForceSignals, payload.instantLargeSignals, payload.extraLargeSellSignals, payload.extraLargeBuySignals, payload.largeForceSignals].flatMap((rows) => Array.isArray(rows) ? rows : [])) {
    unique.set(`${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`, signal);
  }
  return [...unique.values()].sort((a, b) => b.barTs - a.barTs);
}

export function IntradayTrackingNotifier({ onOpenTracker, onOpenStock }: { onOpenTracker: () => void; onOpenStock: (ticker: string, name?: string) => void }) {
  const [settings, setSettings] = useState<IntradayTrackingSettings>(() => readTrackerSettings());
  const [alerts, setAlerts] = useState<Signal[]>([]);
  const initialized = useState(() => new Set<string>())[0];

  useEffect(() => {
    const refreshSettings = () => setSettings(readTrackerSettings());
    window.addEventListener("storage", refreshSettings);
    window.addEventListener(INTRADAY_TRACKING_CHANGED_EVENT, refreshSettings);
    return () => { window.removeEventListener("storage", refreshSettings); window.removeEventListener(INTRADAY_TRACKING_CHANGED_EVENT, refreshSettings); };
  }, []);

  useEffect(() => {
    let stopped = false;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch("/api/daytrade-early-sell?limit=5000&fast=1", { cache: "no-store" });
        const payload = await response.json() as Payload;
        if (stopped || !payload.ok) return;
        const tracked = new Set(settings.stocks.map((stock) => stock.ticker));
        const current = uniqueSignals(payload).filter((signal) => tracked.has(signal.ticker) && settings.selected.some((kind) => isChosen(kind, signal)));
        const fresh = current.filter((signal) => !initialized.has(`${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`));
        current.forEach((signal) => initialized.add(`${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`));
        if (settings.remindersEnabled && fresh.length) setAlerts(fresh.slice(0, 5));
      } catch { /* wait for the next five-second refresh */ } finally { busy = false; }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 5_000);
    return () => { stopped = true; timer.cancel(); };
  }, [initialized, settings]);

  if (!settings.remindersEnabled || alerts.length === 0) return null;
  const openTracker = () => {
    setAlerts([]);
    onOpenTracker();
  };

  return <aside className="tracking-alert-toast" role="alert" aria-live="assertive"><header><span>🔔 個股追蹤提醒</span><button type="button" onClick={() => setAlerts([])} aria-label="關閉個股追蹤提醒">×</button></header>{alerts.map((signal) => <button type="button" key={`${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`} onClick={() => onOpenStock(signal.ticker, signal.name)}><time>{formatTime(signal.barTs)}</time><span><b>{signal.ticker} {signal.name}</b><StockTradingBadges ticker={signal.ticker} compact /><strong>{labelFor(signal)}</strong></span></button>)}<footer><button type="button" onClick={openTracker}>查看追蹤清單</button></footer></aside>;
}

export function IntradayStockTrackingPanel({ onOpenStock }: { onOpenStock: (ticker: string, name?: string) => void }) {
  const [savedTracker] = useState(() => readTrackerSettings());
  const [stocks, setStocks] = useState<WatchlistStock[]>(savedTracker.stocks);
  const [selected, setSelected] = useState<TrackingKind[]>(savedTracker.selected);
  const [remindersEnabled, setRemindersEnabled] = useState(savedTracker.remindersEnabled);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<WatchlistStock[]>([]);
  const [batch, setBatch] = useState("");
  const [signals, setSignals] = useState<Signal[]>([]);
  const [tradeDate, setTradeDate] = useState("—");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [syncStatus, setSyncStatus] = useState<IntradayTrackingSyncStatus>(() => {
    if (typeof window === "undefined") return { state: "connecting", label: "正在連接全裝置同步", detail: "追蹤清單與設定會同步到所有裝置。" };
    return (window as Window & { __HANSTOCK_INTRADAY_TRACKING_SYNC_STATUS__?: IntradayTrackingSyncStatus })
      .__HANSTOCK_INTRADAY_TRACKING_SYNC_STATUS__
      ?? { state: "connecting", label: "正在連接全裝置同步", detail: "追蹤清單與設定會同步到所有裝置。" };
  });
  const mounted = useRef(false);
  const applyingCloudSignature = useRef<string | null>(null);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const settings = normalizeIntradayTrackingSettings({ stocks, selected, remindersEnabled });
    const signature = JSON.stringify(settings);
    if (applyingCloudSignature.current) return;
    try {
      window.localStorage.setItem(INTRADAY_TRACKING_STORAGE_KEY, signature);
      window.dispatchEvent(new CustomEvent(INTRADAY_TRACKING_LOCAL_CHANGED_EVENT, { detail: { settings } }));
      window.dispatchEvent(new CustomEvent(INTRADAY_TRACKING_CHANGED_EVENT, { detail: { source: "local", settings } }));
    } catch { /* current session remains available */ }
  }, [stocks, selected, remindersEnabled]);

  useEffect(() => {
    const applyCloud = (event: Event) => {
      const detail = (event as CustomEvent<{ source?: string; settings?: IntradayTrackingSettings }>).detail;
      if (detail?.source !== "cloud" || !detail.settings) return;
      const next = normalizeIntradayTrackingSettings(detail.settings);
      const cloudSignature = JSON.stringify(next);
      applyingCloudSignature.current = cloudSignature;
      setStocks(next.stocks);
      setSelected(next.selected);
      setRemindersEnabled(next.remindersEnabled);
      window.setTimeout(() => {
        if (applyingCloudSignature.current === cloudSignature) applyingCloudSignature.current = null;
      }, 0);
    };
    const updateStatus = (event: Event) => {
      const next = (event as CustomEvent<IntradayTrackingSyncStatus>).detail;
      if (next) setSyncStatus(next);
    };
    window.addEventListener(INTRADAY_TRACKING_CHANGED_EVENT, applyCloud);
    window.addEventListener(INTRADAY_TRACKING_SYNC_STATUS_EVENT, updateStatus);
    return () => {
      window.removeEventListener(INTRADAY_TRACKING_CHANGED_EVENT, applyCloud);
      window.removeEventListener(INTRADAY_TRACKING_SYNC_STATUS_EVENT, updateStatus);
    };
  }, []);

  useEffect(() => {
    const keyword = query.trim();
    if (!keyword) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetch(`/api/stock-search?q=${encodeURIComponent(keyword)}`, { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("search")))
        .then((payload: { stocks?: WatchlistStock[] }) => setResults(normalizeStocks(payload.stocks)))
        .catch(() => { if (!controller.signal.aborted) setResults([]); });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query]);

  useEffect(() => {
    let stopped = false;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const response = await fetch("/api/daytrade-early-sell?limit=5000&fast=1", { cache: "no-store" });
        const payload = await response.json() as Payload;
        if (stopped || !payload.ok) return;
        setSignals(uniqueSignals(payload));
        setTradeDate(payload.tradeDate ?? "—");
        setUpdatedAt(Date.now());
      } catch { /* retain last successful list */ } finally { busy = false; }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 5_000);
    return () => { stopped = true; timer.cancel(); };
  }, []);

  const tracked = useMemo(() => new Set(stocks.map((stock) => stock.ticker)), [stocks]);
  const visibleSignals = useMemo(() => signals.filter((signal) => tracked.has(signal.ticker) && selected.some((kind) => isChosen(kind, signal))), [selected, signals, tracked]);
  const addStock = (stock: WatchlistStock) => {
    if (stocks.some((item) => item.ticker === stock.ticker)) { setMessage(`${stock.ticker} 已在追蹤中`); return; }
    setStocks((current) => [...current, stock]); setQuery(""); setResults([]); setMessage(`已加入 ${stock.ticker} ${stock.name}`);
  };
  const addBatch = async () => {
    const codes = [...new Set(batch.toUpperCase().match(/[0-9A-Z]{2,12}/g) ?? [])].slice(0, 100);
    if (!codes.length) { setMessage("請輸入代號或以逗號、空格分隔多個代號"); return; }
    const response = await fetch(`/api/stock-search?tickers=${encodeURIComponent(codes.join(","))}`, { cache: "no-store" }).catch(() => null);
    const payload = response?.ok ? await response.json() as { stocks?: WatchlistStock[] } : {};
    const found = normalizeStocks(payload.stocks);
    const known = new Set(stocks.map((item) => item.ticker));
    const additions = found.filter((stock) => !known.has(stock.ticker));
    setStocks((current) => [...current, ...additions]); setBatch(""); setMessage(`已加入 ${additions.length} 檔；不限總追蹤檔數`);
  };

  return <section id="intraday-stock-tracking" className="intraday-tracking" aria-label="個股盤中訊號追蹤" tabIndex={-1}>
    <header className="intraday-tracking-head"><div><span className="eyebrow">PERSONAL INTRADAY SIGNAL TRACKER</span><h2>個股盤中訊號追蹤</h2><p>不限檔數追蹤自選個股；只顯示已勾選的盤中訊號，並保留交易日的訊號歷史。</p></div><aside><b>{stocks.length} 檔追蹤</b><small>資料日 {tradeDate}・{updatedAt ? `更新 ${new Date(updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}` : "連線中"}</small><small className={`intraday-tracking-sync is-${syncStatus.state}`} title={syncStatus.detail}>{syncStatus.label}</small></aside></header>
    <div className="intraday-tracking-controls"><label><span>輸入個股</span><input value={query} onChange={(event) => { setQuery(event.target.value); if (!event.target.value.trim()) setResults([]); }} placeholder="輸入代號或名稱，例如 2330、台積電" /></label><div className="tracking-search-results">{results.map((stock) => <button type="button" key={stock.ticker} onClick={() => addStock(stock)}><b>{stock.ticker}</b><strong>{stock.name}</strong><small>{stock.group}</small><StockTradingBadges ticker={stock.ticker} compact /><em>加入追蹤</em></button>)}</div><label className="tracking-batch"><span>批次加入</span><textarea value={batch} onChange={(event) => setBatch(event.target.value)} placeholder="可一次貼上 2330、2603、...（每次最多 100 檔）" /><button type="button" onClick={() => void addBatch()}>批次加入</button></label></div>
    <section className="tracking-options"><header><h3>勾選要追蹤的訊號</h3><button type="button" className={`tracking-reminder-toggle${remindersEnabled ? " is-on" : ""}`} role="switch" aria-checked={remindersEnabled} onClick={() => setRemindersEnabled((enabled) => !enabled)}><i />{remindersEnabled ? "追蹤提醒開啟" : "追蹤提醒關閉"}</button></header><p>開啟後，符合已勾選條件的追蹤個股會在盤中跳出提醒；關閉時只更新本頁清單。</p><div>{OPTIONS.map((option) => <label key={option.kind}><input type="checkbox" checked={selected.includes(option.kind)} onChange={() => setSelected((current) => current.includes(option.kind) ? current.filter((kind) => kind !== option.kind) : [...current, option.kind])} /><span><b>{option.title}</b><small>{option.detail}</small></span></label>)}</div></section>
    {message && <p className="tracking-message">{message}</p>}
    <section className="tracking-stocks"><header><h3>追蹤清單</h3><span>{stocks.length ? "可隨時移除，不影響自選股名單" : "尚未加入個股"}</span></header><div>{stocks.map((stock) => <article key={stock.ticker}><button type="button" onClick={() => onOpenStock(stock.ticker, stock.name)}><b>{stock.ticker}</b><strong>{stock.name}</strong><small>{stock.group}</small><StockTradingBadges ticker={stock.ticker} compact /></button><button type="button" aria-label={`移除 ${stock.ticker}`} onClick={() => setStocks((current) => current.filter((item) => item.ticker !== stock.ticker))}>移除</button></article>)}</div></section>
    <section className="tracking-signals"><header><h3>已追蹤的盤中訊號</h3><span>{visibleSignals.length} 則</span></header>{visibleSignals.length ? <div>{visibleSignals.map((signal) => <article key={`${signal.tradeDate}:${signal.ticker}:${signal.kind}:${signal.barTs}`} className={signal.kind.includes("Bear") || signal.kind.includes("Sell") ? "is-bear" : "is-bull"}><time>{formatTime(signal.barTs)}<small>{signal.tradeDate}</small></time><div><button type="button" onClick={() => onOpenStock(signal.ticker, signal.name)}><b>{signal.ticker} {signal.name}</b><StockTradingBadges ticker={signal.ticker} compact /><strong>{labelFor(signal)}</strong></button><p>{signal.note.replaceAll("\n", "｜")}</p></div><aside><small>訊號成交價</small><b>{signal.price}</b><button type="button" onClick={() => onOpenStock(signal.ticker, signal.name)}>開啟 5 分 K ›</button></aside></article>)}</div> : <p className="tracking-empty">尚無符合已勾選條件的追蹤訊號。訊號出現後會在 5 秒內更新。</p>}</section>
  </section>;
}
