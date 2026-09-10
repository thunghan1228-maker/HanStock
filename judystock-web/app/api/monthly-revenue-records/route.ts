import {
  readMonthlyRevenueObservations,
  readMonthlyRevenueSnapshots,
  readMonthlyRevenueSignals,
  readMonthlyRevenueSyncState,
  saveMonthlyRevenueRefresh,
} from "../../../db/monthly-revenue";
import {
  REVENUE_ALERT_HISTORY_START,
  classifyMonthlyRevenueRecord,
  currentRevenueTarget,
  fetchCurrentOfficialMonthlyRevenue,
  monthlyRevenueBaseline,
} from "../../../lib/monthly-revenue-records";
import stockGroupsSource from "../../../data/stock_groups.py?raw";
import { parseHanStockOfficialPrimaryGroupMap } from "../../../lib/stock-primary-group";
import { loadDailyPriceVolumeSnapshot } from "../../../lib/daily-price-volume-snapshot";

const REFRESH_INTERVAL_MS = 5 * 60 * 1_000;
const PRIMARY_GROUP_BY_CODE = parseHanStockOfficialPrimaryGroupMap(stockGroupsSource);
let refreshInFlight: Promise<void> | null = null;
let lastRefreshAt = 0;

async function refreshOfficialRevenue() {
  const official = await fetchCurrentOfficialMonthlyRevenue();
  const stored = await readMonthlyRevenueObservations(monthlyRevenueBaseline.historyEnd);
  const signals = official.rows.flatMap((record) => classifyMonthlyRevenueRecord(record, stored));
  await saveMonthlyRevenueRefresh({
    records: official.rows,
    signals,
    revenueMonth: official.revenueMonth,
    sourcePublishedDate: official.sourcePublishedDate,
    coverage: official.coverage,
    sources: official.sources,
    checkedAt: Date.now(),
  });
  lastRefreshAt = Date.now();
}

async function maybeRefresh(force: boolean) {
  const target = currentRevenueTarget();
  const state = await readMonthlyRevenueSyncState(target.revenueMonth);
  const lastCompletedAt = Math.max(lastRefreshAt, state?.completedAt ?? 0);
  if (!force && Date.now() - lastCompletedAt < REFRESH_INTERVAL_MS) return;
  if (force && Date.now() - lastCompletedAt < 30_000) return;
  if (!refreshInFlight) {
    refreshInFlight = refreshOfficialRevenue().finally(() => {
      refreshInFlight = null;
    });
  }
  await refreshInFlight;
}

function summaryByKind(signals: Awaited<ReturnType<typeof readMonthlyRevenueSignals>>) {
  const summary = {
    allTimeHigh: 0,
    rolling12High: 0,
    allTimeLow: 0,
    rolling12Low: 0,
  };
  for (const signal of signals) {
    if (signal.signalKind === "all-time-high") summary.allTimeHigh += 1;
    if (signal.signalKind === "rolling-12-high") summary.rolling12High += 1;
    if (signal.signalKind === "all-time-low") summary.allTimeLow += 1;
    if (signal.signalKind === "rolling-12-low") summary.rolling12Low += 1;
  }
  return summary;
}

async function buildPayload(refreshError?: unknown) {
  const target = currentRevenueTarget();
  const [signals, syncState, currentRankings, dailyPrices] = await Promise.all([
    readMonthlyRevenueSignals(REVENUE_ALERT_HISTORY_START),
    readMonthlyRevenueSyncState(target.revenueMonth),
    readMonthlyRevenueSnapshots(target.revenueMonth),
    loadDailyPriceVolumeSnapshot(),
  ]);
  const previousMonth = monthlyRevenueBaseline.historyEnd;
  const previousRankings = Object.entries(monthlyRevenueBaseline.stocks).flatMap(([stockCode, stock]) => {
    const revenue = stock.recent.find(([month]) => month === previousMonth)?.[1];
    const previousYearMonth = `${Number(previousMonth.slice(0, 4)) - 1}${previousMonth.slice(4)}`;
    const previousYearRevenue = stock.recent.find(([month]) => month === previousYearMonth)?.[1];
    if (revenue === undefined) return [];
    return [{
      revenueMonth: previousMonth,
      stockCode,
      name: stock.name,
      market: stock.market,
      revenue,
      previousMonthRevenue: null,
      previousYearRevenue: previousYearRevenue ?? null,
      momPct: null,
      yoyPct: previousYearRevenue ? (revenue / previousYearRevenue - 1) * 100 : null,
      sourcePublishedDate: null,
    }];
  });
  const rankingRows = [...currentRankings, ...previousRankings].map((row) => ({
    ...row,
    groupName: PRIMARY_GROUP_BY_CODE.get(row.stockCode) ?? "未分類",
    ...(dailyPrices[row.market === "twse" ? "twse" : "tpex"].get(row.stockCode) ?? {
      closePrice: null, volumeLots: null, priceDate: null,
    }),
  }));
  const groupSizes = [...PRIMARY_GROUP_BY_CODE.values()].reduce<Record<string, number>>((sizes, groupName) => {
    sizes[groupName] = (sizes[groupName] ?? 0) + 1;
    return sizes;
  }, {});
  return {
    ok: Boolean(syncState) && !refreshError,
    stale: Boolean(refreshError),
    error: refreshError instanceof Error ? refreshError.message : refreshError ? "monthly_revenue_refresh_failed" : null,
    historyStart: REVENUE_ALERT_HISTORY_START,
    baseline: {
      historyStart: monthlyRevenueBaseline.historyStart,
      historyEnd: monthlyRevenueBaseline.historyEnd,
      stockCount: monthlyRevenueBaseline.stockCount,
      sourceRows: monthlyRevenueBaseline.sourceRows,
    },
    targetRevenueMonth: target.revenueMonth,
    checkedAt: syncState?.checkedAt ?? null,
    completedAt: syncState?.completedAt ?? null,
    sourcePublishedDate: syncState?.sourcePublishedDate ?? null,
    coverage: {
      total: syncState?.coverageTotal ?? 0,
      twse: syncState?.coverageTwse ?? 0,
      tpex: syncState?.coverageTpex ?? 0,
    },
    sources: syncState ? JSON.parse(syncState.sourcesJson) as string[] : [],
    summary: summaryByKind(signals),
    signals,
    rankings: {
      availableMonths: [...new Set(rankingRows.map((row) => row.revenueMonth))].sort().reverse(),
      rows: rankingRows,
      groupSizes,
    },
    refreshIntervalMinutes: REFRESH_INTERVAL_MS / 60_000,
  };
}

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const force = params.get("refresh") === "1" || params.get("auto") === "1";
  let refreshError: unknown;
  try {
    await maybeRefresh(force);
  } catch (error) {
    refreshError = error;
    console.error("[monthly-revenue] refresh failed", error);
  }
  try {
    const payload = await buildPayload(refreshError);
    return Response.json(payload, {
      status: payload.ok || payload.signals.length || payload.completedAt ? 200 : 502,
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (error) {
    return Response.json({
      ok: false,
      stale: false,
      error: error instanceof Error ? error.message : "monthly_revenue_unavailable",
      historyStart: REVENUE_ALERT_HISTORY_START,
      baseline: {
        historyStart: monthlyRevenueBaseline.historyStart,
        historyEnd: monthlyRevenueBaseline.historyEnd,
        stockCount: monthlyRevenueBaseline.stockCount,
        sourceRows: monthlyRevenueBaseline.sourceRows,
      },
      targetRevenueMonth: currentRevenueTarget().revenueMonth,
      checkedAt: null,
      completedAt: null,
      sourcePublishedDate: null,
      coverage: { total: 0, twse: 0, tpex: 0 },
      sources: [],
      summary: { allTimeHigh: 0, rolling12High: 0, allTimeLow: 0, rolling12Low: 0 },
      signals: [],
      refreshIntervalMinutes: REFRESH_INTERVAL_MS / 60_000,
    }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}
