"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import { FUNDAMENTAL_RIVER_BOUNDS, fundamentalRiverPosition, type FundamentalPosition, type FundamentalRiverPoint } from "../lib/fundamental-river";
import StockTradingBadges from "./StockTradingBadges";

type StockSelection = { code: string; name: string; market: "twse" | "tpex" | "etf" };
type ReportRow = { ticker: string; name: string; market: string; groupName: string; reportPeriod: string; singleQuarterEps: number | null; position: FundamentalPosition | null; close: number | null };
type ScreenerRow = { ticker: string; name: string; market: "twse" | "tpex"; groupName: string; maScore: number; close: number; changePct: number | null; position: FundamentalPosition; distancePct: number };
type SummaryPayload = { ok?: boolean; dataDate?: string; reportWeek?: string; updatedAt?: string; recentReports?: ReportRow[]; screener?: ScreenerRow[] };
type LiveRankRow = { rank: number; name: string; change: string; score: number; lead: string; leadChange: string };
type LiveQuote = { key: string; price: number | null; change: number | null; changePct: number | null; session?: string };
type RiverRankingRow = { ticker: string; name: string; groupName: string; maScore: number | null; close: number | null; waterline: number | null; position: FundamentalPosition | null; distancePct: number | null };
type StrongRow = RiverRankingRow & { rank: number; price: number | null; change: number | null; changePct: number | null };
type LiveRankingPayload = {
  ok?: boolean;
  fetchedAt?: string;
  sourceDate?: string;
  liveData?: boolean;
  priceType?: string;
  rankings?: { stocks?: { strong?: LiveRankRow[] } };
  quotes?: LiveQuote[];
};
type Quarter = { date: string; effectiveDate: string; label: string; eps: number; ttmEps: number | null; operatingMargin: number | null };
type Revenue = { year: number; month: number; period: string; revenue: number; yoyPct: number | null };
type DetailPayload = SummaryPayload & {
  ticker: string;
  name: string;
  market: "twse" | "tpex";
  groupName: string;
  currentPrice: number | null;
  pe: number | null;
  pb: number | null;
  bvps: number | null;
  ttmEps: number | null;
  groupMedianPe: number | null;
  groupMedianPb: number | null;
  peerSampleSize: number;
  valuationBasis: "pe" | "pb" | null;
  waterline: number | null;
  position: FundamentalPosition | null;
  distancePct: number | null;
  maScore: number | null;
  quarters: Quarter[];
  revenues: { recent: Revenue[]; ytdYoYPct: number | null };
  riverPoints: FundamentalRiverPoint[];
};

type Props = {
  stock: StockSelection | null;
  livePrice: number | null;
  onSelect: (ticker: string) => void;
  onOpenKline: (ticker: string, name: string) => void;
};

function formatNumber(value: number | null | undefined, digits = 1) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
}

function signed(value: number | null | undefined, digits = 1, suffix = "%") {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${formatNumber(value, digits)}${suffix}`;
}

function rankingIdentity(value: string) {
  const match = value.trim().match(/^(\d{4})\s+(.+)$/);
  return match ? { ticker: match[1], name: match[2] } : { ticker: value.slice(0, 4), name: value.slice(4).trim() };
}

function positionTone(position: string | null | undefined) {
  if (position === "跌破特價" || position === "特價" || position === "便宜") return "is-cheap";
  if (position === "昂貴" || position === "突破昂貴") return "is-expensive";
  return "is-neutral";
}

function riverLevels(waterline: number | null) {
  if (waterline === null) return [];
  return [
    { label: "昂貴", range: `${formatNumber(waterline * 1.2, 1)}～${formatNumber(waterline * 1.382, 1)}`, className: "expensive" },
    { label: "貴", range: `${formatNumber(waterline, 1)}～${formatNumber(waterline * 1.2, 1)}`, className: "rich" },
    { label: "便宜", range: `${formatNumber(waterline * .8, 1)}～${formatNumber(waterline, 1)}`, className: "cheap" },
    { label: "特價", range: `${formatNumber(waterline * .618, 1)}～${formatNumber(waterline * .8, 1)}`, className: "special" },
  ];
}

function FundamentalRiverChart({ points }: { points: FundamentalRiverPoint[] }) {
  const model = useMemo(() => {
    if (points.length < 2) return null;
    const allValues = points.flatMap((point) => [point.close, point.waterline * FUNDAMENTAL_RIVER_BOUNDS[0], point.waterline * FUNDAMENTAL_RIVER_BOUNDS[4]]);
    const minimum = Math.min(...allValues) * .94;
    const maximum = Math.max(...allValues) * 1.06;
    const width = 980;
    const height = 380;
    const pad = { left: 28, right: 70, top: 24, bottom: 44 };
    const x = (index: number) => pad.left + index / Math.max(1, points.length - 1) * (width - pad.left - pad.right);
    const y = (value: number) => pad.top + (maximum - value) / Math.max(1, maximum - minimum) * (height - pad.top - pad.bottom);
    const area = (lower: number, upper: number) => [
      ...points.map((point, index) => `${x(index)},${y(point.waterline * upper)}`),
      ...[...points].reverse().map((point, reverseIndex) => `${x(points.length - 1 - reverseIndex)},${y(point.waterline * lower)}`),
    ].join(" ");
    const line = (field: "close" | "waterline") => points.map((point, index) => `${index ? "L" : "M"}${x(index)} ${y(point[field])}`).join(" ");
    return { minimum, maximum, width, height, pad, x, y, area, priceLine: line("close"), waterline: line("waterline") };
  }, [points]);
  if (!model) return <div className="fundamental-river-empty">目前沒有足夠資料可繪製基本面河流</div>;
  const labels = [0, .25, .5, .75, 1].map((ratio) => Math.round((points.length - 1) * ratio));
  const ticks = [0, .25, .5, .75, 1].map((ratio) => model.maximum - (model.maximum - model.minimum) * ratio);
  return <div className="fundamental-chart-scroll" role="region" aria-label="基本面估值河流圖" tabIndex={0}>
    <svg viewBox={`0 0 ${model.width} ${model.height}`} role="img" aria-label="股價、同族群分水嶺與四層基本面估值河道">
      {ticks.map((tick) => <g key={tick}><line className="fundamental-grid" x1={model.pad.left} x2={model.width - model.pad.right} y1={model.y(tick)} y2={model.y(tick)} /><text className="fundamental-axis" x={model.width - model.pad.right + 10} y={model.y(tick) + 4}>{formatNumber(tick, tick >= 100 ? 0 : 1)}</text></g>)}
      <polygon className="fundamental-band fundamental-special" points={model.area(.618, .8)} />
      <polygon className="fundamental-band fundamental-cheap" points={model.area(.8, 1)} />
      <polygon className="fundamental-band fundamental-rich" points={model.area(1, 1.2)} />
      <polygon className="fundamental-band fundamental-expensive" points={model.area(1.2, 1.382)} />
      <path className="fundamental-waterline" d={model.waterline} />
      <path className="fundamental-price" d={model.priceLine} />
      {labels.map((index) => <text className="fundamental-date" key={`${points[index].date}-${index}`} x={model.x(index)} y={model.height - 14} textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{points[index].date.slice(2, 10)}</text>)}
    </svg>
  </div>;
}

function SummarySections({ payload, strong, rankingLabel, onSelect, onOpenKline }: { payload: SummaryPayload | null; strong: StrongRow[]; rankingLabel: string; onSelect: Props["onSelect"]; onOpenKline: Props["onOpenKline"] }) {
  const reports = payload?.recentReports ?? [];
  const screener = payload?.screener ?? [];
  return <div className="fundamental-summary-grid">
    <article className="fundamental-list-card strong-card">
      <header><div><span>LIVE TOP 20</span><h4>今日強勢前 20 大個股</h4><small>{rankingLabel}</small></div><strong>{strong.length || 20} 檔</strong></header>
      <div className="fundamental-screener-scroll" role="region" aria-label="今日強勢前二十大個股排行" tabIndex={0}>
        <div className="fundamental-strong-table">
          <div className="fundamental-strong-row is-head"><span>#</span><span>股票代號</span><span>股票名稱</span><span>交易資訊</span><span>族群</span><span>均線分數</span><span>漲跌幅</span><span>漲跌</span><span>即時／成交價</span><span>目前位置</span><span>距分水嶺</span><span>K 線</span></div>
          {strong.map((row) => <div className="fundamental-strong-row" key={row.ticker}>
            <span>{row.rank}</span>
            <button type="button" className="stock-code" onClick={() => onSelect(row.ticker)}>{row.ticker}</button>
            <button type="button" className="stock-name" onClick={() => onSelect(row.ticker)}>{row.name}</button>
            <span className="fundamental-trading-cell"><StockTradingBadges ticker={row.ticker} dense /></span>
            <span>{row.groupName}</span>
            <strong>{formatNumber(row.maScore, 0)}</strong>
            <em className={(row.changePct ?? 0) < 0 ? "negative" : "positive"}>{signed(row.changePct, 2)}</em>
            <em className={(row.change ?? 0) < 0 ? "negative" : "positive"}>{signed(row.change, 2, "")}</em>
            <b>{formatNumber(row.price, 2)}</b>
            <i className={positionTone(row.position)}>{row.position ?? "等待資料"}</i>
            <em className={(row.distancePct ?? 0) < 0 ? "negative" : "positive"}>{signed(row.distancePct, 1)}</em>
            <button type="button" className="kline-button" onClick={() => onOpenKline(row.ticker, row.name)}>開啟</button>
          </div>)}
          {!strong.length && <div className="fundamental-list-empty">即時強勢榜與河流資料正在同步。</div>}
        </div>
      </div>
    </article>
    <article className="fundamental-list-card report-card">
      <header><div><span>FULL WEEK REPORTS</span><h4>本週新公布財報</h4><small>資料週 {payload?.reportWeek ?? "更新中"}・整週完整列出</small></div><strong>{reports.length} 檔</strong></header>
      <div className="fundamental-screener-scroll" role="region" aria-label="本週新公布完整財報清單" tabIndex={0}>
        <div className="fundamental-report-table">
          <div className="fundamental-report-row is-head"><span>股票代號</span><span>股票名稱</span><span>交易資訊</span><span>族群</span><span>新公布</span><span>單季 EPS</span><span>目前位置</span><span>河流圖</span></div>
          {reports.map((row) => <div className="fundamental-report-row" key={row.ticker}>
            <button type="button" className="stock-code" onClick={() => onSelect(row.ticker)}>{row.ticker}</button>
            <button type="button" className="stock-name" onClick={() => onSelect(row.ticker)}>{row.name}</button>
            <span className="fundamental-trading-cell"><StockTradingBadges ticker={row.ticker} dense /></span>
            <span>{row.groupName}</span>
            <b>{row.reportPeriod.replace(/^20/, "")}</b>
            <strong>{formatNumber(row.singleQuarterEps, 2)}</strong>
            <i className={positionTone(row.position)}>{row.position ?? "等待資料"}</i>
            <button type="button" className="river-button" onClick={() => onSelect(row.ticker)}>看河流</button>
          </div>)}
          {!reports.length && <div className="fundamental-list-empty">本週完整財報名單正在更新。</div>}
        </div>
      </div>
      <footer>資料週 {payload?.reportWeek ?? "—"}・每週更新，新財報公布後重新計算整條基本面河道</footer>
    </article>
    <article className="fundamental-list-card screener-card">
      <header><div><span>MA SCORE × VALUE</span><h4>均線分數 ≥10 × 基本面便宜區</h4></div><strong>{screener.length} 檔</strong></header>
      <div className="fundamental-screener-scroll" role="region" aria-label="基本面便宜強勢股篩選" tabIndex={0}>
        <div className="fundamental-screener-table">
          <div className="fundamental-screener-row is-head"><span>#</span><span>股票代號</span><span>股票名稱</span><span>交易資訊</span><span>族群</span><span>均線分數</span><span>漲跌幅</span><span>成交價</span><span>目前位置</span><span>距分水嶺</span><span>K 線</span></div>
          {screener.map((row, index) => <div className="fundamental-screener-row" key={row.ticker}><span>{index + 1}</span><button type="button" className="stock-code" onClick={() => onSelect(row.ticker)}>{row.ticker}</button><button type="button" className="stock-name" onClick={() => onSelect(row.ticker)}>{row.name}</button><span className="fundamental-trading-cell"><StockTradingBadges ticker={row.ticker} dense /></span><span>{row.groupName}</span><strong>{row.maScore}</strong><em className={(row.changePct ?? 0) < 0 ? "negative" : "positive"}>{signed(row.changePct, 2)}</em><b>{formatNumber(row.close, 2)}</b><i className={positionTone(row.position)}>{row.position}</i><em className={row.distancePct < 0 ? "negative" : "positive"}>{signed(row.distancePct, 1)}</em><button type="button" className="kline-button" onClick={() => onOpenKline(row.ticker, row.name)}>開啟</button></div>)}
          {!screener.length && <div className="fundamental-list-empty">全市場估值或均線資料正在更新。</div>}
        </div>
      </div>
    </article>
  </div>;
}

export function FundamentalRiverWorkspace({ stock, livePrice, onSelect, onOpenKline }: Props) {
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [strongRows, setStrongRows] = useState<StrongRow[]>([]);
  const [rankingLabel, setRankingLabel] = useState("沿用首頁即時強弱榜排序");
  const [detailResult, setDetailResult] = useState<{ key: string; detail: DetailPayload | null } | null>(null);
  const [period, setPeriod] = useState<"3m" | "6m" | "all">("3m");
  const [refreshTick, setRefreshTick] = useState(0);
  const tracked = useRef(new Set<string>());
  const summaryLoading = useRef(false);
  const stockCode = stock?.code ?? "";
  const stockName = stock?.name ?? "";
  const stockMarket = stock?.market ?? null;
  const selectionKey = stockCode && stockMarket ? `${stockMarket}:${stockCode}` : "";
  const detail = detailResult?.key === selectionKey ? detailResult.detail : null;
  const loading = Boolean(stockCode && stockMarket && stockMarket !== "etf" && detailResult?.key !== selectionKey);
  const loadSummary = useCallback(async () => {
    if (summaryLoading.current) return;
    summaryLoading.current = true;
    try {
      const [summaryPayload, rankingPayload] = await Promise.all([
        fetch("/api/fundamental-river?mode=summary", { cache: "no-store", signal: AbortSignal.timeout(25_000) })
          .then((response) => response.ok ? response.json() as Promise<SummaryPayload> : null)
          .catch(() => null),
        fetch(`/api/live-ranking?refresh=${Date.now()}`, { cache: "no-store", signal: AbortSignal.timeout(25_000) })
          .then((response) => response.ok ? response.json() as Promise<LiveRankingPayload> : null)
          .catch(() => null),
      ]);
      if (summaryPayload) setSummary(summaryPayload);
      const ranked = rankingPayload?.rankings?.stocks?.strong ?? [];
      const identities = ranked.slice(0, 20).map((row) => ({ ...row, ...rankingIdentity(row.name) })).filter((row) => /^\d{4}$/.test(row.ticker));
      if (!identities.length) return;
      const tickers = identities.map((row) => row.ticker);
      const enrichment = await fetch(`/api/fundamental-river?mode=ranking&tickers=${encodeURIComponent(tickers.join(","))}`, { cache: "no-store", signal: AbortSignal.timeout(25_000) })
        .then((response) => response.ok ? response.json() as Promise<{ rows?: RiverRankingRow[] }> : null)
        .catch(() => null);
      const riverByTicker = new Map((enrichment?.rows ?? []).map((row) => [row.ticker, row]));
      const quoteByTicker = new Map((rankingPayload?.quotes ?? []).map((quote) => [quote.key.split(":").at(-1) ?? quote.key, quote]));
      setStrongRows(identities.map((row, index) => {
        const river = riverByTicker.get(row.ticker);
        const quote = quoteByTicker.get(row.ticker);
        const price = quote?.price ?? river?.close ?? null;
        const waterline = river?.waterline ?? null;
        return {
          ticker: row.ticker,
          name: row.name,
          rank: index + 1,
          groupName: river?.groupName && river.groupName !== "未分類" ? river.groupName : row.lead || "未分類",
          maScore: river?.maScore ?? null,
          close: river?.close ?? null,
          waterline,
          price,
          change: quote?.change ?? null,
          changePct: quote?.changePct ?? Number.parseFloat(row.change),
          position: price !== null && waterline !== null ? fundamentalRiverPosition(price, waterline) : river?.position ?? null,
          distancePct: price !== null && waterline !== null ? (price / waterline - 1) * 100 : river?.distancePct ?? null,
        };
      }));
      setRankingLabel(rankingPayload?.liveData
        ? "盤中即時價・每 30 秒同步首頁排行"
        : `${rankingPayload?.sourceDate ?? "最新交易日"} 收盤價・每 30 秒同步首頁排行`);
    } finally {
      summaryLoading.current = false;
    }
  }, []);

  useEffect(() => {
    void loadSummary();
    const timer = createVisibilityGatedInterval(() => { void loadSummary(); }, 30_000);
    return () => { timer.cancel(); };
  }, [loadSummary]);
  useEffect(() => {
    if (!stockCode || !stockMarket || stockMarket === "etf") return;
    const controller = new AbortController();
    fetch(`/api/fundamental-river?ticker=${encodeURIComponent(stockCode)}&market=${stockMarket}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<DetailPayload> : Promise.reject(new Error("fundamental_river_failed")))
      .then((payload) => {
        setDetailResult({ key: selectionKey, detail: payload });
        setSummary(payload);
        const key = `${payload.ticker}:${payload.quarters.at(-1)?.label ?? payload.dataDate ?? "current"}`;
        if (tracked.current.has(key)) return;
        tracked.current.add(key);
        void fetch("/api/fundamental-river", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          ticker: payload.ticker, name: payload.name || stockName, market: payload.market, groupName: payload.groupName,
          reportPeriod: payload.quarters.at(-1)?.label ?? payload.dataDate ?? "最新", ttmEps: payload.ttmEps,
          waterline: payload.waterline, position: payload.position ?? "等待資料", distancePct: payload.distancePct,
          close: payload.currentPrice, maScore: payload.maScore, pe: payload.pe, pb: payload.pb,
        }) }).then(() => loadSummary()).catch(() => undefined);
      })
      .catch(() => { if (!controller.signal.aborted) setDetailResult({ key: selectionKey, detail: null }); });
    return () => controller.abort();
  }, [loadSummary, refreshTick, selectionKey, stockCode, stockMarket, stockName]);

  const refreshDetail = () => {
    if (!selectionKey || stockMarket === "etf") return;
    setDetailResult(null);
    setRefreshTick((value) => value + 1);
  };

  const visiblePrice = livePrice ?? detail?.currentPrice ?? null;
  const visiblePosition = detail?.waterline != null && visiblePrice != null ? fundamentalRiverPosition(visiblePrice, detail.waterline) : detail?.position ?? null;
  const visibleDistance = detail?.waterline != null && visiblePrice != null ? (visiblePrice / detail.waterline - 1) * 100 : detail?.distancePct ?? null;
  const points = useMemo(() => {
    const all = detail?.riverPoints ?? [];
    if (period === "3m") return all.slice(-66);
    if (period === "6m") return all.slice(-132);
    return all;
  }, [detail?.riverPoints, period]);
  const latestQuarter = detail?.quarters.at(-1) ?? null;
  const previousQuarter = detail?.quarters.at(-2) ?? null;
  const quickStocks = strongRows.slice(0, 3);

  return <div className="fundamental-river-workspace">
    <section className="fundamental-river-intro">
      <div><span className="eyebrow">FUNDAMENTALS × PEER VALUATION</span><h3>基本面估值河流圖</h3><p>近四季 EPS 搭配同族群本益比中位數計算分水嶺；虧損公司自動改用每股淨值與同族群股價淨值比。</p></div>
      <div className="fundamental-quick-stocks" aria-label="今日強勢股票快速查詢">
        {quickStocks.length ? quickStocks.map((row) => <button type="button" key={row.ticker} onClick={() => onSelect(row.ticker)}>{row.ticker} {row.name}</button>) : ["2330", "2492", "2408"].map((ticker) => <button type="button" key={ticker} onClick={() => onSelect(ticker)}>{ticker}</button>)}
        {stockCode && stockMarket !== "etf" ? <button type="button" className="refresh" onClick={refreshDetail}>立即更新</button> : null}
      </div>
    </section>

    {stock?.market === "etf" && <div className="fundamental-river-empty"><strong>基本面河流圖僅適用上市、上櫃個股</strong><span>ETF 沒有可直接比較的單季 EPS 與每股淨值，請輸入一般股票。</span></div>}
    {stock && stock.market !== "etf" && loading && <div className="fundamental-river-loading"><span></span><strong>正在整合股價、財報、月營收與同族群估值…</strong></div>}
    {stock && stock.market !== "etf" && !loading && !detail && <div className="fundamental-river-empty"><strong>這檔股票的基本面資料目前無法完整取得</strong><span>請稍後再按「立即更新」，或先查看下方全市場篩選。</span></div>}
    {detail && <article className="fundamental-river-detail">
      <header className="fundamental-detail-head">
        <div><span>{detail.groupName}</span><h3>{detail.ticker} {detail.name || stock?.name}</h3><StockTradingBadges ticker={detail.ticker} /><small>資料日 {detail.dataDate || "最新交易日"}・同族群樣本 {detail.peerSampleSize} 檔</small></div>
        <strong className={positionTone(visiblePosition)}>{visiblePosition ?? "等待資料"}<small>距分水嶺 {signed(visibleDistance, 1)}</small></strong>
      </header>
      <div className="fundamental-key-metrics">
        <span><b>股價</b><strong>{formatNumber(visiblePrice, 2)}</strong><small>即時／最新成交</small></span>
        <span><b>分水嶺</b><strong>{formatNumber(detail.waterline, 1)}</strong><small>{detail.valuationBasis === "pb" ? "同族群 P/B" : "同族群 P/E"} 中位數</small></span>
        <span><b>目前位置</b><strong className={positionTone(visiblePosition)}>{visiblePosition ?? "—"}</strong><small>每日收盤後重算</small></span>
      </div>
      <div className="fundamental-period-switch" role="tablist" aria-label="河流圖期間">
        <button type="button" role="tab" aria-selected={period === "3m"} className={period === "3m" ? "active" : ""} onClick={() => setPeriod("3m")}>三個月</button>
        <button type="button" role="tab" aria-selected={period === "6m"} className={period === "6m" ? "active" : ""} onClick={() => setPeriod("6m")}>六個月</button>
        <button type="button" role="tab" aria-selected={period === "all"} className={period === "all" ? "active" : ""} onClick={() => setPeriod("all")}>長時間</button>
      </div>
      <FundamentalRiverChart points={points} />
      <div className="fundamental-legend"><span className="price">股價</span><span className="waterline">分水嶺</span><small>河道由下往上：特價／便宜／貴／昂貴</small></div>
      <div className="fundamental-value-layout">
        <div className="fundamental-ladder">{riverLevels(detail.waterline).map((level) => <div className={level.className} key={level.label}><strong>{level.label}</strong><span>{level.range}</span>{visiblePosition === level.label && <i>現在</i>}</div>)}</div>
        <div className="fundamental-financials">
          <div className="fundamental-current-line">現在 <strong>{formatNumber(visiblePrice, 2)}</strong>・位置 <b className={positionTone(visiblePosition)}>{visiblePosition}</b>・距分水嶺 <em className={(visibleDistance ?? 0) < 0 ? "negative" : "positive"}>{signed(visibleDistance, 1)}</em></div>
          <div className="fundamental-financial-grid">
            <span><b>本益比（近4季）</b><strong>{formatNumber(detail.pe, 1)}</strong></span>
            <span><b>股價淨值比</b><strong>{formatNumber(detail.pb, 2)}</strong></span>
            <span><b>近4季 EPS 合計</b><strong>{formatNumber(detail.ttmEps, 2)}</strong></span>
            <span><b>每股淨值</b><strong>{formatNumber(detail.bvps, 2)}</strong></span>
          </div>
          <div className="fundamental-data-group"><h4>單季 EPS（元）</h4><div>{detail.quarters.slice(-4).map((quarter) => <span key={quarter.date}><b>{quarter.label}</b> {formatNumber(quarter.eps, 2)}</span>)}</div></div>
          <div className="fundamental-data-group"><h4>近月營收（億）・今年累計 <em className={(detail.revenues.ytdYoYPct ?? 0) < 0 ? "negative" : "positive"}>{signed(detail.revenues.ytdYoYPct, 1)}</em></h4><div>{detail.revenues.recent.map((row) => <span key={row.period}><b>{row.period}</b> {formatNumber(row.revenue / 100_000_000, 1)} <em className={(row.yoyPct ?? 0) < 0 ? "negative" : "positive"}>{signed(row.yoyPct, 1)}</em></span>)}</div></div>
          <div className="fundamental-margin">營益率 <b>{previousQuarter?.label ?? "前季"} {formatNumber(previousQuarter?.operatingMargin, 1)}%</b><span>→</span><strong>{latestQuarter?.label ?? "本季"} {formatNumber(latestQuarter?.operatingMargin, 1)}%</strong></div>
        </div>
      </div>
      <section className="fundamental-howto">
        <div><span className="special">特價</span><span className="cheap">便宜</span><span className="rich">貴</span><span className="expensive">昂貴</span></div>
        <p><b>分水嶺</b>使用同族群本益比中位數，乘上這家公司近四季 EPS；財報更新後河道才會位移，股價線則每日更新。這和原本只看 160 日股價的「歷史價位位置均線」是兩套獨立模型。</p>
      </section>
      <footer><span>資料來源：TWSE／TPEx、公開財務資料與 HanStock 行情；僅供研究，不構成投資建議。</span><button type="button" onClick={() => onOpenKline(detail.ticker, detail.name || stock?.name || detail.ticker)}>開啟五分鐘 K 線</button></footer>
    </article>}
    <SummarySections payload={summary} strong={strongRows} rankingLabel={rankingLabel} onSelect={onSelect} onOpenKline={onOpenKline} />
  </div>;
}
