"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import StockTradingBadges from "./StockTradingBadges";

type SignalKind = "all-time-high" | "rolling-12-high" | "all-time-low" | "rolling-12-low";

type RevenueSignal = {
  id: number;
  revenueMonth: string;
  stockCode: string;
  name: string;
  market: string;
  signalKind: SignalKind;
  revenue: number;
  previousMonthRevenue: number | null;
  previousYearRevenue: number | null;
  momPct: number | null;
  yoyPct: number | null;
  comparisonRevenue: number;
  comparisonMonth: string;
  historyMonths: number;
  sourcePublishedDate: string;
  firstObservedAt: number;
  updatedAt: number;
};

type RevenueRankingRow = {
  revenueMonth: string;
  stockCode: string;
  name: string;
  market: string;
  groupName: string;
  revenue: number;
  previousMonthRevenue: number | null;
  previousYearRevenue: number | null;
  momPct: number | null;
  yoyPct: number | null;
  sourcePublishedDate: string | null;
  closePrice: number | null;
  volumeLots: number | null;
  priceDate: string | null;
};

type RevenueGroupRow = {
  name: string;
  medianYoY: number;
  positiveCount: number;
  totalPublished: number;
  totalMembers: number;
  totalReported: number;
  stocks: RevenueRankingRow[];
};

type RevenuePayload = {
  ok: boolean;
  stale: boolean;
  error: string | null;
  historyStart: string;
  baseline: { historyStart: string; historyEnd: string; stockCount: number; sourceRows: number };
  targetRevenueMonth: string;
  checkedAt: number | null;
  completedAt: number | null;
  sourcePublishedDate: string | null;
  coverage: { total: number; twse: number; tpex: number };
  summary: { allTimeHigh: number; rolling12High: number; allTimeLow: number; rolling12Low: number };
  signals: RevenueSignal[];
  rankings: {
    availableMonths: string[];
    rows: RevenueRankingRow[];
    groupSizes: Record<string, number>;
  };
  refreshIntervalMinutes: number;
};

type LiveQuote = {
  code: string;
  exchange: "twse" | "tpex";
  price: number | null;
  change: number | null;
  changePct: number | null;
  quoteTime?: string | null;
  mode?: "live" | "preopen-trial" | "close-fallback";
};

const QUOTE_REFRESH_MS = 10_000;
const QUOTE_BATCH_SIZE = 50;

const kindMeta: Record<SignalKind, { label: string; short: string; tone: "high" | "low" }> = {
  "all-time-high": { label: "歷史單月新高", short: "歷史新高", tone: "high" },
  "rolling-12-high": { label: "近 12 月新高", short: "12 月新高", tone: "high" },
  "all-time-low": { label: "歷史單月新低", short: "歷史新低", tone: "low" },
  "rolling-12-low": { label: "近 12 月新低", short: "12 月新低", tone: "low" },
};

function formatRevenue(value: number) {
  const amount = value * 1_000;
  if (Math.abs(amount) >= 100_000_000) return `${(amount / 100_000_000).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 億`;
  if (Math.abs(amount) >= 10_000) return `${(amount / 10_000).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 萬`;
  return amount.toLocaleString("zh-TW");
}

function formatPct(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPrice(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-TW", {
    minimumFractionDigits: value % 1 === 0 ? 0 : 1,
    maximumFractionDigits: 2,
  });
}

function formatChange(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${formatPrice(value)} 元`;
}

function quoteTone(value: number | null | undefined) {
  return (value ?? 0) > 0 ? "positive" : (value ?? 0) < 0 ? "negative" : "neutral";
}

function quoteSourceLabel(quote: LiveQuote | undefined) {
  if (!quote) return "行情讀取中";
  if (quote.mode === "preopen-trial") return "盤前試撮";
  if (quote.mode === "close-fallback") return "最近交易";
  return "即時行情";
}

function formatDateTime(value: number | null) {
  if (!value) return "尚未完成";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function median(values: number[]) {
  const sorted = [...values].sort((left, right) => left - right);
  if (!sorted.length) return 0;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export default function MonthlyRevenueRecords({ active, onOpenStock }: { active: boolean; onOpenStock(ticker: string, name: string): void }) {
  const [payload, setPayload] = useState<RevenuePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | SignalKind>("all");
  const [quotesByCode, setQuotesByCode] = useState<Record<string, LiveQuote>>({});
  const [view, setView] = useState<"growth" | "groups" | "records">("growth");
  const [selectedMonth, setSelectedMonth] = useState("");
  const [marketFilter, setMarketFilter] = useState<"all" | "twse" | "tpex">("all");
  const [groupFilter, setGroupFilter] = useState("all");
  const [minimumYoY, setMinimumYoY] = useState(30);
  const [displayCount, setDisplayCount] = useState(100);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copying" | "copied" | "error">("idle");

  useEffect(() => {
    if (copyStatus !== "copied" && copyStatus !== "error") return;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 2_000);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  const load = useCallback(async (refresh = false) => {
    try {
      const response = await fetch(`/api/monthly-revenue-records${refresh ? "?refresh=1" : ""}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(60_000),
      });
      const next = await response.json() as RevenuePayload;
      if (!response.ok && !next.signals?.length) throw new Error(next.error || `HTTP ${response.status}`);
      setPayload(next);
      setRequestError(next.stale ? next.error : null);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "月營收資料暫時無法載入");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(true);
    const timer = createVisibilityGatedInterval(() => { void load(true); }, 5 * 60 * 1_000);
    return () => { timer.cancel(); };
  }, [load]);

  useEffect(() => {
    if (!selectedMonth && payload?.rankings.availableMonths[0]) setSelectedMonth(payload.rankings.availableMonths[0]);
  }, [payload, selectedMonth]);

  const quoteItems = useMemo(() => {
    const unique = new Map<string, string>();
    for (const row of payload?.signals ?? []) {
      if (row.market !== "twse" && row.market !== "tpex") continue;
      unique.set(row.stockCode, `${row.market}:${row.stockCode}`);
    }
    return [...unique.values()];
  }, [payload]);

  const loadQuotes = useCallback(async () => {
    if (!quoteItems.length) return;
    const batches = Array.from({ length: Math.ceil(quoteItems.length / QUOTE_BATCH_SIZE) }, (_, index) =>
      quoteItems.slice(index * QUOTE_BATCH_SIZE, index * QUOTE_BATCH_SIZE + QUOTE_BATCH_SIZE),
    );
    const settled = await Promise.allSettled(batches.map(async (batch) => {
      const response = await fetch(`/api/quotes?items=${encodeURIComponent(batch.join(","))}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as { mode?: LiveQuote["mode"]; quotes?: LiveQuote[] };
      return (data.quotes ?? []).map((quote) => ({ ...quote, mode: data.mode }));
    }));
    const rows = settled.flatMap((result) => result.status === "fulfilled" ? result.value : []);
    if (!rows.length) return;
    setQuotesByCode((current) => {
      const next = { ...current };
      rows.forEach((row) => { next[row.code] = row; });
      return next;
    });
  }, [quoteItems]);

  useEffect(() => {
    void loadQuotes();
    const timer = createVisibilityGatedInterval(() => { void loadQuotes(); }, QUOTE_REFRESH_MS);
    return () => { timer.cancel(); };
  }, [loadQuotes]);

  const filteredSignals = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return (payload?.signals ?? []).filter((signal) => {
      if (kindFilter !== "all" && signal.signalKind !== kindFilter) return false;
      if (!keyword) return true;
      return signal.stockCode.toLowerCase().includes(keyword) || signal.name.toLowerCase().includes(keyword);
    });
  }, [kindFilter, payload, query]);

  const monthRows = useMemo(() => (payload?.rankings.rows ?? []).filter((row) => row.revenueMonth === selectedMonth), [payload, selectedMonth]);
  const groupOptions = useMemo(() => [...new Set(monthRows.map((row) => row.groupName).filter((name) => name !== "未分類"))].sort((a, b) => a.localeCompare(b, "zh-Hant")), [monthRows]);
  const filteredRankingRows = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return monthRows
      .filter((row) => marketFilter === "all" || row.market === marketFilter)
      .filter((row) => groupFilter === "all" || row.groupName === groupFilter)
      .filter((row) => row.yoyPct !== null && row.yoyPct >= minimumYoY)
      .filter((row) => !keyword || row.stockCode.toLowerCase().includes(keyword) || row.name.toLowerCase().includes(keyword))
      .sort((left, right) => (right.yoyPct ?? Number.NEGATIVE_INFINITY) - (left.yoyPct ?? Number.NEGATIVE_INFINITY))
      .slice(0, displayCount);
  }, [displayCount, groupFilter, marketFilter, minimumYoY, monthRows, query]);
  const copyRankingCodes = async () => {
    if (!filteredRankingRows.length) return;
    setCopyStatus("copying");
    try {
      await navigator.clipboard.writeText([...new Set(filteredRankingRows.map((row) => row.stockCode))].join(","));
      setCopyStatus("copied");
    } catch {
      setCopyStatus("error");
    }
  };
  const revenueGroups = useMemo(() => {
    const grouped = new Map<string, RevenueRankingRow[]>();
    monthRows.filter((row) => row.groupName !== "未分類" && (marketFilter === "all" || row.market === marketFilter)).forEach((row) => {
      grouped.set(row.groupName, [...(grouped.get(row.groupName) ?? []), row]);
    });
    return [...grouped.entries()].flatMap(([name, stocks]) => {
      const withGrowth = stocks.filter((row) => row.yoyPct !== null);
      if (withGrowth.length < 3) return [];
      const medianYoY = median(withGrowth.map((row) => row.yoyPct ?? 0));
      if (medianYoY < 0) return [];
      return [{
        name,
        medianYoY,
        positiveCount: withGrowth.filter((row) => (row.yoyPct ?? 0) > 0).length,
        totalPublished: withGrowth.length,
        totalMembers: payload?.rankings.groupSizes[name] ?? withGrowth.length,
        totalReported: monthRows.filter((row) => row.groupName === name).length,
        stocks: withGrowth.sort((left, right) => (right.yoyPct ?? 0) - (left.yoyPct ?? 0)),
      } satisfies RevenueGroupRow];
    }).filter((group) => groupFilter === "all" || group.name === groupFilter)
      .filter((group) => !query.trim() || group.name.includes(query.trim()) || group.stocks.some((row) => row.stockCode.includes(query.trim()) || row.name.includes(query.trim())))
      .sort((left, right) => right.medianYoY - left.medianYoY);
  }, [groupFilter, marketFilter, monthRows, payload, query]);

  return <>
    {active && <section className="revenue-record-panel" aria-labelledby="revenue-record-title">
      <header className="revenue-record-head">
        <div>
          <span className="eyebrow">OFFICIAL MONTHLY REVENUE ANALYSIS</span>
          <h2 id="revenue-record-title">每月營收分析</h2>
          <p>全市場年增排行、67 族群營收強弱與歷史營收高低點，一頁完成。</p>
        </div>
      </header>

      <div className="revenue-main-tabs" role="tablist" aria-label="每月營收分析功能">
        <button type="button" role="tab" aria-selected={view === "growth"} className={view === "growth" ? "active" : ""} onClick={() => setView("growth")}>📈 每月營收成長榜</button>
        <button type="button" role="tab" aria-selected={view === "groups"} className={view === "groups" ? "active" : ""} onClick={() => setView("groups")}>🧭 族群檢視</button>
        <button type="button" role="tab" aria-selected={view === "records"} className={view === "records" ? "active" : ""} onClick={() => setView("records")}>🔔 創新高／新低</button>
      </div>

      <div className="revenue-status-strip">
        <span><b>{payload?.targetRevenueMonth.replace("-", "/") ?? "—"}</b> 營收申報</span>
        <span>已公布 <b>{payload?.coverage.total ?? 0}</b> 檔（上市 {payload?.coverage.twse ?? 0}／上櫃 {payload?.coverage.tpex ?? 0}）</span>
        <span>官方資料日 <b>{payload?.sourcePublishedDate?.replaceAll("-", "/") ?? "—"}</b></span>
        <span>完成更新 <b>{formatDateTime(payload?.completedAt ?? null)}</b></span>
        <button type="button" onClick={() => { setLoading(true); void load(true); }} disabled={loading}>{loading ? "更新中…" : "立即更新"}</button>
      </div>

      {view !== "records" && <div className="revenue-ranking-tools">
        <label><span>月份</span><select value={selectedMonth} onChange={(event) => setSelectedMonth(event.target.value)}>{payload?.rankings.availableMonths.map((month) => <option value={month} key={month}>{month.replace("-", "/")} 營收</option>)}</select></label>
        <label><span>市場</span><select value={marketFilter} onChange={(event) => setMarketFilter(event.target.value as "all" | "twse" | "tpex")}><option value="all">全部</option><option value="twse">上市</option><option value="tpex">上櫃</option></select></label>
        <label><span>族群</span><select value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}><option value="all">全部67族群</option>{groupOptions.map((group) => <option value={group} key={group}>{group}</option>)}</select></label>
        {view === "growth" && <label><span>年增門檻</span><select value={minimumYoY} onChange={(event) => setMinimumYoY(Number(event.target.value))}><option value={0}>年增為正</option><option value={10}>≥ 10%</option><option value={30}>≥ 30%</option><option value={50}>≥ 50%</option><option value={100}>≥ 100%</option></select></label>}
        {view === "growth" && <label><span>顯示</span><select value={displayCount} onChange={(event) => setDisplayCount(Number(event.target.value))}><option value={50}>50檔</option><option value={100}>100檔</option><option value={300}>300檔</option></select></label>}
        <label className="revenue-ranking-search"><span>搜尋</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={view === "groups" ? "族群、代號或名稱" : "股票代號或名稱"} /></label>
      </div>}

      {view === "growth" && <div className="revenue-growth-board">
        <div className="revenue-growth-summary"><span><b>{selectedMonth.replace("-", "/") || "—"}</b> 營收年增排行</span><div className="revenue-copy-tools"><strong>{filteredRankingRows.length} 檔符合</strong><button type="button" className="revenue-copy-button" disabled={!filteredRankingRows.length || copyStatus === "copying"} onClick={() => void copyRankingCodes()}>{copyStatus === "copied" ? "已複製" : copyStatus === "copying" ? "複製中…" : "複製名單"}</button><span role="status">{copyStatus === "error" ? "複製失敗，請允許剪貼簿存取後重試。" : copyStatus === "copied" ? "股票代號已複製，以逗號分隔。" : ""}</span></div></div>
        <p className="revenue-price-note">收盤與成交量採最近一次官方交易日快照，日期標示於各列；切換營收月份不會改成當月歷史股價。成交量單位：張。</p>
        <div className="revenue-growth-table" role="region" aria-label="每月營收成長榜" tabIndex={0}>
          <div className="revenue-growth-row is-head"><span>排名</span><span>股票</span><span>族群</span><span>年增率</span><span>月增率</span><span>單月營收</span><span>收盤</span><span>成交量（張）</span><span>公布日</span></div>
          {filteredRankingRows.map((row, index) => <article className="revenue-growth-row" key={`${row.revenueMonth}:${row.stockCode}`}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <span className="revenue-growth-stock"><button type="button" onClick={() => onOpenStock(row.stockCode, row.name)}>{row.stockCode}</button><button type="button" onClick={() => onOpenStock(row.stockCode, row.name)}>{row.name}</button><small>{row.market === "twse" ? "上市" : "上櫃"}</small></span>
            <span>{row.groupName}</span><strong className={(row.yoyPct ?? 0) >= 0 ? "positive" : "negative"}>{formatPct(row.yoyPct)}</strong><b className={(row.momPct ?? 0) >= 0 ? "positive" : "negative"}>{formatPct(row.momPct)}</b><span>{formatRevenue(row.revenue)}</span><span className="revenue-close-cell"><b>{formatPrice(row.closePrice ?? null)}</b><small>{row.priceDate?.replaceAll("-", "/") ?? "資料待補"}</small></span><span>{row.volumeLots !== null && Number.isFinite(row.volumeLots) ? row.volumeLots.toLocaleString("zh-TW", { maximumFractionDigits: 3 }) : "—"}</span><span>{row.sourcePublishedDate?.replaceAll("-", "/") ?? "歷史完整月"}</span>
          </article>)}
          {!loading && !filteredRankingRows.length && <div className="revenue-history-empty">目前沒有符合條件的月營收個股。</div>}
        </div>
      </div>}

      {view === "groups" && <div className="revenue-group-board">
        <div className="revenue-growth-summary"><span><b>{selectedMonth.replace("-", "/") || "—"}</b> 族群年增中位數排行</span><strong>{revenueGroups.length} 個族群</strong></div>
        <div className="revenue-group-grid">
          {revenueGroups.map((group, index) => <article className="revenue-group-card" key={group.name}>
            <header><em>{index + 1}</em><div><h3>{group.name}</h3><small>已公布 {group.totalPublished}／名單 {group.totalMembers} 檔・{group.positiveCount}/{group.totalPublished} 年增為正</small></div><strong>{formatPct(group.medianYoY)}<small>年增中位</small></strong></header>
            <div>{group.stocks.map((row) => <button type="button" key={row.stockCode} onClick={() => onOpenStock(row.stockCode, row.name)}><span><b>{row.stockCode}</b><strong>{row.name}</strong></span><em className={(row.yoyPct ?? 0) >= 0 ? "positive" : "negative"}>{formatPct(row.yoyPct)}</em><small>{row.sourcePublishedDate?.slice(5).replace("-", "/") ?? "完整月"}</small></button>)}</div>
            <p className="revenue-group-summary">{group.name}在目前篩選中排名第 {index + 1}；{group.totalPublished} 檔可比較年增，年增中位數 {formatPct(group.medianYoY)}，其中 {group.positiveCount} 檔為正。成長榜上榜 {filteredRankingRows.filter((row) => row.groupName === group.name).length} 檔{filteredRankingRows.some((row) => row.groupName === group.name) ? `（${filteredRankingRows.filter((row) => row.groupName === group.name).slice(0, 3).map((row) => row.name).join("、")}${filteredRankingRows.filter((row) => row.groupName === group.name).length > 3 ? "等" : ""}）` : ""}。完整族群名單 {group.totalMembers} 檔中，{Math.max(0, group.totalMembers - group.totalReported)} 檔尚無本月營收資料。</p>
          </article>)}
          {!loading && !revenueGroups.length && <div className="revenue-history-empty">目前沒有至少3檔已公布且年增中位數為正的族群。</div>}
        </div>
      </div>}

      {view === "records" && <><div className="revenue-summary-grid" aria-label="營收高低點統計">
        <button type="button" className={kindFilter === "all-time-high" ? "active is-high" : "is-high"} onClick={() => setKindFilter(kindFilter === "all-time-high" ? "all" : "all-time-high")}><span>歷史單月新高</span><strong>{payload?.summary.allTimeHigh ?? 0}</strong></button>
        <button type="button" className={kindFilter === "rolling-12-high" ? "active is-high" : "is-high"} onClick={() => setKindFilter(kindFilter === "rolling-12-high" ? "all" : "rolling-12-high")}><span>近 12 月新高</span><strong>{payload?.summary.rolling12High ?? 0}</strong></button>
        <button type="button" className={kindFilter === "all-time-low" ? "active is-low" : "is-low"} onClick={() => setKindFilter(kindFilter === "all-time-low" ? "all" : "all-time-low")}><span>歷史單月新低</span><strong>{payload?.summary.allTimeLow ?? 0}</strong></button>
        <button type="button" className={kindFilter === "rolling-12-low" ? "active is-low" : "is-low"} onClick={() => setKindFilter(kindFilter === "rolling-12-low" ? "all" : "rolling-12-low")}><span>近 12 月新低</span><strong>{payload?.summary.rolling12Low ?? 0}</strong></button>
      </div>

      <div className="revenue-history-tools">
        <label><span>搜尋歷史</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="輸入代號或名稱" /></label>
        <button type="button" className={kindFilter === "all" ? "active" : ""} onClick={() => setKindFilter("all")}>全部訊號</button>
        <small>收錄自 {payload?.historyStart.replaceAll("-", "/") ?? "2026/08/26"}；官方基準 {payload?.baseline.historyStart.replace("-", "/") ?? "2011/01"}～{payload?.baseline.historyEnd.replace("-", "/") ?? "2026/07"}</small>
      </div>

      {requestError && <div className="revenue-data-warning">本次官方更新暫時失敗，畫面保留已存歷史：{requestError}</div>}

      <div className="revenue-history-table" role="region" aria-label="月營收高低點歷史" tabIndex={0}>
        <div className="revenue-history-row is-head"><span>公布／月份</span><span>股票／交易資格</span><span>漲跌幅</span><span>漲跌</span><span>成交價</span><span>訊號</span><span>單月營收</span><span>月增／年增</span><span>突破基準</span></div>
        {filteredSignals.map((signal) => {
          const meta = kindMeta[signal.signalKind];
          const quote = quotesByCode[signal.stockCode];
          const tone = quoteTone(quote?.changePct ?? quote?.change);
          return <article className={`revenue-history-row is-${meta.tone}`} key={signal.id}>
            <span><b>{signal.sourcePublishedDate.replaceAll("-", "/")}</b><small>{signal.revenueMonth.replace("-", "/")} 營收</small></span>
            <span className="revenue-stock-cell"><span className="revenue-stock-links"><button type="button" onClick={() => onOpenStock(signal.stockCode, signal.name)} aria-label={`開啟 ${signal.stockCode} ${signal.name} K 線圖`}>{signal.stockCode}</button><button type="button" onClick={() => onOpenStock(signal.stockCode, signal.name)}>{signal.name}</button></span><small className="revenue-stock-market">{signal.market === "twse" ? "上市" : "上櫃"}</small><StockTradingBadges ticker={signal.stockCode} compact detailed /></span>
            <span className={`revenue-live-quote ${tone}`}><b>{formatPct(quote?.changePct ?? null)}</b><small>{quoteSourceLabel(quote)}</small></span>
            <span className={`revenue-live-quote ${tone}`}><b>{formatChange(quote?.change ?? null)}</b><small>即時漲跌</small></span>
            <span className={`revenue-live-quote ${tone}`}><b>{formatPrice(quote?.price ?? null)}</b><small>成交價</small></span>
            <span><em>{meta.label}</em><small>官方歷史 {signal.historyMonths} 月</small></span>
            <span><b>{formatRevenue(signal.revenue)}</b><small>單位：新台幣</small></span>
            <span><b className={(signal.momPct ?? 0) >= 0 ? "positive" : "negative"}>{formatPct(signal.momPct)}</b><small className={(signal.yoyPct ?? 0) >= 0 ? "positive" : "negative"}>{formatPct(signal.yoyPct)}</small></span>
            <span><b>{formatRevenue(signal.comparisonRevenue)}</b><small>{signal.comparisonMonth.replace("-", "/")}</small></span>
          </article>;
        })}
        {!loading && filteredSignals.length === 0 && <div className="revenue-history-empty">目前沒有符合搜尋條件的公告；新資料公布後會自動加入並保留。</div>}
        {loading && !payload && <div className="revenue-history-empty">正在讀取官方月營收與歷史紀錄…</div>}
      </div>
      <footer className="revenue-record-foot">歷史新高／低以公開資訊觀測站自 2011/01 可回溯資料判定。</footer></>}
    </section>}

  </>;
}
