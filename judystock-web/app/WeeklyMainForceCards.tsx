"use client";

import { useEffect, useMemo, useState } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import { rankBrokerBranchWeekly, type BrokerBranchWeeklyRow } from "../lib/broker-branch-weekly-score";
import { rankTdccWeeklyChanges, type TdccWeeklyRow } from "../lib/tdcc-weekly-score";
import { assessWeeklyMainForce } from "../lib/weekly-main-force-score";

type BrokerPayload = { rows?: BrokerBranchWeeklyRow[]; weekEndDate?: string | null };
type TdccPayload = { rows?: TdccWeeklyRow[]; dataDate?: string; previousDate?: string | null };
type WeeklyMainForceHistoryPoint = {
  weekEndDate: string;
  ticker: string;
  institutionalScore: number;
  brokerBranchScore: number;
  tdccLargeHolderScore: number;
  compositeScore: number;
  label: string;
};

function signed(value: number | null) {
  return value === null ? "待資料" : `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}
function scoreClass(value: number | null) { return value === null ? "pending" : value > 0 ? "positive" : value < 0 ? "negative" : "neutral"; }

function mergeHistory(...groups: WeeklyMainForceHistoryPoint[][]) {
  const merged = new Map<string, WeeklyMainForceHistoryPoint>();
  groups.flat().forEach((point) => merged.set(point.weekEndDate, point));
  return [...merged.values()].sort((a, b) => a.weekEndDate.localeCompare(b.weekEndDate));
}

function historyCacheKey(code: string) { return `hanstock-weekly-main-force-${code}`; }
function readCachedHistory(code: string) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(historyCacheKey(code)) ?? "[]") as WeeklyMainForceHistoryPoint[];
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}
function cacheHistory(code: string, history: WeeklyMainForceHistoryPoint[]) {
  try { window.localStorage.setItem(historyCacheKey(code), JSON.stringify(history.slice(-156))); } catch { /* storage may be unavailable */ }
}

function WeeklyMainForceHistoryChart({ code, history }: { code: string; history: WeeklyMainForceHistoryPoint[] }) {
  const series = history.slice(-26);
  const width = 760;
  const height = 250;
  const left = 54;
  const right = 18;
  const baseline = 116;
  const halfHeight = 78;
  const slotCount = Math.max(6, series.length);
  const leadingSlots = (slotCount - series.length) / 2;
  const maxMagnitude = Math.max(1, ...series.map((point) => Math.abs(point.compositeScore)));
  const columnWidth = (width - left - right) / slotCount;
  const barWidth = Math.min(58, columnWidth * 0.58);

  return <section className="chip-momentum-card weekly-main-force-chart" aria-label={`${code}主力綜合籌碼每週趨勢`}>
    <header><div><span className="eyebrow">WEEKLY MAIN FORCE MOMENTUM</span><h3>主力綜合籌碼週趨勢</h3></div><p><i className="positive" />紅柱為主力籌碼增強　<i className="negative" />綠柱為主力籌碼轉弱</p></header>
    {series.length ? <div className="chip-momentum-chart-scroll" role="region" aria-label={`${code}主力綜合籌碼最近週次`} tabIndex={0}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${code}最近${series.length}週主力綜合籌碼柱狀圖`}>
        <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline - halfHeight} y2={baseline - halfHeight} />
        <line className="chip-momentum-zero" x1={left} x2={width - right} y1={baseline} y2={baseline} />
        <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline + halfHeight} y2={baseline + halfHeight} />
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline - halfHeight + 4} textAnchor="end">+{maxMagnitude.toFixed(1)}</text>
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline + 4} textAnchor="end">0</text>
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline + halfHeight + 4} textAnchor="end">-{maxMagnitude.toFixed(1)}</text>
        {series.map((point, index) => {
          const barHeight = Math.max(2, Math.abs(point.compositeScore) / maxMagnitude * halfHeight);
          const x = left + (index + leadingSlots) * columnWidth + (columnWidth - barWidth) / 2;
          const y = point.compositeScore >= 0 ? baseline - barHeight : baseline;
          return <g key={`${code}-weekly-main-force-${point.weekEndDate}`}>
            <rect className={point.compositeScore >= 0 ? "chip-momentum-positive" : "chip-momentum-negative"} x={x} y={y} width={barWidth} height={barHeight} rx="5" />
            <text className={point.compositeScore >= 0 ? "chip-momentum-value positive" : "chip-momentum-value negative"} x={x + barWidth / 2} y={point.compositeScore >= 0 ? Math.max(18, y - 7) : Math.min(height - 30, y + barHeight + 16)} textAnchor="middle">{signed(point.compositeScore)}</text>
            <text className="chip-momentum-date" x={x + barWidth / 2} y={height - 14} textAnchor="middle">{point.weekEndDate.slice(5)}</text>
          </g>;
        })}
      </svg>
    </div> : <div className="weekly-main-force-chart-empty">三項週資料齊全後，會從本週開始自動累積柱狀圖。</div>}
    <footer>每週五依法人 35%＋分點 40%＋集保 25% 結算並永久保存；圖表顯示最近 26 週。</footer>
  </section>;
}

function CombinedChipMomentumChart({ code, history }: { code: string; history: WeeklyMainForceHistoryPoint[] }) {
  const series = history.slice(-26).map((point) => ({
    ...point,
    combinedScore: Number((point.institutionalScore * 0.6 + point.compositeScore * 0.4).toFixed(1)),
  }));
  const width = 760;
  const height = 250;
  const left = 54;
  const right = 18;
  const baseline = 116;
  const halfHeight = 78;
  const slotCount = Math.max(6, series.length);
  const leadingSlots = (slotCount - series.length) / 2;
  const maxMagnitude = Math.max(1, ...series.map((point) => Math.abs(point.combinedScore)));
  const columnWidth = (width - left - right) / slotCount;
  const barWidth = Math.min(58, columnWidth * 0.58);

  return <section className="chip-momentum-card weekly-main-force-chart" aria-label={`${code}短中線綜合籌碼每週趨勢`}>
    <header><div><span className="eyebrow">COMBINED CHIP MOMENTUM</span><h3>短中線綜合籌碼週趨勢</h3></div><p><i className="positive" />紅柱為綜合籌碼增強　<i className="negative" />綠柱為綜合籌碼轉弱</p></header>
    {series.length ? <div className="chip-momentum-chart-scroll" role="region" aria-label={`${code}短中線綜合籌碼最近週次`} tabIndex={0}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${code}最近${series.length}週短中線綜合籌碼柱狀圖`}>
        <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline - halfHeight} y2={baseline - halfHeight} />
        <line className="chip-momentum-zero" x1={left} x2={width - right} y1={baseline} y2={baseline} />
        <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline + halfHeight} y2={baseline + halfHeight} />
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline - halfHeight + 4} textAnchor="end">+{maxMagnitude.toFixed(1)}</text>
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline + 4} textAnchor="end">0</text>
        <text className="chip-momentum-axis-label" x={left - 8} y={baseline + halfHeight + 4} textAnchor="end">-{maxMagnitude.toFixed(1)}</text>
        {series.map((point, index) => {
          const barHeight = Math.max(2, Math.abs(point.combinedScore) / maxMagnitude * halfHeight);
          const x = left + (index + leadingSlots) * columnWidth + (columnWidth - barWidth) / 2;
          const y = point.combinedScore >= 0 ? baseline - barHeight : baseline;
          return <g key={`${code}-weekly-main-force-${point.weekEndDate}`}>
            <rect className={point.combinedScore >= 0 ? "chip-momentum-positive" : "chip-momentum-negative"} x={x} y={y} width={barWidth} height={barHeight} rx="5" />
            <text className={point.combinedScore >= 0 ? "chip-momentum-value positive" : "chip-momentum-value negative"} x={x + barWidth / 2} y={point.combinedScore >= 0 ? Math.max(18, y - 7) : Math.min(height - 30, y + barHeight + 16)} textAnchor="middle">{signed(point.combinedScore)}</text>
            <text className="chip-momentum-date" x={x + barWidth / 2} y={height - 14} textAnchor="middle">{point.weekEndDate.slice(5)}</text>
          </g>;
        })}
      </svg>
    </div> : <div className="weekly-main-force-chart-empty">週資料齊全後，會從本週開始自動累積綜合柱狀圖。</div>}
    <footer>短線五日籌碼 60%＋每週主力綜合 40%；每週五結算並永久保存，顯示最近 26 週。</footer>
  </section>;
}

type WeeklyCompositeSnapshot = { ticker: string; weekEndDate: string; score: number };

export function WeeklyMainForceCards({ code, institutionalScore, onCompositeUpdate }: { code: string; institutionalScore: number; onCompositeUpdate?: (snapshot: WeeklyCompositeSnapshot | null) => void }) {
  const [brokerPayload, setBrokerPayload] = useState<BrokerPayload | null>(null);
  const [tdccPayload, setTdccPayload] = useState<TdccPayload | null>(null);
  const [history, setHistory] = useState<WeeklyMainForceHistoryPoint[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch("/api/broker-branch-weekly", { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<BrokerPayload> : null).catch(() => null),
      fetch("/api/tdcc-radar", { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<TdccPayload> : null).catch(() => null),
    ]).then(([broker, tdcc]) => {
      setBrokerPayload(broker);
      setTdccPayload(tdcc);
    });
    return () => controller.abort();
  }, []);

  const brokerRows = useMemo(() => brokerPayload?.rows ?? [], [brokerPayload]);
  const tdccRows = useMemo(() => tdccPayload?.rows ?? [], [tdccPayload]);
  const brokerRow = brokerRows.find((row) => row.ticker === code) ?? null;
  const tdccRow = tdccRows.find((row) => row.code === code) ?? null;
  const brokerScore = useMemo(() => rankBrokerBranchWeekly(brokerRows).get(code) ?? null, [brokerRows, code]);
  const tdccScore = useMemo(() => rankTdccWeeklyChanges(tdccRows).get(code) ?? null, [tdccRows, code]);

  useEffect(() => {
    let controller: AbortController | null = null;
    setHistory(readCachedHistory(code));
    const load = () => {
      controller?.abort();
      controller = new AbortController();
      fetch(`/api/weekly-main-force-history?ticker=${encodeURIComponent(code)}`, { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() as Promise<{ history?: WeeklyMainForceHistoryPoint[] }> : null)
        .then((payload) => {
          if (!Array.isArray(payload?.history)) return;
          setHistory((current) => {
            const next = mergeHistory(current, payload.history ?? []);
            cacheHistory(code, next);
            return next;
          });
        })
        .catch(() => undefined);
    };
    load();
    const timer = createVisibilityGatedInterval(() => { void load(); }, 30_000);
    return () => { controller?.abort(); timer.cancel(); };
  }, [code]);

  const latestSaved = history.at(-1) ?? null;
  const effectiveInstitutionalScore = latestSaved?.institutionalScore ?? institutionalScore;
  const effectiveBrokerScore = brokerScore ?? latestSaved?.brokerBranchScore ?? null;
  const effectiveTdccScore = tdccScore ?? latestSaved?.tdccLargeHolderScore ?? null;
  const assessment = assessWeeklyMainForce({ institutional: effectiveInstitutionalScore, brokerBranch: effectiveBrokerScore, tdccLargeHolder: effectiveTdccScore });
  const currentWeekEndDate = latestSaved?.weekEndDate ?? brokerPayload?.weekEndDate ?? tdccPayload?.dataDate ?? null;
  const currentPoint = currentWeekEndDate && effectiveBrokerScore !== null && effectiveTdccScore !== null && assessment.score !== null ? {
    weekEndDate: currentWeekEndDate,
    ticker: code,
    institutionalScore: effectiveInstitutionalScore,
    brokerBranchScore: effectiveBrokerScore,
    tdccLargeHolderScore: effectiveTdccScore,
    compositeScore: assessment.score,
    label: assessment.label,
  } satisfies WeeklyMainForceHistoryPoint : null;
  const chartHistory = currentPoint ? mergeHistory(history, [currentPoint]) : history;

  useEffect(() => {
    if (!onCompositeUpdate) return;
    if (!currentWeekEndDate || assessment.score === null) {
      onCompositeUpdate(null);
      return;
    }
    onCompositeUpdate({ ticker: code, weekEndDate: currentWeekEndDate, score: assessment.score });
  }, [onCompositeUpdate, code, currentWeekEndDate, assessment.score]);

  useEffect(() => {
    const weekEndDate = brokerPayload?.weekEndDate ?? latestSaved?.weekEndDate;
    if (!weekEndDate || effectiveBrokerScore === null || effectiveTdccScore === null || assessment.score === null) return;
    const controller = new AbortController();
    fetch("/api/weekly-main-force-history", {
      method: "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weekEndDate, rows: [{ ticker: code, institutional: effectiveInstitutionalScore, brokerBranch: effectiveBrokerScore, tdccLargeHolder: effectiveTdccScore }] }),
    }).then((response) => response.ok ? response.json() as Promise<{ history?: WeeklyMainForceHistoryPoint[] }> : null)
      .then((payload) => { if (Array.isArray(payload?.history)) setHistory((current) => mergeHistory(current, payload.history ?? [])); })
      .catch(() => undefined);
    return () => controller.abort();
  }, [code, brokerPayload?.weekEndDate, latestSaved?.weekEndDate, effectiveInstitutionalScore, effectiveBrokerScore, effectiveTdccScore, assessment.score]);

  return <>
    <span><b>法人週分數</b><strong className={scoreClass(effectiveInstitutionalScore)}>{signed(effectiveInstitutionalScore)}</strong><small>最近五日法人平均｜權重 35%</small></span>
    <span className={scoreClass(effectiveBrokerScore)}><b>分點主力週分數</b><strong>{signed(effectiveBrokerScore)}</strong><small>{brokerRow ? `週淨額 ${brokerRow.netAmount.toLocaleString("zh-TW")}｜集中度 ${brokerRow.concentration.toFixed(1)}%` : latestSaved ? `${latestSaved.weekEndDate} 已保存週分數` : "等待正式券商分點週資料"}</small></span>
    <span className={scoreClass(effectiveTdccScore)}><b>集保大戶週分數</b><strong>{signed(effectiveTdccScore)}</strong><small>{tdccRow ? `持股 ${tdccRow.largeHolderPct.toFixed(2)}%｜週變化 ${tdccRow.weeklyChangePp === null ? "—" : `${tdccRow.weeklyChangePp > 0 ? "+" : ""}${tdccRow.weeklyChangePp.toFixed(2)} 個百分點`}` : latestSaved ? `${latestSaved.weekEndDate} 已保存週分數` : "等待兩期集保資料"}</small></span>
    <span className={scoreClass(assessment.score)}><b>每週主力綜合</b><strong>{assessment.score === null ? assessment.label : signed(assessment.score)}</strong><small>{assessment.score === null ? `已取得 ${assessment.availableComponentCount}/3 項` : `${assessment.label}｜35%／40%／25%`}</small></span>
    <WeeklyMainForceHistoryChart code={code} history={chartHistory} />
    <CombinedChipMomentumChart code={code} history={chartHistory} />
  </>;
}
