"use client";

import { useEffect, useMemo, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import StockTradingBadges from "./StockTradingBadges";
import WeeklyChipResearch from "./WeeklyChipResearch";
import type { ChipWeights, MarketRankingSeriesPoint } from "../lib/chip-scoring";
import {
  buildWeeklyGroupComparisons,
  buildWeeklyStockComparisons,
  selectWeeklyTop,
  summarizeWeeklyPerformance,
  type WeeklyChipComparisonRow,
  type WeeklyChipDirection,
  type WeeklyChipMarket,
  type WeeklyChipPeriod,
} from "../lib/weekly-chip-ranking";
import { rankTdccWeeklyChanges, type TdccWeeklyRow } from "../lib/tdcc-weekly-score";
import { rankBrokerBranchWeekly, type BrokerBranchWeeklyRow } from "../lib/broker-branch-weekly-score";
import { assessWeeklyMainForce, buildWeeklyMainForceRows } from "../lib/weekly-main-force-score";

type MarketRankingRow = { code: string; name: string; market: "twse" | "tpex" | "etf"; series: MarketRankingSeriesPoint[] };
type Props = {
  rows: MarketRankingRow[];
  groups: Array<{ name: string; codes: string[] }>;
  groupByTicker: Map<string, string>;
  weights: ChipWeights;
  loading: boolean;
  updatedAt: string | null;
  onRefresh: () => void;
  onOpenStock: (code: string, name: string) => void;
  onOpenGroup: (name: string) => void;
};
type PerformanceRow = { ticker: string; startDate: string; endDate: string; startClose: number | null; endClose: number | null; returnPct: number | null };
type ArchiveSummary = {
  weekEndDate: string;
  comparedWeekEndDate: string | null;
  stockIncreaseAverage: number | null;
  stockDecreaseAverage: number | null;
  strongestStock: string;
  weakestStock: string;
  strongestGroup: string;
  weakestGroup: string;
  stockCount: number;
  groupCount: number;
  savedAt: number;
};
type SavedArchiveItem = {
  key: string;
  code: string | null;
  name: string;
  groupName: string;
  market: string;
  score: number;
  increaseRank: number | null;
  decreaseRank: number | null;
};
type SavedArchive = {
  weekEndDate: string;
  weights?: Record<string, number>;
  comparedWeekEndDate: string | null;
  stocks: SavedArchiveItem[];
  groups: SavedArchiveItem[];
};
type WeeklyMainForceSavedRow = {
  weekEndDate: string;
  ticker: string;
  institutionalScore: number;
  brokerBranchScore: number;
  tdccLargeHolderScore: number;
  compositeScore: number;
  label: string;
};

function signed(value: number, digits = 1, suffix = "") { return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`; }
function tone(value: number | null) { return value === null || value === 0 ? "neutral" : value > 0 ? "positive" : "negative"; }
function formatPrice(value: number | null) { return value === null ? "待回補" : value.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function formatUpdateTime(value: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(parsed);
}
function ScoreCell({ value }: { value: number }) { return <strong className={tone(value)}>{signed(value)}</strong>; }
function WeeklyDeltaCell({ value }: { value: number | null }) { return <strong className={tone(value)}>{value === null ? "待資料" : `${signed(value)}分`}</strong>; }
function archiveItems(rows: WeeklyChipComparisonRow[], period: WeeklyChipPeriod) {
  return rows.map((row) => ({
    key: row.key,
    code: row.code,
    name: row.name,
    groupName: row.groupName,
    market: row.market,
    score: period === "current" ? row.currentScore : row.previousScore,
    increaseRank: period === "current" ? row.currentIncreaseRank : row.previousIncreaseRank,
    decreaseRank: period === "current" ? row.currentDecreaseRank : row.previousDecreaseRank,
  }));
}

function restoreArchiveComparisons(current: SavedArchive | undefined, previous: SavedArchive | undefined, kind: "stocks" | "groups") {
  if (!current || !previous || current.comparedWeekEndDate !== previous.weekEndDate) return [] as WeeklyChipComparisonRow[];
  const previousByKey = new Map(previous[kind].map((item) => [item.key, item] as const));
  return current[kind].flatMap((item): WeeklyChipComparisonRow[] => {
    const old = previousByKey.get(item.key);
    if (!old) return [];
    return [{
      key: item.key,
      code: item.code,
      name: item.name,
      groupName: item.groupName,
      market: (kind === "groups" ? "族群" : item.market) as WeeklyChipComparisonRow["market"],
      coveredCount: 1,
      currentEndDate: current.weekEndDate,
      previousEndDate: previous.weekEndDate,
      currentScore: item.score,
      previousScore: old.score,
      twoWeeksAgoScore: null,
      scoreChange: Math.round((item.score - old.score) * 10) / 10,
      previousScoreChange: null,
      currentIncreaseRank: item.increaseRank,
      previousIncreaseRank: old.increaseRank,
      currentDecreaseRank: item.decreaseRank,
      previousDecreaseRank: old.decreaseRank,
    }];
  });
}

export function WeeklyChipAnalysisPanel({ rows, groups, groupByTicker, weights, loading, updatedAt, onRefresh, onOpenStock, onOpenGroup }: Props) {
  const [reportTab, setReportTab] = useState('overview');
  const [performanceRows, setPerformanceRows] = useState<PerformanceRow[]>([]);
  const [performanceLoading, setPerformanceLoading] = useState(false);
  const [history, setHistory] = useState<ArchiveSummary[]>([]);
  const [savedArchives, setSavedArchives] = useState<SavedArchive[]>([]);
  const [archiveLoading, setArchiveLoading] = useState(true);
  const [archiveError, setArchiveError] = useState(false);
  const [archiveRetry, setArchiveRetry] = useState(0);
  const [tdccRows, setTdccRows] = useState<TdccWeeklyRow[]>([]);
  const [brokerRows, setBrokerRows] = useState<BrokerBranchWeeklyRow[]>([]);
  const [savedMainForceRows, setSavedMainForceRows] = useState<WeeklyMainForceSavedRow[]>([]);
  const stockInputs = useMemo(() => rows.map((row) => ({
    code: row.code,
    name: row.name,
    market: (row.market === "twse" ? "上市" : row.market === "tpex" ? "上櫃" : "ETF") as WeeklyChipMarket,
    groupName: groupByTicker.get(row.code) ?? (row.market === "etf" ? "ETF" : "未分類"),
    series: row.series,
  })), [rows, groupByTicker]);
  const liveStockComparisons = useMemo(() => buildWeeklyStockComparisons(stockInputs, weights), [stockInputs, weights]);
  const liveGroupComparisons = useMemo(() => buildWeeklyGroupComparisons(liveStockComparisons, groups), [liveStockComparisons, groups]);
  const archivedStockComparisons = useMemo(() => restoreArchiveComparisons(savedArchives[0], savedArchives[1], "stocks"), [savedArchives]);
  const archivedGroupComparisons = useMemo(() => restoreArchiveComparisons(savedArchives[0], savedArchives[1], "groups"), [savedArchives]);
  const stockComparisons = liveStockComparisons.length ? liveStockComparisons : archivedStockComparisons;
  const groupComparisons = liveGroupComparisons.length ? liveGroupComparisons : archivedGroupComparisons;
  const sample = stockComparisons[0] ?? groupComparisons[0] ?? null;
  const currentIncrease = useMemo(() => selectWeeklyTop(stockComparisons, "current", "increase", 20), [stockComparisons]);
  const currentDecrease = useMemo(() => selectWeeklyTop(stockComparisons, "current", "decrease", 20), [stockComparisons]);
  const previousIncrease = useMemo(() => selectWeeklyTop(stockComparisons, "previous", "increase", 20), [stockComparisons]);
  const previousDecrease = useMemo(() => selectWeeklyTop(stockComparisons, "previous", "decrease", 20), [stockComparisons]);
  const currentGroupIncrease = useMemo(() => selectWeeklyTop(groupComparisons, "current", "increase", 20), [groupComparisons]);
  const currentGroupDecrease = useMemo(() => selectWeeklyTop(groupComparisons, "current", "decrease", 20), [groupComparisons]);
  const previousGroupIncrease = useMemo(() => selectWeeklyTop(groupComparisons, "previous", "increase", 20), [groupComparisons]);
  const previousGroupDecrease = useMemo(() => selectWeeklyTop(groupComparisons, "previous", "decrease", 20), [groupComparisons]);

  useEffect(() => {
    const controller = new AbortController();
    setArchiveLoading(true);
    setArchiveError(false);
    fetch("/api/weekly-chip-history?archives=1&limit=12", { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ archives?: SavedArchive[]; history?: ArchiveSummary[] }> : Promise.reject(new Error("weekly_archive_load_failed")))
      .then((payload) => {
        if (Array.isArray(payload.archives)) setSavedArchives(payload.archives);
        if (Array.isArray(payload.history)) setHistory(payload.history);
      })
      .catch(() => { if (!controller.signal.aborted) setArchiveError(true); })
      .finally(() => { if (!controller.signal.aborted) setArchiveLoading(false); });
    return () => controller.abort();
  }, [archiveRetry]);

  const researchArchives = useMemo(() => {
    const merged = new Map(savedArchives.map(archive => [archive.weekEndDate, archive]));
    const live = liveStockComparisons[0];
    if (live) {
      // Saved, completed weeks remain authoritative for historical research.
      if (!merged.has(live.previousEndDate)) merged.set(live.previousEndDate, { weekEndDate: live.previousEndDate, comparedWeekEndDate: null, weights, stocks: archiveItems(liveStockComparisons, "previous"), groups: [] });
      if (!merged.has(live.currentEndDate)) merged.set(live.currentEndDate, { weekEndDate: live.currentEndDate, comparedWeekEndDate: live.previousEndDate, weights, stocks: archiveItems(liveStockComparisons, "current"), groups: [] });
    }
    return [...merged.values()];
  }, [savedArchives, liveStockComparisons, weights]);

  useEffect(() => {
    if (!sample) return;
    const tickers = [...new Set([...previousIncrease, ...previousDecrease].flatMap((row) => row.code ? [row.code] : []))];
    if (!tickers.length) return;
    const controller = new AbortController();
    setPerformanceLoading(true);
    const params = new URLSearchParams({ items: tickers.join(","), start: sample.previousEndDate, end: sample.currentEndDate });
    fetch(`/api/weekly-chip-performance?${params}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]) })
      .then((response) => response.ok ? response.json() as Promise<{ rows?: PerformanceRow[] }> : Promise.reject(new Error("weekly_performance_failed")))
      .then((payload) => setPerformanceRows(Array.isArray(payload.rows) ? payload.rows : []))
      .catch((error) => { if (!(error instanceof DOMException && error.name === "AbortError")) setPerformanceRows([]); })
      .finally(() => { if (!controller.signal.aborted) setPerformanceLoading(false); });
    return () => controller.abort();
  }, [sample?.currentEndDate, sample?.previousEndDate, previousIncrease, previousDecrease]);

  useEffect(() => {
    const liveSample = liveStockComparisons[0] ?? liveGroupComparisons[0] ?? null;
    if (!liveSample || !liveStockComparisons.length) return;
    const controller = new AbortController();
    const archives = [
      { weekEndDate: liveSample.previousEndDate, comparedWeekEndDate: null, weights, stocks: archiveItems(liveStockComparisons, "previous"), groups: archiveItems(liveGroupComparisons, "previous") },
      { weekEndDate: liveSample.currentEndDate, comparedWeekEndDate: liveSample.previousEndDate, weights, stocks: archiveItems(liveStockComparisons, "current"), groups: archiveItems(liveGroupComparisons, "current") },
    ];
    fetch("/api/weekly-chip-history", { method: "POST", cache: "no-store", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archives }) })
      .then((response) => response.ok ? response.json() as Promise<{ history?: ArchiveSummary[] }> : Promise.reject(new Error("weekly_archive_failed")))
      .then((payload) => setHistory(Array.isArray(payload.history) ? payload.history : []))
      .catch((error) => { if (!(error instanceof DOMException && error.name === "AbortError")) setHistory([]); });
    return () => controller.abort();
  }, [liveStockComparisons, liveGroupComparisons, weights]);

  useEffect(() => {
    let controller: AbortController | null = null;
    const loadSavedMainForce = () => {
      controller?.abort();
      controller = new AbortController();
      fetch("/api/weekly-main-force-history", { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() as Promise<{ rows?: WeeklyMainForceSavedRow[] }> : null)
        .then((saved) => { if (Array.isArray(saved?.rows)) setSavedMainForceRows(saved.rows); })
        .catch(() => undefined);
    };
    loadSavedMainForce();
    const timer = createVisibilityGatedInterval(() => { void loadSavedMainForce(); }, 30_000);
    return () => { controller?.abort(); timer.cancel(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch("/api/tdcc-radar", { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<{ rows?: TdccWeeklyRow[] }> : null).catch(() => null),
      fetch("/api/broker-branch-weekly", { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<{ rows?: BrokerBranchWeeklyRow[] }> : null).catch(() => null),
    ]).then(([tdcc, broker]) => {
      setTdccRows(Array.isArray(tdcc?.rows) ? tdcc.rows : []);
      setBrokerRows(Array.isArray(broker?.rows) ? broker.rows : []);
    });
    return () => controller.abort();
  }, []);
  const tdccScores = useMemo(() => {
    const scores = new Map(savedMainForceRows.map((row) => [row.ticker, row.tdccLargeHolderScore] as const));
    rankTdccWeeklyChanges(tdccRows).forEach((value, ticker) => scores.set(ticker, value));
    return scores;
  }, [tdccRows, savedMainForceRows]);
  const brokerScores = useMemo(() => {
    const scores = new Map(savedMainForceRows.map((row) => [row.ticker, row.brokerBranchScore] as const));
    rankBrokerBranchWeekly(brokerRows).forEach((value, ticker) => scores.set(ticker, value));
    return scores;
  }, [brokerRows, savedMainForceRows]);
  const mainForceRows = useMemo(() => buildWeeklyMainForceRows(
    new Map(stockComparisons.flatMap((row) => row.code ? [[row.code, row.currentScore] as const] : [])),
    tdccScores,
    brokerScores,
  ), [stockComparisons, tdccScores, brokerScores]);
  const readyMainForceCount = useMemo(() => mainForceRows.filter((row) => row.score !== null).length, [mainForceRows]);

  useEffect(() => {
    if (!sample?.currentEndDate) return;
    const records = mainForceRows.flatMap((row) => row.score === null || row.institutional === null || row.brokerBranch === null || row.tdccLargeHolder === null ? [] : [{
      ticker: row.ticker,
      institutional: row.institutional,
      brokerBranch: row.brokerBranch,
      tdccLargeHolder: row.tdccLargeHolder,
    }]);
    if (!records.length) return;
    const controller = new AbortController();
    fetch("/api/weekly-main-force-history", {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weekEndDate: sample.currentEndDate, rows: records }),
    }).catch(() => undefined);
    return () => controller.abort();
  }, [sample?.currentEndDate, mainForceRows]);


  const performanceByTicker = useMemo(() => new Map(performanceRows.map((row) => [row.ticker, row])), [performanceRows]);
  const strongPerformance = useMemo(() => summarizeWeeklyPerformance(previousIncrease.map((row) => ({ ticker: row.code ?? row.key, returnPct: performanceByTicker.get(row.code ?? "")?.returnPct ?? null })), "increase"), [previousIncrease, performanceByTicker]);
  const weakPerformance = useMemo(() => summarizeWeeklyPerformance(previousDecrease.map((row) => ({ ticker: row.code ?? row.key, returnPct: performanceByTicker.get(row.code ?? "")?.returnPct ?? null })), "decrease"), [previousDecrease, performanceByTicker]);

  const renderPerformanceRows = (items: WeeklyChipComparisonRow[], direction: WeeklyChipDirection) => items.map((row, index) => {
    const performance = row.code ? performanceByTicker.get(row.code) : null;
    return <div className="weekly-performance-row" key={`${direction}-${row.key}`}>
      <span className="weekly-chip-rank">{index + 1}</span>
      <button type="button" className="weekly-chip-code" onClick={() => row.code && onOpenStock(row.code, row.name)}>{row.code}</button>
      <button type="button" className="weekly-chip-stock-name" onClick={() => row.code && onOpenStock(row.code, row.name)}><strong>{row.name}</strong>{row.code && <StockTradingBadges ticker={row.code} compact />}<i>›</i></button>
      <span className="weekly-chip-group">{row.groupName}</span><span>{formatPrice(performance?.startClose ?? null)}</span><span>{formatPrice(performance?.endClose ?? null)}</span>
      <strong className={tone(performance?.returnPct ?? null)}>{performance?.returnPct === null || performance?.returnPct === undefined ? (performanceLoading ? "計算中" : "待回補") : signed(performance.returnPct, 2, "%")}</strong><ScoreCell value={row.previousScore} />
    </div>;
  });
  const renderCurrentRows = (items: WeeklyChipComparisonRow[], direction: WeeklyChipDirection) => items.map((row, index) => {
    const brokerScore = row.code ? brokerScores.get(row.code) ?? null : null;
    const tdccScore = row.code ? tdccScores.get(row.code) ?? null : null;
    const assessment = assessWeeklyMainForce({ institutional: row.currentScore, brokerBranch: brokerScore, tdccLargeHolder: tdccScore });
    return <div className="weekly-current-row" key={`${direction}-${row.key}`}>
      <span className="weekly-chip-rank">{index + 1}</span><button type="button" className="weekly-chip-code" onClick={() => row.code && onOpenStock(row.code, row.name)}>{row.code}</button><button type="button" className="weekly-chip-stock-name" onClick={() => row.code && onOpenStock(row.code, row.name)}><strong>{row.name}</strong>{row.code && <StockTradingBadges ticker={row.code} compact />}<i>›</i></button><span className="weekly-chip-group">{row.groupName}</span><ScoreCell value={row.previousScore} /><strong className={tone(row.scoreChange)}>{signed(row.scoreChange)}</strong><ScoreCell value={row.currentScore} /><span className={tone(brokerScore)} title="分點主力週分數">分點 {brokerScore === null ? "待" : signed(brokerScore)}</span><span className={tone(tdccScore)} title="集保大戶週分數">集保 {tdccScore === null ? "待" : signed(tdccScore)}</span><strong className={tone(assessment.score)} title={assessment.label}>{assessment.score === null ? "待三項" : signed(assessment.score)}</strong>
    </div>;
  });
  const renderDirection = (direction: WeeklyChipDirection) => {
    const previous = direction === "increase" ? previousIncrease : previousDecrease;
    const current = direction === "increase" ? currentIncrease : currentDecrease;
    const label = direction === "increase" ? "增加" : "減少";
    return <section className={`weekly-direct-section is-${direction}`} key={direction}>
      <header><div><span>{direction === "increase" ? "STRONG PERFORMANCE" : "WEAK PERFORMANCE"}</span><h3>籌碼{label}：上週成效＋本週名單</h3></div><p>左邊追蹤上週入選20大至本週五收盤；右邊直接列出本週新20大。</p></header>
      <div className="weekly-direct-pair">
        <article className={`weekly-chip-side weekly-chip-side--previous is-${direction}`}><header><div><span>{sample?.previousEndDate ?? "上週"} 入選</span><h4>上週{label}20大　本週成效</h4></div><b>{previous.length} 檔</b></header><div className="weekly-chip-table-scroll" tabIndex={0}><div className="weekly-performance-table"><div className="weekly-performance-head"><span>排行</span><span>代號</span><span>名稱</span><span>族群</span><span>上週收盤</span><span>本週收盤</span><span>漲跌幅</span><span>上週分數</span></div>{renderPerformanceRows(previous, direction)}</div></div></article>
        <article className={`weekly-chip-side weekly-chip-side--current is-${direction}`}><header><div><span>{sample?.currentEndDate ?? "本週"} 結算</span><h4>本週{label}20大　新名單</h4></div><b>{current.length} 檔</b></header><div className="weekly-chip-table-scroll" tabIndex={0}><div className="weekly-current-table"><div className="weekly-current-head"><span>排行</span><span>代號</span><span>名稱</span><span>族群</span><span>上週分數</span><span>增減</span><span>法人週分</span><span>分點週分</span><span>集保週分</span><span>三項綜合</span></div>{renderCurrentRows(current, direction)}</div></div></article>
      </div>
    </section>;
  };
  const renderGroupComparison = (direction: WeeklyChipDirection) => {
    const previous = direction === "increase" ? previousGroupIncrease : previousGroupDecrease;
    const current = direction === "increase" ? currentGroupIncrease : currentGroupDecrease;
    return <article className={`weekly-group-compare is-${direction}`} key={direction}><header><h3>族群籌碼{direction === "increase" ? "增加" : "減少"}前20</h3><span>上週排行｜本週排行</span></header><div className="weekly-chip-table-scroll" tabIndex={0}><div className="weekly-group-table"><div className="weekly-group-head"><span>上週名次</span><span>上週族群</span><span>上上週分數</span><span>上週分數</span><span>上週增減</span><span className="weekly-group-current-start">本週名次</span><span>本週族群</span><span>上週分數</span><span>本週分數</span><span>本週增減</span></div>{Array.from({ length: 20 }, (_, index) => { const oldRow = previous[index]; const newRow = current[index]; return <div className="weekly-group-row" key={`${direction}-${index}`}><span className="weekly-group-rank">{oldRow ? index + 1 : "—"}</span><button className="weekly-group-name" type="button" onClick={() => oldRow && onOpenGroup(oldRow.name)}>{oldRow?.name ?? "—"}</button><strong className={oldRow ? tone(oldRow.twoWeeksAgoScore) : "neutral"}>{oldRow?.twoWeeksAgoScore === null || oldRow?.twoWeeksAgoScore === undefined ? "待資料" : signed(oldRow.twoWeeksAgoScore)}</strong><strong className={oldRow ? tone(oldRow.previousScore) : "neutral"}>{oldRow ? signed(oldRow.previousScore) : "—"}</strong>{oldRow ? <WeeklyDeltaCell value={oldRow.previousScoreChange} /> : <strong className="neutral">—</strong>}<span className="weekly-group-rank weekly-group-current-start">{newRow ? index + 1 : "—"}</span><button className="weekly-group-name" type="button" onClick={() => newRow && onOpenGroup(newRow.name)}>{newRow?.name ?? "—"}</button><strong className={newRow ? tone(newRow.previousScore) : "neutral"}>{newRow ? signed(newRow.previousScore) : "—"}</strong><strong className={newRow ? tone(newRow.currentScore) : "neutral"}>{newRow ? signed(newRow.currentScore) : "—"}</strong>{newRow ? <WeeklyDeltaCell value={newRow.scoreChange} /> : <strong className="neutral">—</strong>}</div>; })}</div></div></article>;
  };

  return <section className="weekly-chip-console" aria-label="每週籌碼分析">
    <header className="weekly-chip-head"><div><span className="eyebrow">WEEKLY CHIP CONTINUITY</span><h2>每週籌碼分析</h2><p>依主題切換週報分頁，歷史追蹤保留完整比較。每逢星期五收盤定稿，追蹤上週20大至本週五的股價成效，並把每個週次永久保存。</p></div><aside><b>本週結算 {sample?.currentEndDate ?? "等待完整資料"}</b><small>上週結算 {sample?.previousEndDate ?? "—"}</small><small>最新更新 {formatUpdateTime(updatedAt)}</small><button type="button" onClick={onRefresh} disabled={loading}>{loading ? "更新中" : "立即更新"}</button></aside></header>
    <WeeklyChipResearch onTabChange={setReportTab} archives={researchArchives} loading={archiveLoading} error={archiveError} onRetry={() => setArchiveRetry(value => value + 1)} onOpenStock={onOpenStock} />
    <div hidden={reportTab !== "history"}><h3>最新結算週完整明細</h3><p>以下為最新結算週與上週的固定比較；上方研究報告可切換歷史週次。</p>
    <section className="weekly-performance-summary" aria-label="上週選股本週績效摘要"><article><span>上週籌碼增加20大</span><strong className={tone(strongPerformance.averageReturnPct)}>{strongPerformance.averageReturnPct === null ? (performanceLoading ? "計算中" : "待回補") : signed(strongPerformance.averageReturnPct, 2, "%")}</strong><small>平均漲跌幅｜上漲命中率 {strongPerformance.hitRate === null ? "—" : `${strongPerformance.hitRate.toFixed(1)}%`}</small></article><article><span>上週籌碼減少20大</span><strong className={tone(weakPerformance.averageReturnPct)}>{weakPerformance.averageReturnPct === null ? (performanceLoading ? "計算中" : "待回補") : signed(weakPerformance.averageReturnPct, 2, "%")}</strong><small>平均漲跌幅｜下跌命中率 {weakPerformance.hitRate === null ? "—" : `${weakPerformance.hitRate.toFixed(1)}%`}</small></article><article><span>績效期間</span><strong>{sample ? `${sample.previousEndDate} → ${sample.currentEndDate}` : "—"}</strong><small>星期五收盤價對星期五收盤價</small></article><article><span>永久歷史</span><strong>{history.length} 週</strong><small>每週五自動新增，不覆蓋舊週次</small></article></section>
    <section className="weekly-performance-summary" aria-label="每週主力綜合籌碼資料狀態"><article><span>法人週分數</span><strong>{sample ? `${stockComparisons.length} 檔` : "待資料"}</strong><small>本週五日法人平均｜35%</small></article><article><span>分點主力週分數</span><strong className={brokerScores.size ? "positive" : "neutral"}>{brokerScores.size ? `${brokerScores.size} 檔` : "待資料"}</strong><small>週淨額＋方向化集中度｜40%</small></article><article><span>集保大戶週分數</span><strong className={tdccScores.size ? "positive" : "neutral"}>{tdccScores.size ? `${tdccScores.size} 檔` : "待兩期"}</strong><small>大戶持股集中度變化｜25%</small></article><article><span>每週主力綜合</span><strong className={readyMainForceCount ? "positive" : "neutral"}>{readyMainForceCount ? `${readyMainForceCount} 檔完成` : "待三項齊全"}</strong><small>法人35%／分點40%／集保25%</small></article></section>
    <section className="weekly-score-method" aria-label="法人分點集保週分名詞與計分方式"><header><div><span>WEEKLY SCORE GUIDE</span><h3>法人、分點、集保週分怎麼看</h3></div><p>三項都先換算成市場相對的 -100～+100 分；正分代表籌碼偏多，負分代表籌碼偏空。</p></header><div><article><b>法人週分</b><p>本週最近五個交易日的外資、投信、自營商與避險分數先平均，再依戰鬥版法人權重加權。</p><small>五日法人平均｜綜合占 35%</small></article><article><b>分點週分</b><p>全市場週淨買賣額排名占 65%，再加上跟隨買賣方向的分點集中度排名 35%。</p><small>週淨額 65%＋方向化集中度 35%｜綜合占 40%</small></article><article><b>集保週分</b><p>集保持股分級 15（100 萬股以上）占比，本週減上週後，再依全市場變化排名換算。</p><small>大戶持股比週增減｜綜合占 25%</small></article><article><b>三項綜合</b><p>法人週分 × 35%＋分點週分 × 40%＋集保週分 × 25%。</p><small>三項必須齊全；缺一項顯示「待三項」，不以 0 分代替。</small></article></div></section>
    {stockComparisons.length ? <>{(["increase", "decrease"] as WeeklyChipDirection[]).map(renderDirection)}<section className="weekly-groups-all"><header><div><span>GROUP WEEKLY COMPARISON</span><h2>族群每週籌碼強弱</h2></div><p>族群增加與減少各前20名，上週與本週直接並列。</p></header><div className="weekly-group-grid">{(["increase", "decrease"] as WeeklyChipDirection[]).map(renderGroupComparison)}</div></section><section className="weekly-history-section"><header><div><span>PERMANENT WEEKLY ARCHIVE</span><h2>歷史週次強弱變化</h2></div><p>資料保存在網站資料庫，重新整理、換裝置或隔週都不會消失。</p></header><div className="weekly-chip-table-scroll" tabIndex={0}><div className="weekly-history-table"><div className="weekly-history-head"><span>週結算日</span><span>增加20平均分</span><span>減少20平均分</span><span>最強個股</span><span>最弱個股</span><span>最強族群</span><span>最弱族群</span></div>{history.map((week) => <div className="weekly-history-row" key={week.weekEndDate}><strong>{week.weekEndDate}</strong><b className={tone(week.stockIncreaseAverage)}>{week.stockIncreaseAverage === null ? "—" : signed(week.stockIncreaseAverage)}</b><b className={tone(week.stockDecreaseAverage)}>{week.stockDecreaseAverage === null ? "—" : signed(week.stockDecreaseAverage)}</b><span>{week.strongestStock}</span><span>{week.weakestStock}</span><span>{week.strongestGroup}</span><span>{week.weakestGroup}</span></div>)}</div></div>{!history.length && <div className="weekly-chip-empty"><strong>正在建立歷史週次</strong><span>本週與上週會先建立，之後每週五自動累加。</span></div>}</section></> : <div className="weekly-chip-empty"><strong>{loading ? "正在整理完整週資料…" : "等待兩個完整星期的籌碼資料"}</strong><span>取得後會直接顯示上週成效、本週名單、族群排行與歷史週次。</span></div>}
    </div>
  </section>;
}
