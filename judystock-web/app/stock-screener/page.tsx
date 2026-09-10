"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { DEFAULT_BATTLE_SETTINGS, type BattleRuntimeSettings } from "../../lib/battle-settings";
import { averageOfficialFlow, calculateCombinedChipScore, calculateOfficialChipScore, type MarketRankingSeriesPoint } from "../../lib/chip-scoring";
import StrategyWorkbench from "./StrategyWorkbench";

type RankingRow = { code: string; name: string; market: "twse" | "tpex" | "etf"; exchange: "twse" | "tpex"; series: MarketRankingSeriesPoint[] };
type Quote = { code: string; price: number | null; changePct: number | null };
type MarketRankingPayload = { ok?: boolean; fetchedAt?: string; dataDate?: string; rows?: RankingRow[]; snapshotFallback?: boolean; coverage?: { tradingDays?: number } };
type TechnicalMarketPayload = { ok?: boolean; updatedAt?: string; dataDate?: string; rows?: Array<{ code: string; close: number; changePct: number }> };
export type ResultRow = {
  code: string;
  name: string;
  group: string;
  market: "上市" | "上櫃" | "ETF";
  exchange: "twse" | "tpex";
  price: number | null;
  changePct: number | null;
  today: number;
  threeDay: number;
  fiveDay: number;
  surge: number;
  strongDays: number;
  trustDays: number;
  combined: number;
  judgement: string;
};

const SCREENER_WARM_CACHE_KEY = "hanstock-screener-warm-cache-v1";
type ScreenerWarmCache = { savedAt: number; dataDate: string; quoteDataDate: string; updatedAt: string; rows: ResultRow[] };

function readScreenerWarmCache(): ScreenerWarmCache | null {
  try {
    const raw = window.localStorage.getItem(SCREENER_WARM_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ScreenerWarmCache;
    return Array.isArray(parsed?.rows) ? parsed : null;
  } catch {
    return null;
  }
}

function writeScreenerWarmCache(cache: ScreenerWarmCache) {
  try {
    window.localStorage.setItem(SCREENER_WARM_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // 儲存空間不可用（無痕模式、配額已滿）時放棄快取即可，不影響畫面顯示。
  }
}

function newestTimestamp(...values: Array<string | undefined | null>): string {
  let best = "";
  for (const value of values) {
    if (value && value > best) best = value;
  }
  return best || "—";
}

const SCREENER_REFRESH_CLAIM_KEY = "hanstock-screener-refresh-claim-v1";
const SCREENER_REFRESH_CLAIM_INTERVAL_MS = 60_000;

function claimBackgroundRefresh(): boolean {
  try {
    const now = Date.now();
    const last = Number(window.localStorage.getItem(SCREENER_REFRESH_CLAIM_KEY) ?? "0");
    if (now - last < SCREENER_REFRESH_CLAIM_INTERVAL_MS) return false;
    window.localStorage.setItem(SCREENER_REFRESH_CLAIM_KEY, String(now));
    return true;
  } catch {
    return true;
  }
}

async function latestMarketRanking(signal: AbortSignal, previous: MarketRankingPayload): Promise<MarketRankingPayload> {
  const response = await fetch("/api/market-ranking", { cache: "no-store", signal });
  const payload = await response.json() as MarketRankingPayload;
  if (!payload.rows?.length) return previous;
  if (previous.fetchedAt && payload.fetchedAt && payload.fetchedAt <= previous.fetchedAt) return previous;
  return payload;
}

function formatUpdateTime(value: string) {
  if (!value || value === "—") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function judgement(today: number, fiveDay: number, combined: number): string {
  if (today >= 60 && fiveDay >= 60) return "多方雙強";
  if (today <= -60 && fiveDay <= -60) return "空方雙弱";
  if (combined >= 60) return "偏多";
  if (combined <= -60) return "偏空";
  return "中性";
}

export default function StockScreenerPage() {
  const [rankingRows, setRankingRows] = useState<RankingRow[]>([]);
  const [cachedRows, setCachedRows] = useState<ResultRow[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [groups, setGroups] = useState<Array<{ name: string; codes: string[] }>>([]);
  const [weights, setWeights] = useState<BattleRuntimeSettings["chipWeights"]>(DEFAULT_BATTLE_SETTINGS.chipWeights);
  const [dataDate, setDataDate] = useState("—");
  const [quoteDataDate, setQuoteDataDate] = useState("—");
  const [updatedAt, setUpdatedAt] = useState("—");
  const [dataStatus, setDataStatus] = useState<"更新中" | "正式最新資料" | "舊資料備援" | "更新失敗">("更新中");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const warm = readScreenerWarmCache();
      if (warm) {
        setCachedRows(warm.rows);
        setDataDate(warm.dataDate);
        setQuoteDataDate(warm.quoteDataDate);
        setUpdatedAt(warm.updatedAt);
        setDataStatus("舊資料備援");
        setLoading(false);
      } else {
        setLoading(true);
        setDataStatus("更新中");
      }
      try {
        const [snapshotResponse, groupResponse, settingsResponse] = await Promise.all([
          fetch("/api/market-ranking", { cache: "default", signal: controller.signal }),
          fetch("/api/group-chip-members", { cache: "default", signal: controller.signal }),
          fetch("/api/runtime-settings", { cache: "default", signal: controller.signal }),
        ]);
        const snapshot = await snapshotResponse.json() as MarketRankingPayload;
        const groupPayload = await groupResponse.json() as { groups?: Array<{ name: string; codes: string[] }> };
        const settingsPayload = await settingsResponse.json() as { settings?: BattleRuntimeSettings };
        if (snapshot.rows?.length) {
          setRankingRows(snapshot.rows);
          setDataDate(snapshot.dataDate ?? "—");
        }
        setGroups(Array.isArray(groupPayload.groups) ? groupPayload.groups : []);
        if (settingsPayload.settings?.chipWeights) setWeights(settingsPayload.settings.chipWeights);
        const nextDataDate = snapshot.dataDate ?? "—";
        const nextQuoteDate = warm?.quoteDataDate ?? "—";
        setDataDate(nextDataDate);
        setUpdatedAt(newestTimestamp(snapshot.fetchedAt, warm?.updatedAt));
        const sameLatestDay = nextQuoteDate !== "—" && nextDataDate !== "—" && nextQuoteDate === nextDataDate;
        const completeChipHistory = (snapshot.coverage?.tradingDays ?? 0) >= 6;
        setDataStatus(completeChipHistory && sameLatestDay ? "正式最新資料" : "舊資料備援");
        setLoading(false);

        if (claimBackgroundRefresh()) {
          void latestMarketRanking(controller.signal, snapshot).then((ranking) => {
            if (controller.signal.aborted || !ranking.rows?.length || ranking === snapshot) return;
            setRankingRows(ranking.rows);
            setDataDate(ranking.dataDate ?? nextDataDate);
            setUpdatedAt(newestTimestamp(ranking.fetchedAt));
          }).catch(() => undefined);
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) setDataStatus(warm ? "舊資料備援" : "更新失敗");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    return () => controller.abort();
  }, []);

  const groupByCode = useMemo(() => {
    const map = new Map<string, string>();
    groups.forEach((item) => item.codes.forEach((code) => { if (!map.has(code)) map.set(code, item.name); }));
    return map;
  }, [groups]);

  const cachedRowByCode = useMemo(() => new Map(cachedRows.map((row) => [row.code, row])), [cachedRows]);

  const allRows = useMemo<ResultRow[]>(() => rankingRows.length ? rankingRows.map((row) => {
    const today = calculateOfficialChipScore(averageOfficialFlow(row.series, 1), weights);
    const threeDay = calculateOfficialChipScore(averageOfficialFlow(row.series, 3), weights);
    const fiveDay = calculateOfficialChipScore(averageOfficialFlow(row.series, 5), weights);
    const previousDay = row.series[1]
      ? calculateOfficialChipScore(averageOfficialFlow(row.series.slice(1), 1), weights)
      : today;
    const surge = Math.round((today - previousDay) * 10) / 10;
    const dailyScores = row.series.map((point) => calculateOfficialChipScore(point, weights));
    const strongDays = dailyScores.findIndex((score) => score < 10);
    const trustDays = row.series.findIndex((point) => point.trust < 10);
    const combined = calculateCombinedChipScore(today, fiveDay);
    const quote = quotes[row.code];
    const cached = cachedRowByCode.get(row.code);
    return {
      code: row.code, name: row.name,
      group: row.market === "etf" ? "ETF" : groupByCode.get(row.code) ?? "未分類",
      market: row.market === "twse" ? "上市" : row.market === "tpex" ? "上櫃" : "ETF",
      exchange: row.exchange, price: quote?.price ?? cached?.price ?? null, changePct: quote?.changePct ?? cached?.changePct ?? null,
      today,
      threeDay,
      fiveDay,
      surge,
      strongDays: strongDays < 0 ? dailyScores.length : strongDays,
      trustDays: trustDays < 0 ? row.series.length : trustDays,
      combined,
      judgement: judgement(today, fiveDay, combined),
    };
  }) : cachedRows, [rankingRows, quotes, groupByCode, weights, cachedRows, cachedRowByCode]);

  useEffect(() => {
    if (!rankingRows.length || !allRows.length || dataDate === "—") return;
    writeScreenerWarmCache({ savedAt: Date.now(), dataDate, quoteDataDate, updatedAt, rows: allRows });
  }, [rankingRows.length, allRows, dataDate, quoteDataDate, updatedAt]);

  return (
    <main className="screener-shell">
      <header className="screener-topbar">
        <nav className="screener-header-links" aria-label="選股程式快速連結">
          <Link href="/" aria-label="返回盤中戰鬥版">← <span>返回盤中戰鬥版</span></Link>
          <Link href="/education?category=screening" aria-label="查看選股策略教學"><b>?</b><span>策略教學</span></Link>
        </nav>
        <div><span>HANSTOCK STOCK SCREENER</span><h1>HanStock 選股程式｜策略工坊</h1><p>只保留策略工坊；其他選股頁籤與對應功能已移除。</p></div>
        <aside className={`screener-data-state ${dataStatus === "正式最新資料" ? "ready" : "waiting"}`}><b>籌碼資料日 {dataDate}</b><small>行情資料日 {quoteDataDate}</small><small>最新更新 {formatUpdateTime(updatedAt)}</small><em>{dataStatus}｜全市場 {allRows.length.toLocaleString("zh-TW")} 檔</em></aside>
      </header>
      <StrategyWorkbench rows={allRows} loading={loading} dataDate={dataDate} quoteDataDate={quoteDataDate} updatedAt={updatedAt} />
    </main>
  );
}
