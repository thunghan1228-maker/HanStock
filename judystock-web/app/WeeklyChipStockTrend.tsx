"use client";

import { useEffect, useMemo, useState } from "react";
import type { ChipWeights, MarketRankingSeriesPoint } from "../lib/chip-scoring";
import { buildWeeklyStockComparisons, weeklyScoreChangePercent, type WeeklyChipMarket } from "../lib/weekly-chip-ranking";
import StockTradingBadges from "./StockTradingBadges";

type MarketRankingRow = { code: string; name: string; market: "twse" | "tpex" | "etf"; series: MarketRankingSeriesPoint[] };
type TickerHistoryPoint = { weekEndDate: string; score: number; increaseRank: number | null; decreaseRank: number | null };

type Props = {
  query: string;
  rows: MarketRankingRow[];
  groupByTicker: Map<string, string>;
  weights: ChipWeights;
  loading: boolean;
  onOpenStock: (code: string, name: string) => void;
};

function signed(value: number, digits = 1, suffix = "") { return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`; }
function tone(value: number | null) { return value === null || value === 0 ? "neutral" : value > 0 ? "positive" : "negative"; }
function ScoreCell({ value }: { value: number }) { return <strong className={tone(value)}>{signed(value)}</strong>; }

/** 共用個股搜尋：法人籌碼與每週五日平均由同一個代號／名稱驅動。 */
export function WeeklyChipStockTrend({ query, rows, groupByTicker, weights, loading, onOpenStock }: Props) {
  const [tickerHistory, setTickerHistory] = useState<TickerHistoryPoint[]>([]);
  const comparisons = useMemo(() => buildWeeklyStockComparisons(rows.map((row) => ({
    code: row.code,
    name: row.name,
    market: (row.market === "twse" ? "上市" : row.market === "tpex" ? "上櫃" : "ETF") as WeeklyChipMarket,
    groupName: groupByTicker.get(row.code) ?? (row.market === "etf" ? "ETF" : "未分類"),
    series: row.series,
  })), weights), [rows, groupByTicker, weights]);
  const result = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return null;
    return comparisons.find((row) => row.code === normalized || row.name.toLowerCase().includes(normalized)) ?? null;
  }, [comparisons, query]);

  useEffect(() => {
    if (!result?.code) { setTickerHistory([]); return; }
    const controller = new AbortController();
    fetch(`/api/weekly-chip-history?ticker=${encodeURIComponent(result.code)}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ history?: TickerHistoryPoint[] }> : Promise.reject(new Error("ticker_weekly_history_failed")))
      .then((payload) => setTickerHistory(Array.isArray(payload.history) ? payload.history : []))
      .catch((error) => { if (!(error instanceof DOMException && error.name === "AbortError")) setTickerHistory([]); });
    return () => controller.abort();
  }, [result?.code]);

  if (!query.trim()) return null;
  return <section className="weekly-chip-search-card stock-analysis-weekly-trend" aria-label="個股歷週五日平均趨勢">
    <header><span>個股歷週五日平均籌碼趨勢</span><small>與上方同一檔股票同步查詢</small></header>
    {loading && !comparisons.length ? <p>正在讀取每週五日平均籌碼資料…</p> : result ? <>
      <div className="weekly-stock-trend-result"><button type="button" onClick={() => onOpenStock(result.code ?? "", result.name)}><b>{result.code}</b><strong>{result.name}</strong><span>{result.groupName}｜{result.market}</span><StockTradingBadges ticker={result.code ?? ""} compact /></button><article><small>上週五日平均</small><ScoreCell value={result.previousScore} /><em>{result.previousEndDate}</em></article><i className={tone(result.scoreChange)}>→</i><article><small>本週五日平均</small><ScoreCell value={result.currentScore} /><em>{result.currentEndDate}</em></article><article className="weekly-stock-change"><small>較上週變化</small><strong className={tone(result.scoreChange)}>{signed(result.scoreChange)} 分</strong><b className={tone(result.scoreChange)}>{weeklyScoreChangePercent(result.currentScore, result.previousScore) === null ? "上週為0分" : `${signed(weeklyScoreChangePercent(result.currentScore, result.previousScore) ?? 0, 2)}%`}</b></article></div>
      {tickerHistory.length > 0 && <div className="weekly-ticker-history"><strong>歷週分數</strong>{tickerHistory.slice(0, 16).map((point) => <span key={point.weekEndDate}><small>{point.weekEndDate}</small><b className={tone(point.score)}>{signed(point.score)}</b><em>{point.increaseRank ? `增第${point.increaseRank}` : point.decreaseRank ? `減第${point.decreaseRank}` : "未入前20"}</em></span>)}</div>}
    </> : <p className="weekly-chip-search-empty">目前沒有這檔股票的兩週完整五日平均資料。</p>}
  </section>;
}
