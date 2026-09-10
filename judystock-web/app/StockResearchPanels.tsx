"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import { buildValuationRiverPoints, VALUATION_RIVER_WINDOW, valuationDistanceFilterMatches, valuationMedian, valuationRiverPosition, type ValuationDistanceFilter } from "../lib/valuation-river";
import StockTradingBadges from "./StockTradingBadges";

type ResearchPayload = {
  ok?: boolean;
  ticker?: string;
  updatedAt?: string;
  sources?: Record<string, boolean>;
  candles?: Array<{ date: string; close: number }>;
  valuation?: { pe: number | null; pb: number | null; dividendYield: number | null; eps: number | null; dataDate: string };
  transfers?: Array<{ id: string; identity: string; name: string; method: string; plannedShares: number | null; currentShares: number | null; afterShares: number | null; period: string; reportDate: string }>;
  directors?: Array<{ id: string; identity: string; name: string; shares: number | null; relatedShares: number | null; pledgeRatio: number | null; pledgedShares: number | null; reportDate: string; dataMonth: string }>;
};

type FlowPoint = { date: string; foreign: number; trust: number; dealer: number; hedge: number };

type ResearchProps = {
  ticker: string;
  name: string;
  market: "twse" | "tpex" | "etf";
  currentPrice: number | null;
  flows: FlowPoint[];
  onOpenKline: () => void;
};

type TdccPayload = { ok?: boolean; dataDate?: string; previousDate?: string | null; updatedAt?: string; stale?: boolean; rows?: Array<{ code: string; largeHolderPct: number; previousPct: number | null; weeklyChangePp: number | null }> };
type BranchPayload = { tradeDate?: string; rows?: Array<{ ticker: string; netAmount: number; netLots: number | null; concentration: number; activeBranches: number }> };
type ResearchUniverseRow = { code: string; name: string; market: "twse" | "tpex" | "etf" };
type TechnicalMarketRow = {
  code: string;
  name?: string;
  market: "twse" | "tpex";
  close: number;
  changePct?: number | null;
  riverBase: number | null;
  riverDistancePct: number | null;
  riverPosition: "昂貴" | "偏貴" | "便宜" | "特價" | null;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma240: number | null;
  maScore: number | null;
  maLabel: string;
  maBullConfirmed?: boolean;
  maBearConfirmed?: boolean;
  maLiveBaseSums?: Record<string, number | null>;
  technicalReady?: boolean;
};
type RiverScreenerPayload = { dataDate?: string; historyTradingDays?: number; historyReady?: boolean; riverReadyCount?: number; technicalReadyCount?: number; readyTarget?: number; rows?: TechnicalMarketRow[] };
type RiverFilter = "all" | "昂貴" | "偏貴" | "便宜" | "特價";
type RiverDistanceFilter = ValuationDistanceFilter;
type HolderFilter = "all" | "increase" | "decrease" | "flat";
type MaFilter = "all" | "bull10" | "bull12" | "bull15" | "bear5" | "bear0";
type Ma20BiasFilter = "all" | "near" | "steady" | "expanding" | "overheated";
type MaSignalConfig = { enabled: boolean; mode: "river" | "holder"; riverFilter?: RiverFilter; riverDistanceFilter?: RiverDistanceFilter; ma20BiasFilter?: Ma20BiasFilter; holderFilter?: HolderFilter; maFilter: MaFilter };
type RiverDisplayRow = TechnicalMarketRow & { name: string; groupName: string; displayMarket: ResearchUniverseRow["market"]; riverBase: number; riverDistancePct: number; riverPosition: Exclude<RiverFilter, "all"> };

const MA_SIGNAL_STORAGE_KEY = "hanstock-ma-signal-config-v1";
const MA_SIGNAL_EVENT = "hanstock-ma-signal-config";
const TECHNICAL_SNAPSHOT_STORAGE_KEY = "hanstock-technical-market-fast-v4";
const TDCC_SNAPSHOT_STORAGE_KEY = "hanstock-tdcc-radar-fast-v1";
const TDCC_SNAPSHOT_MAX_AGE_MS = 21 * 24 * 60 * 60 * 1_000;
const maFilterLabels: Record<MaFilter, string> = { all: "全部均線", bull10: "多頭排列 ≥10", bull12: "強多頭確認 ≥12", bull15: "完整多頭確認 15", bear5: "空頭確認 ≤5", bear0: "完整空頭確認 0" };
const riverDistanceFilters: RiverDistanceFilter[] = ["all", "near", "clear", "strong", "extreme"];
const riverDistanceFilterLabels: Record<RiverDistanceFilter, string> = { all: "全部距離", near: "近水線 0–5%", clear: "明顯 5–15%", strong: "強烈 15–30%", extreme: "極端 30%以上" };
const ma20BiasFilters: Ma20BiasFilter[] = ["all", "near", "steady", "expanding", "overheated"];
const ma20BiasFilterLabels: Record<Ma20BiasFilter, string> = { all: "全部乖離", near: "貼近月線 0–3%", steady: "穩健 3–8%", expanding: "擴張 8–15%", overheated: "過熱 15%以上" };

function maFilterMatches(row: Pick<TechnicalMarketRow, "maScore" | "maBullConfirmed" | "maBearConfirmed">, filter: MaFilter) {
  const score = row.maScore;
  if (filter === "all") return true;
  if (score === null || score === undefined) return false;
  if (filter === "bull10") return score >= 10;
  if (filter === "bull12") return score >= 12 && row.maBullConfirmed === true;
  if (filter === "bull15") return score === 15 && row.maBullConfirmed === true;
  if (filter === "bear5") return score <= 5 && row.maBearConfirmed === true;
  return score === 0 && row.maBearConfirmed === true;
}

function ma20BiasPct(row: Pick<TechnicalMarketRow, "close" | "ma20">) {
  return typeof row.ma20 === "number" && row.ma20 > 0 && Number.isFinite(row.close) ? Math.abs((row.close / row.ma20 - 1) * 100) : null;
}

function priceChangeFromPct(close: number, changePct: number | null | undefined) {
  if (typeof changePct !== "number" || !Number.isFinite(changePct) || changePct <= -100 || !Number.isFinite(close)) return null;
  const previousClose = close / (1 + changePct / 100);
  const change = close - previousClose;
  return Number.isFinite(change) ? change : null;
}

function ma20BiasFilterMatches(row: Pick<TechnicalMarketRow, "close" | "ma20">, filter: Ma20BiasFilter) {
  if (filter === "all") return true;
  const bias = ma20BiasPct(row);
  if (bias === null) return false;
  if (filter === "near") return bias <= 3;
  if (filter === "steady") return bias > 3 && bias <= 8;
  if (filter === "expanding") return bias > 8 && bias <= 15;
  return bias > 15;
}

function riverPositionAt(price: number, base: number) {
  return valuationRiverPosition(price, base);
}

function liveMaState(row: TechnicalMarketRow, price: number) {
  const periods = [5, 10, 20, 60, 120, 240];
  const sums = row.maLiveBaseSums;
  if (!sums) return { score: row.maScore, bullConfirmed: row.maBullConfirmed ?? false, bearConfirmed: row.maBearConfirmed ?? false };
  const averages = periods.map((period) => {
    const sum = sums[String(period)];
    return typeof sum === "number" ? (sum + price) / period : null;
  });
  if (averages.some((value) => value === null)) return { score: row.maScore, bullConfirmed: row.maBullConfirmed ?? false, bearConfirmed: row.maBearConfirmed ?? false };
  let score = 0;
  for (let fast = 0; fast < averages.length; fast += 1) for (let slow = fast + 1; slow < averages.length; slow += 1) {
    if (averages[fast]! > averages[slow]!) score += 1;
  }
  const bullConfirmed = score >= 12 && price >= averages[0]! && averages[0]! >= (row.ma5 ?? Infinity) && averages[1]! >= (row.ma10 ?? Infinity) && averages[2]! >= (row.ma20 ?? Infinity);
  const bearConfirmed = score <= 5 && price <= averages[0]! && averages[0]! <= (row.ma5 ?? -Infinity) && averages[1]! <= (row.ma10 ?? -Infinity) && averages[2]! <= (row.ma20 ?? -Infinity);
  return { score, bullConfirmed, bearConfirmed };
}

let sharedTechnicalPayload: RiverScreenerPayload | null = null;
let sharedTechnicalBackfilling = false;
let sharedTechnicalLoad: Promise<void> | null = null;
const technicalListeners = new Set<(payload: RiverScreenerPayload | null, backfilling: boolean) => void>();

function publishTechnicalState(payload: RiverScreenerPayload | null, backfilling: boolean) {
  sharedTechnicalPayload = payload;
  sharedTechnicalBackfilling = backfilling;
  if (payload?.rows?.length && typeof window !== "undefined") {
    try { localStorage.setItem(TECHNICAL_SNAPSHOT_STORAGE_KEY, JSON.stringify(payload)); } catch { /* 瀏覽器容量不足時仍保留伺服器快照。 */ }
  }
  technicalListeners.forEach((listener) => listener(payload, backfilling));
}

function readTechnicalSnapshot() {
  if (typeof window === "undefined") return null;
  try {
    const payload = JSON.parse(localStorage.getItem(TECHNICAL_SNAPSHOT_STORAGE_KEY) ?? "null") as RiverScreenerPayload | null;
    return payload?.rows?.length ? payload : null;
  } catch { return null; }
}

function ensureTechnicalMarket() {
  if (sharedTechnicalLoad) return sharedTechnicalLoad;
  sharedTechnicalLoad = (async () => {
    publishTechnicalState(sharedTechnicalPayload, !sharedTechnicalPayload);
    try {
      const response = await fetch("/api/technical-market?fast=1&quoteRev=latest-trading-day-v1", { cache: "default", signal: AbortSignal.timeout(15_000) });
      const next = response.ok ? await response.json() as RiverScreenerPayload : null;
      if (!next) return;
      publishTechnicalState(next, false);
    } catch {
      publishTechnicalState(sharedTechnicalPayload, false);
    } finally {
      sharedTechnicalLoad = null;
    }
  })();
  return sharedTechnicalLoad;
}

function useTechnicalMarket() {
  const [payload, setPayload] = useState<RiverScreenerPayload | null>(sharedTechnicalPayload);
  const [backfilling, setBackfilling] = useState(sharedTechnicalBackfilling);
  useEffect(() => {
    if (!sharedTechnicalPayload) {
      const stored = readTechnicalSnapshot();
      if (stored) publishTechnicalState(stored, false);
    }
    const listener = (next: RiverScreenerPayload | null, filling: boolean) => {
      setPayload(next);
      setBackfilling(filling);
    };
    technicalListeners.add(listener);
    listener(sharedTechnicalPayload, sharedTechnicalBackfilling);
    void ensureTechnicalMarket();
    return () => { technicalListeners.delete(listener); };
  }, []);
  return { payload, backfilling };
}

type StoredTdccSnapshot = { savedAt: number; payload: TdccPayload };
let sharedTdccPayload: TdccPayload | null = null;
let sharedTdccRefreshing = false;
let sharedTdccLoad: Promise<void> | null = null;
const tdccListeners = new Set<(payload: TdccPayload | null, refreshing: boolean) => void>();

function publishTdccState(payload: TdccPayload | null, refreshing: boolean) {
  sharedTdccPayload = payload;
  sharedTdccRefreshing = refreshing;
  if (payload?.rows?.length && typeof window !== "undefined") {
    try {
      const snapshot: StoredTdccSnapshot = { savedAt: Date.now(), payload };
      localStorage.setItem(TDCC_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot));
    } catch { /* 瀏覽器容量不足時仍沿用記憶體與伺服器快照。 */ }
  }
  tdccListeners.forEach((listener) => listener(payload, refreshing));
}

function readTdccSnapshot() {
  if (typeof window === "undefined") return null;
  try {
    const snapshot = JSON.parse(localStorage.getItem(TDCC_SNAPSHOT_STORAGE_KEY) ?? "null") as StoredTdccSnapshot | null;
    if (!snapshot?.payload?.rows?.length || Date.now() - snapshot.savedAt > TDCC_SNAPSHOT_MAX_AGE_MS) return null;
    return snapshot.payload;
  } catch { return null; }
}

function ensureTdccRadar(force = false) {
  if (sharedTdccLoad) return sharedTdccLoad;
  sharedTdccLoad = (async () => {
    publishTdccState(sharedTdccPayload, true);
    try {
      const endpoint = force ? "/api/tdcc-radar?refresh=1" : "/api/tdcc-radar";
      const response = await fetch(endpoint, { cache: force ? "no-store" : "default", signal: AbortSignal.timeout(force ? 30_000 : 10_000) });
      const next = response.ok ? await response.json() as TdccPayload : null;
      publishTdccState(next?.rows?.length ? next : sharedTdccPayload, false);
    } catch {
      publishTdccState(sharedTdccPayload, false);
    } finally {
      sharedTdccLoad = null;
    }
  })();
  return sharedTdccLoad;
}

function useTdccRadar() {
  const [payload, setPayload] = useState<TdccPayload | null>(sharedTdccPayload);
  const [refreshing, setRefreshing] = useState(sharedTdccRefreshing);
  useEffect(() => {
    if (!sharedTdccPayload) {
      const stored = readTdccSnapshot();
      if (stored) publishTdccState(stored, false);
    }
    const listener = (next: TdccPayload | null, loading: boolean) => {
      setPayload(next);
      setRefreshing(loading);
    };
    tdccListeners.add(listener);
    listener(sharedTdccPayload, sharedTdccRefreshing);
    void ensureTdccRadar();
    return () => { tdccListeners.delete(listener); };
  }, []);
  return { payload, refreshing, refresh: () => ensureTdccRadar(true) };
}

function useMaSignalRegistration(config: Omit<MaSignalConfig, "enabled">) {
  const [enabled, setEnabled] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      const stored = JSON.parse(localStorage.getItem(MA_SIGNAL_STORAGE_KEY) ?? "null") as MaSignalConfig | null;
      return Boolean(stored?.enabled && stored.mode === config.mode);
    } catch { return false; }
  });
  useEffect(() => {
    if (!enabled) return;
    const next = { ...config, enabled: true } satisfies MaSignalConfig;
    localStorage.setItem(MA_SIGNAL_STORAGE_KEY, JSON.stringify(next));
    window.dispatchEvent(new CustomEvent(MA_SIGNAL_EVENT, { detail: next }));
  }, [config, enabled]);
  const toggle = () => {
    const nextEnabled = !enabled;
    setEnabled(nextEnabled);
    const next = { ...config, enabled: nextEnabled } satisfies MaSignalConfig;
    localStorage.setItem(MA_SIGNAL_STORAGE_KEY, JSON.stringify(next));
    window.dispatchEvent(new CustomEvent(MA_SIGNAL_EVENT, { detail: next }));
  };
  return { enabled, toggle };
}

function formatNumber(value: number | null | undefined, digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-TW", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
}

function formatShares(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 100_000_000) return `${formatNumber(value / 100_000_000, 1)} 億股`;
  if (Math.abs(value) >= 10_000) return `${formatNumber(value / 10_000, 1)} 萬股`;
  return `${Math.round(value).toLocaleString("zh-TW")} 股`;
}

function formatAmount(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const absolute = Math.abs(value);
  if (absolute >= 100_000_000) return `${sign}${formatNumber(absolute / 100_000_000, 1)} 億`;
  if (absolute >= 10_000) return `${sign}${formatNumber(absolute / 10_000, 1)} 萬`;
  return `${sign}${Math.round(absolute).toLocaleString("zh-TW")}`;
}

function useResearch(ticker: string, market: ResearchProps["market"]) {
  const [data, setData] = useState<ResearchPayload | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/stock-research?ticker=${encodeURIComponent(ticker)}&market=${market === "tpex" ? "tpex" : "twse"}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<ResearchPayload> : Promise.reject(new Error("research_failed")))
      .then(setData)
      .catch(() => { if (!controller.signal.aborted) setData(null); });
    return () => controller.abort();
  }, [ticker, market]);
  return { data: data?.ticker === ticker ? data : null, loading: data?.ticker !== ticker };
}

export function ValuationRiverScreener({ stocks, groupByTicker, onSelect, onOpenKline }: { stocks: ResearchUniverseRow[]; groupByTicker: Map<string, string>; onSelect: (ticker: string) => void; onOpenKline: (ticker: string, name: string) => void }) {
  const { payload, backfilling } = useTechnicalMarket();
  const [filter, setFilter] = useState<RiverFilter>("all");
  const [distanceFilter, setDistanceFilter] = useState<RiverDistanceFilter>("all");
  const [maFilter, setMaFilter] = useState<MaFilter>("all");
  const [ma20BiasFilter, setMa20BiasFilter] = useState<Ma20BiasFilter>("all");
  const [limit, setLimit] = useState(30);
  const signalConfig = useMemo(() => ({ mode: "river" as const, riverFilter: filter, riverDistanceFilter: distanceFilter, ma20BiasFilter, maFilter }), [filter, distanceFilter, ma20BiasFilter, maFilter]);
  const signal = useMaSignalRegistration(signalConfig);
  const stockMap = useMemo(() => new Map(stocks.map((row) => [row.code, row])), [stocks]);
  const displayRows = useMemo(() => (payload?.rows ?? []).flatMap((row): RiverDisplayRow[] => {
    const stock = stockMap.get(row.code);
    return row.riverBase !== null && row.riverDistancePct !== null && row.riverPosition
      ? [{ ...row, name: stock?.name ?? row.name ?? row.code, groupName: groupByTicker.get(row.code) ?? (stock?.market === "etf" ? "ETF" : "未分類"), displayMarket: stock?.market ?? row.market, riverBase: row.riverBase, riverDistancePct: row.riverDistancePct, riverPosition: row.riverPosition }]
      : [];
  }), [groupByTicker, payload, stockMap]);
  const rows = useMemo(() => displayRows.filter((row) => (filter === "all" || row.riverPosition === filter) && valuationDistanceFilterMatches(row.riverDistancePct, distanceFilter) && maFilterMatches(row, maFilter) && ma20BiasFilterMatches(row, ma20BiasFilter)).sort((a, b) => {
    if (maFilter.startsWith("bull")) return (b.maScore ?? -1) - (a.maScore ?? -1) || a.riverDistancePct - b.riverDistancePct;
    if (maFilter.startsWith("bear")) return (a.maScore ?? 16) - (b.maScore ?? 16) || b.riverDistancePct - a.riverDistancePct;
    if (filter === "特價" || filter === "便宜") return a.riverDistancePct - b.riverDistancePct;
    if (filter === "偏貴" || filter === "昂貴") return b.riverDistancePct - a.riverDistancePct;
    return Math.abs(b.riverDistancePct) - Math.abs(a.riverDistancePct);
  }), [displayRows, filter, distanceFilter, maFilter, ma20BiasFilter]);
  const counts = useMemo(() => displayRows.reduce<Record<RiverFilter, number>>((result, row) => {
    if (row.riverPosition) {
      result[row.riverPosition] += 1;
      result.all += 1;
    }
    return result;
  }, { all: 0, 昂貴: 0, 偏貴: 0, 便宜: 0, 特價: 0 }), [displayRows]);
  const distanceCounts = useMemo(() => displayRows.filter((row) => filter === "all" || row.riverPosition === filter).reduce<Record<RiverDistanceFilter, number>>((result, row) => {
    riverDistanceFilters.forEach((item) => { if (valuationDistanceFilterMatches(row.riverDistancePct, item)) result[item] += 1; });
    return result;
  }, { all: 0, near: 0, clear: 0, strong: 0, extreme: 0 }), [displayRows, filter]);
  const maCounts = useMemo(() => displayRows.filter((row) => (filter === "all" || row.riverPosition === filter) && valuationDistanceFilterMatches(row.riverDistancePct, distanceFilter)).reduce<Record<MaFilter, number>>((result, row) => {
    (Object.keys(result) as MaFilter[]).forEach((item) => { if (maFilterMatches(row, item)) result[item] += 1; });
    return result;
  }, { all: 0, bull10: 0, bull12: 0, bull15: 0, bear5: 0, bear0: 0 }), [displayRows, filter, distanceFilter]);
  const ma20BiasCounts = useMemo(() => displayRows.filter((row) => (filter === "all" || row.riverPosition === filter) && valuationDistanceFilterMatches(row.riverDistancePct, distanceFilter) && maFilterMatches(row, maFilter)).reduce<Record<Ma20BiasFilter, number>>((result, row) => {
    ma20BiasFilters.forEach((item) => { if (ma20BiasFilterMatches(row, item)) result[item] += 1; });
    return result;
  }, { all: 0, near: 0, steady: 0, expanding: 0, overheated: 0 }), [displayRows, filter, distanceFilter, maFilter]);
  const selectFilter = (next: RiverFilter) => { setFilter(next); setLimit(30); };
  const selectDistanceFilter = (next: RiverDistanceFilter) => { setDistanceFilter(next); setLimit(30); };
  const selectMaFilter = (next: MaFilter) => { if (next === "all" && signal.enabled) signal.toggle(); setMaFilter(next); setLimit(30); };
  const selectMa20BiasFilter = (next: Ma20BiasFilter) => { setMa20BiasFilter(next); setLimit(30); };
  return (
    <section className="market-research-screener river-screener" aria-label="全市場歷史價位位置均線篩選">
      <header><div><span className="eyebrow">HISTORICAL PRICE POSITION × MA SCREENER</span><h3>全市場歷史價位位置均線選股</h3><p>依最近 160 個交易日收盤價中位數劃分價位位置，再交叉 6 條均線的 15 組排列分數；不使用 EPS 評價。</p></div><strong>資料日 {payload?.dataDate ?? "讀取中"}</strong></header>
      <div className="research-filter-buttons" role="group" aria-label="歷史價格位置區間篩選">
        {(["all", "昂貴", "偏貴", "便宜", "特價"] as RiverFilter[]).map((item) => <button type="button" key={item} className={`${filter === item ? "active" : ""} filter-${item}`} onClick={() => selectFilter(item)}><strong>{item === "all" ? "全部" : item}</strong><small>{counts[item]} 檔</small></button>)}
      </div>
      <div className="river-distance-filter"><header><span>🎯 距分水嶺強度</span><small>第二層濾網；可再搭配下方均線分數縮小名單</small></header><div className="research-filter-buttons" role="group" aria-label="距分水嶺強度篩選">{riverDistanceFilters.map((item) => <button type="button" key={item} className={distanceFilter === item ? "active" : ""} onClick={() => selectDistanceFilter(item)}><strong>{riverDistanceFilterLabels[item]}</strong><small>{distanceCounts[item]} 檔</small></button>)}</div></div>
      <div className="ma-score-filter">
        <header><span>⭐ 均線分數篩選</span><small>15 分是排列分數；強多頭另需收盤站上 MA5，且 MA5／10／20 同步上升</small><button type="button" className={signal.enabled ? "is-on" : ""} disabled={maFilter === "all" || maCounts[maFilter] === 0} onClick={signal.toggle}>{signal.enabled ? "🔔 盤中提醒已開啟" : maFilter === "all" ? "先選均線條件" : maCounts[maFilter] === 0 ? "目前尚無訊號" : "🔕 開啟盤中提醒"}</button></header>
        <div role="group" aria-label="均線分數篩選">{(["all", "bull10", "bull12", "bull15", "bear5", "bear0"] as MaFilter[]).map((item) => <button type="button" key={item} className={maFilter === item ? "active" : ""} onClick={() => selectMaFilter(item)}><strong>{maFilterLabels[item]}</strong><small>{maCounts[item]} 檔</small></button>)}</div>
        {!payload?.historyReady && <p>六均線正在逐檔更新：已完成 {payload?.technicalReadyCount ?? 0}/{payload?.readyTarget ?? "—"} 檔{backfilling ? "，畫面會自動更新" : "，下一次流量會接續補齊"}。歷史價格位置區間可先使用，不必等待全部完成。</p>}
      </div>
      <div className="river-distance-filter ma20-bias-filter"><header><span>📐 距 20MA 乖離率</span><small>第三層濾網；排除已離月線過遠的過熱股票（取絕對值）</small></header><div className="research-filter-buttons" role="group" aria-label="距 20MA 乖離率篩選">{ma20BiasFilters.map((item) => <button type="button" key={item} className={ma20BiasFilter === item ? "active" : ""} onClick={() => selectMa20BiasFilter(item)}><strong>{ma20BiasFilterLabels[item]}</strong><small>{ma20BiasCounts[item]} 檔</small></button>)}</div></div>
      {!payload && <div className="screener-empty">正在整理全市場歷史價格位置…</div>}
      {payload && rows.length === 0 && <div className="screener-empty">{counts.all === 0 && backfilling ? "正在建立近 160 個交易日價格位置區間，第一批完成後會自動顯示" : "目前沒有符合這個區間的股票"}</div>}
      {rows.length > 0 && <div className="research-screen-table-scroll" role="region" aria-label={`${filter === "all" ? "全部" : filter}歷史價格位置股票`} tabIndex={0}><div className="research-screen-table river-screen-table is-ma-table"><div className="research-screen-row is-head"><span>股票</span><span>漲跌幅</span><span>漲跌</span><span>成交價</span><span>族群</span><span>市場</span><span>均線分數</span><span>目前位置</span><span>距分水嶺</span></div>{rows.slice(0, limit).map((row) => { const priceChange = priceChangeFromPct(row.close, row.changePct); return <button type="button" className="research-screen-row" key={row.code} title={`開啟 ${row.code} ${row.name} 五分鐘 K 線`} aria-label={`開啟 ${row.code} ${row.name} 五分鐘 K 線，可切換一分鐘與日線`} onClick={() => { onSelect(row.code); onOpenKline(row.code, row.name); }}><strong>{row.code}<small>{row.name}　› 五分 K</small><StockTradingBadges ticker={row.code} compact /></strong><b className={(row.changePct ?? 0) < 0 ? "negative" : (row.changePct ?? 0) > 0 ? "positive" : "neutral"}>{row.changePct === null || row.changePct === undefined ? "—" : `${row.changePct > 0 ? "+" : ""}${formatNumber(row.changePct, 2)}%`}</b><b className={(priceChange ?? 0) < 0 ? "negative" : (priceChange ?? 0) > 0 ? "positive" : "neutral"}>{priceChange === null ? "—" : `${priceChange > 0 ? "+" : ""}${formatNumber(priceChange, 2)} 元`}</b><span>{formatNumber(row.close, 2)}</span><span className="river-group" title={row.groupName}>{row.groupName}</span><span>{row.displayMarket === "twse" ? "上市" : row.displayMarket === "tpex" ? "上櫃" : "ETF"}</span><b className={(row.maScore ?? 0) >= 10 ? "positive" : (row.maScore ?? 15) <= 5 ? "negative" : "neutral"}>{row.maScore === null ? "資料不足" : `${row.maScore}/15`}<small>{row.maLabel}{ma20BiasPct(row) === null ? "" : ` · 距20MA ${formatNumber(ma20BiasPct(row), 1)}%`}</small></b><i className={`river-label is-${row.riverPosition}`}>{row.riverPosition}</i><em className={row.riverDistancePct < 0 ? "negative" : "positive"}>{row.riverDistancePct > 0 ? "+" : ""}{formatNumber(row.riverDistancePct, 1)}%</em></button>; })}</div></div>}
      {rows.length > limit && <button className="screener-more" type="button" onClick={() => setLimit((current) => current + 30)}>顯示更多（尚有 {rows.length - limit} 檔）</button>}
    </section>
  );
}

export function LargeHolderScreener({ stocks, onSelect }: { stocks: ResearchUniverseRow[]; onSelect: (ticker: string) => void }) {
  const { payload, refreshing } = useTdccRadar();
  const { payload: technicalPayload, backfilling } = useTechnicalMarket();
  const [filter, setFilter] = useState<HolderFilter>("all");
  const [maFilter, setMaFilter] = useState<MaFilter>("all");
  const [limit, setLimit] = useState(30);
  const signalConfig = useMemo(() => ({ mode: "holder" as const, holderFilter: filter, maFilter }), [filter, maFilter]);
  const signal = useMaSignalRegistration(signalConfig);
  const stockMap = useMemo(() => new Map(stocks.map((row) => [row.code, row])), [stocks]);
  const technicalMap = useMemo(() => new Map((technicalPayload?.rows ?? []).map((row) => [row.code, row])), [technicalPayload]);
  const matches = (change: number | null, selected: HolderFilter) => selected === "all" || selected === "increase" && change !== null && change > 0 || selected === "decrease" && change !== null && change < 0 || selected === "flat" && change === 0;
  const rows = useMemo(() => (payload?.rows ?? []).flatMap((row) => {
    const stock = stockMap.get(row.code);
    const technical = technicalMap.get(row.code);
    return stock ? [{ ...row, name: stock.name, market: stock.market, maScore: technical?.maScore ?? null, maLabel: technical?.maLabel ?? "資料不足", maBullConfirmed: technical?.maBullConfirmed, maBearConfirmed: technical?.maBearConfirmed }] : [];
  }).filter((row) => matches(row.weeklyChangePp, filter) && maFilterMatches(row, maFilter)).sort((a, b) => {
    if (maFilter.startsWith("bull")) return (b.maScore ?? -1) - (a.maScore ?? -1) || (b.weeklyChangePp ?? 0) - (a.weeklyChangePp ?? 0);
    if (maFilter.startsWith("bear")) return (a.maScore ?? 16) - (b.maScore ?? 16) || (a.weeklyChangePp ?? 0) - (b.weeklyChangePp ?? 0);
    const changeA = a.weeklyChangePp ?? 0;
    const changeB = b.weeklyChangePp ?? 0;
    if (filter === "decrease") return changeA - changeB;
    if (filter === "increase") return changeB - changeA;
    return Math.abs(changeB) - Math.abs(changeA);
  }), [payload, stockMap, technicalMap, filter, maFilter]);
  const counts = useMemo(() => (payload?.rows ?? []).reduce<Record<HolderFilter, number>>((result, row) => {
    result.all += 1;
    if (row.weeklyChangePp !== null && row.weeklyChangePp > 0) result.increase += 1;
    else if (row.weeklyChangePp !== null && row.weeklyChangePp < 0) result.decrease += 1;
    else if (row.weeklyChangePp === 0) result.flat += 1;
    return result;
  }, { all: 0, increase: 0, decrease: 0, flat: 0 }), [payload]);
  const maCounts = useMemo(() => (payload?.rows ?? []).filter((row) => matches(row.weeklyChangePp, filter)).reduce<Record<MaFilter, number>>((result, row) => {
    const technical = technicalMap.get(row.code) ?? { maScore: null };
    (Object.keys(result) as MaFilter[]).forEach((item) => { if (maFilterMatches(technical, item)) result[item] += 1; });
    return result;
  }, { all: 0, bull10: 0, bull12: 0, bull15: 0, bear5: 0, bear0: 0 }), [payload, technicalMap, filter]);
  const labels: Record<HolderFilter, string> = { all: "全部", increase: "持股增加", decrease: "持股減少", flat: "持平" };
  const selectFilter = (next: HolderFilter) => { setFilter(next); setLimit(30); };
  const selectMaFilter = (next: MaFilter) => { if (next === "all" && signal.enabled) signal.toggle(); setMaFilter(next); setLimit(30); };
  return (
    <section className="market-research-screener holder-screener" aria-label="400張以上大戶持股篩選">
      <header><div><span className="eyebrow">400+ HOLDER SCREENER</span><h3>400 張以上大戶持股選股</h3><p>依 TDCC 本週與前週持股比例變化，快速找出大戶持股增加或減少的股票。</p></div><strong>{payload?.dataDate ? `${payload.previousDate ?? "前期"} → ${payload.dataDate}${refreshing ? "・背景確認中" : ""}` : "首次建立快照中"}</strong></header>
      <div className="research-filter-buttons holder-filter-buttons" role="group" aria-label="大戶持股變化篩選">{(["all", "increase", "decrease", "flat"] as HolderFilter[]).map((item) => <button type="button" key={item} className={`${filter === item ? "active" : ""} filter-${item}`} onClick={() => selectFilter(item)}><strong>{labels[item]}</strong><small>{counts[item]} 檔</small></button>)}</div>
      <div className="ma-score-filter holder-ma-score-filter">
        <header><span>⭐ 均線分數交叉篩選</span><small>大戶異動 × 6 MA 排列</small><button type="button" className={signal.enabled ? "is-on" : ""} disabled={maFilter === "all" || maCounts[maFilter] === 0} onClick={signal.toggle}>{signal.enabled ? "🔔 盤中提醒已開啟" : maFilter === "all" ? "先選均線條件" : maCounts[maFilter] === 0 ? "目前尚無訊號" : "🔕 開啟盤中提醒"}</button></header>
        <div role="group" aria-label="大戶持股均線分數篩選">{(["all", "bull10", "bull12", "bull15", "bear5", "bear0"] as MaFilter[]).map((item) => <button type="button" key={item} className={maFilter === item ? "active" : ""} onClick={() => selectMaFilter(item)}><strong>{maFilterLabels[item]}</strong><small>{item === "all" ? `${technicalPayload?.technicalReadyCount ?? 0} 檔已算` : `${maCounts[item]} 檔`}</small></button>)}</div>
        {!technicalPayload?.historyReady && <p>六均線逐檔更新中：已完成 {technicalPayload?.technicalReadyCount ?? 0}/{technicalPayload?.readyTarget ?? "—"} 檔{backfilling ? "，畫面會自動更新" : ""}。</p>}
      </div>
      {!payload && <div className="screener-empty">正在整理全市場大戶持股變化…</div>}
      {payload && rows.length === 0 && <div className="screener-empty">目前沒有符合條件的股票</div>}
      {rows.length > 0 && <div className="research-screen-table-scroll" role="region" aria-label={`${labels[filter]}大戶持股股票`} tabIndex={0}><div className="research-screen-table holder-screen-table is-ma-table"><div className="research-screen-row is-head"><span>股票</span><span>本週大戶持股</span><span>前週大戶持股</span><span>週變化</span><span>均線分數</span><span>判讀</span></div>{rows.slice(0, limit).map((row) => <button type="button" className="research-screen-row" key={row.code} onClick={() => onSelect(row.code)}><strong>{row.code}<small>{row.name}</small><StockTradingBadges ticker={row.code} compact /></strong><b>{formatNumber(row.largeHolderPct, 1)}%</b><span>{row.previousPct === null ? "—" : `${formatNumber(row.previousPct, 1)}%`}</span><em className={(row.weeklyChangePp ?? 0) < 0 ? "negative" : (row.weeklyChangePp ?? 0) > 0 ? "positive" : "neutral"}>{row.weeklyChangePp === null ? "—" : `${row.weeklyChangePp > 0 ? "+" : ""}${formatNumber(row.weeklyChangePp, 1)}pp`}</em><b className={(row.maScore ?? 0) >= 10 ? "positive" : (row.maScore ?? 15) <= 5 ? "negative" : "neutral"}>{row.maScore === null ? "資料不足" : `${row.maScore}/15`}<small>{row.maLabel}</small></b><i className={(row.weeklyChangePp ?? 0) < 0 ? "negative" : (row.weeklyChangePp ?? 0) > 0 ? "positive" : "neutral"}>{row.weeklyChangePp === null ? "無前期" : row.weeklyChangePp > 0 ? "持股增加" : row.weeklyChangePp < 0 ? "持股減少" : "持平"}</i></button>)}</div></div>}
      {rows.length > limit && <button className="screener-more" type="button" onClick={() => setLimit((current) => current + 30)}>顯示更多（尚有 {rows.length - limit} 檔）</button>}
    </section>
  );
}

type MaLiveAlert = { code: string; name: string; price: number; score: number; position: string; detail: string; detectedAt: string };

function taipeiMarketIsOpen() {
  const now = new Date(Date.now() + 8 * 60 * 60 * 1000);
  const weekday = now.getUTCDay();
  const minutes = now.getUTCHours() * 60 + now.getUTCMinutes();
  return weekday >= 1 && weekday <= 5 && minutes >= 9 * 60 && minutes <= 13 * 60 + 30;
}

function holderFilterMatches(change: number | null, filter: HolderFilter) {
  return filter === "all" || filter === "increase" && change !== null && change > 0 || filter === "decrease" && change !== null && change < 0 || filter === "flat" && change === 0;
}

export function MaScreenerSignalNotifier({ stocks, onOpenStock }: { stocks: ResearchUniverseRow[]; onOpenStock: (ticker: string, name: string) => void }) {
  const [config, setConfig] = useState<MaSignalConfig | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const stored = JSON.parse(localStorage.getItem(MA_SIGNAL_STORAGE_KEY) ?? "null") as MaSignalConfig | null;
      return stored?.enabled ? stored : null;
    } catch { return null; }
  });
  const [alerts, setAlerts] = useState<MaLiveAlert[]>([]);
  const technicalRef = useRef<RiverScreenerPayload | null>(null);
  const holderRef = useRef<TdccPayload | null>(null);
  const seenRef = useRef(new Set<string>());
  const stockMap = useMemo(() => new Map(stocks.map((row) => [row.code, row])), [stocks]);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<MaSignalConfig>).detail;
      setConfig(detail?.enabled ? detail : null);
      setAlerts([]);
    };
    window.addEventListener(MA_SIGNAL_EVENT, listener);
    return () => window.removeEventListener(MA_SIGNAL_EVENT, listener);
  }, []);

  useEffect(() => {
    seenRef.current.clear();
    if (!config || config.maFilter === "all") return;
    let stopped = false;
    let running = false;
    let lastStaticLoad = 0;

    const loadStatic = async () => {
      if (Date.now() - lastStaticLoad < 60_000 && technicalRef.current) return;
      const requests: Array<Promise<unknown>> = [fetch("/api/technical-market", { cache: "no-store" }).then((response) => response.ok ? response.json() : null)];
      if (config.mode === "holder") requests.push(fetch("/api/tdcc-radar", { cache: "no-store" }).then((response) => response.ok ? response.json() : null));
      const [technical, holder] = await Promise.all(requests);
      if (technical) technicalRef.current = technical as RiverScreenerPayload;
      if (holder) holderRef.current = holder as TdccPayload;
      lastStaticLoad = Date.now();
    };

    const nearThreshold = (score: number | null) => {
      if (score === null) return false;
      if (config.maFilter === "bull10") return score >= 8;
      if (config.maFilter === "bull12") return score >= 10;
      if (config.maFilter === "bull15") return score >= 13;
      if (config.maFilter === "bear5") return score <= 7;
      return score <= 2;
    };

    const poll = async () => {
      if (running || stopped || !taipeiMarketIsOpen()) return;
      running = true;
      try {
        await loadStatic();
        const technical = technicalRef.current;
        if (!(technical?.rows ?? []).some((row) => row.technicalReady)) return;
        const holderMap = new Map((holderRef.current?.rows ?? []).map((row) => [row.code, row]));
        const candidates = (technical.rows ?? []).filter((row) => {
          if (!row.technicalReady || !nearThreshold(row.maScore)) return false;
          if (config.mode !== "holder") return true;
          return holderFilterMatches(holderMap.get(row.code)?.weeklyChangePp ?? null, config.holderFilter ?? "all");
        }).sort((a, b) => config.maFilter.startsWith("bull") ? (b.maScore ?? -1) - (a.maScore ?? -1) : (a.maScore ?? 16) - (b.maScore ?? 16)).slice(0, 250);
        const batches = Array.from({ length: Math.ceil(candidates.length / 50) }, (_, index) => candidates.slice(index * 50, index * 50 + 50));
        const quotePayloads = await Promise.all(batches.map((batch) => fetch(`/api/quotes?items=${encodeURIComponent(batch.map((row) => `${row.market}:${row.code}`).join(","))}`, { cache: "no-store" }).then((response) => response.ok ? response.json() as Promise<{ quotes?: Array<{ code: string; price: number | null }> }> : null)));
        const quotes = new Map(quotePayloads.flatMap((payload) => payload?.quotes ?? []).flatMap((quote) => typeof quote.price === "number" ? [[quote.code, quote.price] as const] : []));
        const matches = candidates.flatMap((row) => {
          const price = quotes.get(row.code);
          if (price === undefined || row.riverBase === null) return [];
          const live = liveMaState(row, price);
          const position = riverPositionAt(price, row.riverBase);
          const distance = (price / row.riverBase - 1) * 100;
          if (!maFilterMatches({ maScore: live.score, maBullConfirmed: live.bullConfirmed, maBearConfirmed: live.bearConfirmed }, config.maFilter)) return [];
          if (config.mode === "river" && (config.riverFilter ?? "all") !== "all" && position !== config.riverFilter) return [];
          if (config.mode === "river" && !valuationDistanceFilterMatches(distance, config.riverDistanceFilter ?? "all")) return [];
          if (config.mode === "river" && !ma20BiasFilterMatches({ close: price, ma20: row.ma20 }, config.ma20BiasFilter ?? "all")) return [];
          const holderChange = holderMap.get(row.code)?.weeklyChangePp ?? null;
          const stock = stockMap.get(row.code);
          return [{
            code: row.code,
            name: stock?.name ?? row.code,
            price,
            score: live.score ?? row.maScore ?? 0,
            position,
            detail: config.mode === "holder" ? `大戶週變化 ${holderChange === null ? "—" : `${holderChange > 0 ? "+" : ""}${holderChange.toFixed(2)}pp`}` : `距分水嶺 ${distance > 0 ? "+" : ""}${distance.toFixed(1)}%`,
            detectedAt: new Date().toLocaleTimeString("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
          } satisfies MaLiveAlert];
        });
        const fresh = matches.filter((row) => !seenRef.current.has(row.code));
        matches.forEach((row) => seenRef.current.add(row.code));
        if (fresh.length) setAlerts((current) => [...fresh.slice(0, 5), ...current].slice(0, 8));
      } catch {
        // 單次即時行情失敗不關閉提醒；五秒後自動重試。
      } finally {
        running = false;
      }
    };

    void poll();
    const timer = createVisibilityGatedInterval(() => { void poll(); }, 5_000);
    return () => { stopped = true; timer.cancel(); };
  }, [config, stockMap]);

  if (!config || alerts.length === 0) return null;
  return <aside className="ma-live-alert" aria-live="assertive" aria-label="均線盤中即時訊號">
    <header><div><span>🔔 盤中即時偵測</span><strong>{config.mode === "river" ? `${config.riverFilter === "all" ? "全部估值" : config.riverFilter} × ${riverDistanceFilterLabels[config.riverDistanceFilter ?? "all"]} × ${maFilterLabels[config.maFilter]} × ${ma20BiasFilterLabels[config.ma20BiasFilter ?? "all"]}` : `${config.holderFilter === "all" ? "全部大戶" : config.holderFilter === "increase" ? "持股增加" : config.holderFilter === "decrease" ? "持股減少" : "持平"} × ${maFilterLabels[config.maFilter]}`}</strong></div><button type="button" onClick={() => setAlerts([])} aria-label="關閉均線即時訊號">×</button></header>
    <div>{alerts.map((alert) => <button type="button" key={`${alert.code}-${alert.detectedAt}`} onClick={() => onOpenStock(alert.code, alert.name)}><time>{alert.detectedAt}</time><strong>{alert.code}<small>{alert.name}</small><StockTradingBadges ticker={alert.code} compact /></strong><span><b>均線 {alert.score}/15</b><small>{alert.position}・{alert.detail}</small></span><em>{formatNumber(alert.price, 2)}</em></button>)}</div>
  </aside>;
}

function RiverChart({ candles }: { candles: Array<{ date: string; close: number }> }) {
  const model = useMemo(() => {
    const points = buildValuationRiverPoints(candles);
    const allValues = points.flatMap((point) => [point.close, point.base * 0.618, point.base * 1.382]);
    const minimum = Math.min(...allValues) * 0.94;
    const maximum = Math.max(...allValues) * 1.06;
    const width = 960;
    const height = 360;
    const pad = { left: 42, right: 62, top: 20, bottom: 42 };
    const x = (index: number) => pad.left + index / Math.max(1, points.length - 1) * (width - pad.left - pad.right);
    const y = (value: number) => pad.top + (maximum - value) / Math.max(1, maximum - minimum) * (height - pad.top - pad.bottom);
    const area = (lower: number, upper: number) => [
      ...points.map((point, index) => `${x(index)},${y(point.base * upper)}`),
      ...[...points].reverse().map((point, reverseIndex) => `${x(points.length - 1 - reverseIndex)},${y(point.base * lower)}`),
    ].join(" ");
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(index)} ${y(point.close)}`).join(" ");
    const baseLine = points.map((point, index) => `${index ? "L" : "M"}${x(index)} ${y(point.base)}`).join(" ");
    return { points, minimum, maximum, width, height, pad, x, y, area, line, baseLine };
  }, [candles]);

  if (model.points.length < 2) return <div className="research-loading">目前沒有足夠日 K 資料可繪製河流圖</div>;
  const labelIndexes = [0, .25, .5, .75, 1].map((ratio) => Math.round((model.points.length - 1) * ratio));
  const ticks = [0, .25, .5, .75, 1].map((ratio) => model.maximum - (model.maximum - model.minimum) * ratio);
  return (
    <div className="valuation-chart-scroll" role="region" aria-label="歷史價位位置均線圖" tabIndex={0}>
      <svg viewBox={`0 0 ${model.width} ${model.height}`} role="img" aria-label="股價與四層歷史價格位置河道">
        {ticks.map((tick) => <g key={tick}><line className="river-grid" x1={model.pad.left} x2={model.width - model.pad.right} y1={model.y(tick)} y2={model.y(tick)} /><text className="river-axis" x={model.width - model.pad.right + 8} y={model.y(tick) + 4}>{formatNumber(tick, tick >= 100 ? 0 : 1)}</text></g>)}
        <polygon className="river-band river-special" points={model.area(.618, .8)} />
        <polygon className="river-band river-cheap" points={model.area(.8, .9)} />
        <polygon className="river-band river-rich" points={model.area(.9, 1.2)} />
        <polygon className="river-band river-expensive" points={model.area(1.2, 1.382)} />
        <path className="river-base-line" d={model.baseLine} />
        <path className="river-price-line" d={model.line} />
        {labelIndexes.map((index) => <text className="river-date" key={`${model.points[index].date}-${index}`} x={model.x(index)} y={model.height - 13} textAnchor={index === 0 ? "start" : index === model.points.length - 1 ? "end" : "middle"}>{model.points[index].date.slice(2, 10)}</text>)}
      </svg>
    </div>
  );
}

export function ValuationRiverPanel(props: ResearchProps) {
  const { data, loading } = useResearch(props.ticker, props.market);
  const candles = data?.candles ?? [];
  const currentPrice = props.currentPrice ?? candles.at(-1)?.close ?? null;
  const waterline = candles.length ? valuationMedian(candles.slice(-VALUATION_RIVER_WINDOW).map((point) => point.close)) : null;
  const ratio = currentPrice !== null && waterline ? currentPrice / waterline : null;
  const position = ratio === null ? "等待資料" : ratio < .618 ? "跌破特價" : ratio < .8 ? "特價" : ratio < .9 ? "便宜" : ratio < 1.2 ? "偏貴" : ratio < 1.382 ? "昂貴" : "突破昂貴";
  const distance = ratio === null ? null : (ratio - 1) * 100;
  const levels = waterline ? [
    { label: "昂貴", range: `${formatNumber(waterline * 1.2, 1)}～${formatNumber(waterline * 1.382, 1)}`, className: "expensive" },
    { label: "偏貴", range: `${formatNumber(waterline * .9, 1)}～${formatNumber(waterline * 1.2, 1)}`, className: "rich" },
    { label: "便宜", range: `${formatNumber(waterline * .8, 1)}～${formatNumber(waterline * .9, 1)}`, className: "cheap" },
    { label: "特價", range: `${formatNumber(waterline * .618, 1)}～${formatNumber(waterline * .8, 1)}`, className: "special" },
  ] : [];
  const officialEps = data?.valuation?.eps ?? (currentPrice && data?.valuation?.pe ? currentPrice / data.valuation.pe : null);

  return (
    <article className="research-panel valuation-river-panel">
      <header className="research-panel-head">
        <div><span className="eyebrow">HISTORICAL PRICE POSITION × MA</span><h3>{props.ticker} {props.name}｜歷史價位位置均線圖</h3><StockTradingBadges ticker={props.ticker} /><p>以近 160 個交易日收盤價中位數作分水嶺；本價位分類不使用 EPS，至少低於分水嶺 10% 才列為便宜。</p></div>
        <strong className={distance !== null && distance < 0 ? "negative" : "positive"}>{position}{distance === null ? "" : `　距分水嶺 ${distance > 0 ? "+" : ""}${formatNumber(distance, 1)}%`}</strong>
      </header>
      {loading ? <div className="research-loading">正在整合日 K 與官方估值資料…</div> : (
        <>
          <div className="research-metric-grid">
            <span><b>現在股價</b><strong>{formatNumber(currentPrice, 2)}</strong><small>即時／最近收盤</small></span>
            <span><b>分水嶺</b><strong>{formatNumber(waterline, 2)}</strong><small>160 日動態中位價</small></span>
            <span><b>目前位置</b><strong>{position}</strong><small>四層價位區間</small></span>
            <span><b>官方本益比</b><strong>{formatNumber(data?.valuation?.pe, 2)}</strong><small>{data?.valuation?.dataDate || "最新官方資料"}</small></span>
            <span><b>每股盈餘</b><strong>{formatNumber(officialEps, 2)}</strong><small>{data?.valuation?.eps == null && officialEps !== null ? "依官方本益比反推" : "官方營益資料"}</small></span>
          </div>
          <RiverChart candles={candles} />
          <div className="river-legend"><span className="price">股價</span><span className="base">分水嶺</span><small>河道由下往上：特價／便宜／偏貴／昂貴</small></div>
          <div className="river-position-grid">
            <div className="river-ladder">{levels.map((level) => <div className={level.className} key={level.label}><strong>{level.label}</strong><span>{level.range}</span></div>)}</div>
            <div className="river-explain"><span>這檔現在</span><strong>{formatNumber(currentPrice, 2)}</strong><span>位置在</span><b>{position}</b><span>距分水嶺</span><em className={distance !== null && distance < 0 ? "negative" : "positive"}>{distance === null ? "—" : `${distance > 0 ? "+" : ""}${formatNumber(distance, 1)}%`}</em><small>區間每日收盤後重算，會隨公司價格結構變動。</small></div>
          </div>
        </>
      )}
      <footer><span>資料來源：TWSE／TPEx 公開資訊與 HanStock 日 K</span><button type="button" onClick={props.onOpenKline}>開啟五分鐘 K 線</button></footer>
    </article>
  );
}

export function InstitutionalHolderPanel(props: ResearchProps) {
  const { data, loading } = useResearch(props.ticker, props.market);
  const { payload: tdcc } = useTdccRadar();
  const [branch, setBranch] = useState<BranchPayload | null>(null);
  const [showZeroDirectors, setShowZeroDirectors] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/broker-branch-daily", { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<BranchPayload> : null)
      .then(setBranch)
      .catch(() => undefined);
    return () => controller.abort();
  }, [props.ticker]);
  const tdccRow = tdcc?.rows?.find((row) => row.code === props.ticker) ?? null;
  const branchRow = branch?.rows?.find((row) => row.ticker === props.ticker) ?? null;
  const fiveDayLots = props.flows.slice(0, 5).reduce((sum, point) => sum + point.foreign + point.trust + point.dealer + point.hedge, 0);
  const transfers = data?.transfers ?? [];
  const plannedShares = transfers.reduce((sum, row) => sum + (row.plannedShares ?? 0), 0);
  const plannedAmount = props.currentPrice === null ? null : plannedShares * props.currentPrice;
  const directors = data?.directors ?? [];
  const visibleDirectors = showZeroDirectors ? directors : directors.filter((row) => (row.shares ?? 0) > 0 || (row.relatedShares ?? 0) > 0 || (row.pledgedShares ?? 0) > 0);
  const hiddenZeroDirectors = directors.length - visibleDirectors.length;

  return (
    <article className="research-panel holder-panel">
      <header className="research-panel-head">
        <div><span className="eyebrow">INSTITUTIONAL HOLDER WATCH</span><h3>{props.ticker} {props.name}｜機構大戶持股追蹤</h3><StockTradingBadges ticker={props.ticker} /><p>法人、券商分點、TDCC 大戶與內部人申報放在同一張異動表。</p></div>
        <strong>{tdcc?.dataDate ? `大戶資料 ${tdcc.dataDate}` : "最新公開資料"}</strong>
      </header>
      <div className="research-metric-grid holder-metrics">
        <span><b>法人近五日淨買賣</b><strong className={fiveDayLots < 0 ? "negative" : "positive"}>{formatAmount(fiveDayLots)} 張</strong><small>外資＋投信＋自營</small></span>
        <span><b>券商分點淨額</b><strong className={(branchRow?.netAmount ?? 0) < 0 ? "negative" : "positive"}>{formatAmount(branchRow?.netAmount)}</strong><small>{branch?.tradeDate ?? "最新交易日"}・{branchRow ? `${branchRow.activeBranches} 分點` : "等待資料"}</small></span>
        <span><b>400 張以上大戶</b><strong>{tdccRow ? `${formatNumber(tdccRow.largeHolderPct, 1)}%` : "—"}</strong><small className={(tdccRow?.weeklyChangePp ?? 0) < 0 ? "negative" : "positive"}>週變化 {tdccRow?.weeklyChangePp == null ? "—" : `${tdccRow.weeklyChangePp > 0 ? "+" : ""}${formatNumber(tdccRow.weeklyChangePp, 1)}pp`}</small></span>
        <span><b>內部人預定轉讓</b><strong className={plannedShares > 0 ? "negative" : "neutral"}>{formatAmount(plannedAmount)}</strong><small>{formatShares(plannedShares)}・{transfers.length} 筆申報</small></span>
      </div>
      <h4>異動追蹤總表</h4>
      <div className="holder-table-scroll" role="region" aria-label="機構大戶異動追蹤總表" tabIndex={0}>
        <div className="holder-table holder-summary-table">
          <div className="holder-row is-head"><span>資料</span><span>目前數值</span><span>異動</span><span>資料日</span><span>判讀</span></div>
          <div className="holder-row"><strong>法人合計</strong><b>{formatAmount(fiveDayLots)} 張</b><em className={fiveDayLots < 0 ? "negative" : "positive"}>{fiveDayLots < 0 ? "偏賣" : "偏買"}</em><span>{props.flows[0]?.date ?? "—"}</span><i>{fiveDayLots < 0 ? "籌碼流出" : "籌碼流入"}</i></div>
          <div className="holder-row"><strong>券商分點</strong><b>{formatAmount(branchRow?.netAmount)}</b><em className={(branchRow?.netAmount ?? 0) < 0 ? "negative" : "positive"}>{branchRow ? `${formatNumber(branchRow.concentration, 1)}% 集中` : "待資料"}</em><span>{branch?.tradeDate ?? "—"}</span><i>{branchRow ? `${branchRow.activeBranches} 家活躍` : "尚無正式分點資料"}</i></div>
          <div className="holder-row"><strong>TDCC 大戶</strong><b>{tdccRow ? `${formatNumber(tdccRow.largeHolderPct, 1)}%` : "—"}</b><em className={(tdccRow?.weeklyChangePp ?? 0) < 0 ? "negative" : "positive"}>{tdccRow?.weeklyChangePp == null ? "—" : `${tdccRow.weeklyChangePp > 0 ? "+" : ""}${formatNumber(tdccRow.weeklyChangePp, 1)}pp`}</em><span>{tdcc?.dataDate ?? "—"}</span><i>{tdccRow?.weeklyChangePp == null ? "等待前週基準" : tdccRow.weeklyChangePp >= 0 ? "大戶持股增加" : "大戶持股減少"}</i></div>
          <div className="holder-row"><strong>內部人申報</strong><b>{formatShares(plannedShares)}</b><em className={plannedShares > 0 ? "negative" : "neutral"}>{transfers.length ? `${transfers.length} 筆轉讓` : "本期無申報"}</em><span>{transfers[0]?.reportDate || "—"}</span><i>{transfers.length ? "注意預定轉讓" : "未見預定轉讓"}</i></div>
        </div>
      </div>
      <h4>內部人預定轉讓明細</h4>
      <div className="holder-table-scroll" role="region" aria-label="內部人轉讓明細" tabIndex={0}>
        <div className="holder-table holder-insider-table">
          <div className="holder-row is-head"><span>身分／姓名</span><span>方式</span><span>預定轉讓</span><span>目前持有</span><span>轉讓後</span><span>期間</span></div>
          {loading && <div className="holder-table-empty">正在讀取公開資訊觀測站資料…</div>}
          {!loading && transfers.length === 0 && <div className="holder-table-empty">本期查無內部人預定轉讓申報</div>}
          {transfers.map((row) => <div className="holder-row" key={row.id}><strong>{row.identity || "內部人"}<small>{row.name || "—"}</small></strong><span>{row.method || "—"}</span><b className="negative">{formatShares(row.plannedShares)}</b><span>{formatShares(row.currentShares)}</span><span>{formatShares(row.afterShares)}</span><i>{row.period || row.reportDate || "—"}</i></div>)}
        </div>
      </div>
      <div className="director-section-title"><h4>董監與經理人持股</h4>{hiddenZeroDirectors > 0 || showZeroDirectors ? <button type="button" onClick={() => setShowZeroDirectors((current) => !current)}>{showZeroDirectors ? "隱藏零持股人員" : `顯示全部（另有 ${hiddenZeroDirectors} 筆零持股）`}</button> : null}</div>
      {directors.length > 0 && <p className="director-data-note">官方月報資料月份 {directors[0].dataMonth || "—"}・出表日期 {directors[0].reportDate || "—"}（非每日更新）</p>}
      <div className="holder-table-scroll" role="region" aria-label="董監與經理人持股" tabIndex={0}>
        <div className="holder-table holder-director-table">
          <div className="holder-row is-head"><span>身分／姓名</span><span>本人／關係人持股</span><span>設質比率</span><span>設質股數</span><span>資料月份／出表</span></div>
          {!loading && directors.length === 0 && <div className="holder-table-empty">目前官方資料未提供可顯示的董監持股明細</div>}
          {visibleDirectors.map((row) => <div className="holder-row" key={row.id}><strong>{row.identity || "董監事"}<small>{row.name || "—"}</small></strong><b>{formatShares(row.shares)}<small>關係人 {formatShares(row.relatedShares)}</small></b><span>{row.pledgeRatio === null ? "—" : `${formatNumber(row.pledgeRatio, 2)}%`}</span><span>{formatShares(row.pledgedShares)}</span><i>{row.dataMonth || "—"}<small>出表 {row.reportDate || "—"}</small></i></div>)}
        </div>
      </div>
      <footer><span>資料研究用途，不構成投資建議；內部人欄為預定轉讓申報，不等同實際成交。</span><button type="button" onClick={props.onOpenKline}>開啟五分鐘 K 線</button></footer>
    </article>
  );
}
