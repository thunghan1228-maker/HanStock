import { type NextRequest } from "next/server";
import { readTechnicalMarketSnapshotMetadata, readTechnicalMarketSnapshots } from "../../../db/technical-market-history";
import { readTechnicalMarketIndicatorMetadata, readTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import { readLatestMarketRanking, saveLatestMarketRanking } from "../../../db/market-ranking-snapshot";
import { buildMaScoreRanking, buildMaScoreRegulars, maArrangementBonus, MA_SCORE_PERIODS, NEW_HIGH_PERIODS, type MaScoreRankingRow } from "../../../lib/ma-score-ranking";

export const runtime = "edge";

type Indicator = {
  code: string;
  market: "twse" | "tpex";
  dataDate: string;
  updatedAt?: string | null;
  payload: Record<string, unknown>;
};

let payloadCache: { key: string; expiresAt: number; payload: Record<string, unknown> } | null = null;
const PERSISTENT_CACHE_VERSION = "ma-score-v2";
const RESPONSE_CACHE_HEADERS = { "Cache-Control": "public, max-age=300, s-maxage=1800, stale-while-revalidate=86400" };

async function sourceFingerprint() {
  const [history, indicators] = await Promise.all([
    readTechnicalMarketSnapshotMetadata().catch(() => null),
    readTechnicalMarketIndicatorMetadata().catch(() => null),
  ]);
  if (!history || !indicators) return null;
  // The ranking changes when a new market date, history day or stock enters
  // the stored universe. Do not scan every large JSON payload merely to build
  // a cache key; that scan was slower than returning the cached ranking.
  return [history.latestDate, history.dayCount, indicators.latestDate, indicators.stockCount].join("|");
}

function numberValue(value: unknown) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function indicatorRows(indicators: Indicator[]) {
  return indicators.flatMap((indicator): MaScoreRankingRow[] => {
    const payload = indicator.payload;
    const score = numberValue(payload.maScore);
    const close = numberValue(payload.close);
    const date = String(payload.date ?? indicator.dataDate ?? "");
    const maValues = Object.fromEntries(MA_SCORE_PERIODS.flatMap((period) => {
      const value = numberValue(payload[`ma${period}`]);
      return value === null ? [] : [[period, value]];
    })) as Record<number, number>;
    if (score === null || close === null || !date || Object.keys(maValues).length !== MA_SCORE_PERIODS.length) return [];
    const storedAbove = Array.isArray(payload.aboveMaPeriods) ? payload.aboveMaPeriods.map(Number).filter((period) => MA_SCORE_PERIODS.includes(period as typeof MA_SCORE_PERIODS[number])) : [];
    const aboveMaPeriods = storedAbove.length ? storedAbove : MA_SCORE_PERIODS.filter((period) => close >= maValues[period]);
    const newHighPeriods = Array.isArray(payload.newHighPeriods) ? payload.newHighPeriods.map(Number).filter((period) => NEW_HIGH_PERIODS.includes(period as typeof NEW_HIGH_PERIODS[number])) : [];
    const arrangementBonus = numberValue(payload.maArrangementBonus) ?? maArrangementBonus(score);
    const storedBaseScore = numberValue(payload.maBaseScore);
    const baseScore = storedBaseScore ?? aboveMaPeriods.length + newHighPeriods.length;
    const compositeScore = numberValue(payload.maCompositeScore) ?? baseScore + arrangementBonus;
    return [{
      code: indicator.code,
      name: String(payload.name ?? indicator.code),
      market: indicator.market,
      date,
      close,
      score: compositeScore,
      baseScore,
      arrangementScore: score,
      arrangementBonus,
      label: String(payload.maLabel ?? "均線整理"),
      bullConfirmed: Boolean(payload.maBullConfirmed),
      bearConfirmed: Boolean(payload.maBearConfirmed),
      maValues,
      aboveMaPeriods,
      newHighPeriods,
    }];
  });
}

export async function GET(request: NextRequest) {
  const requestedLookback = Number(request.nextUrl.searchParams.get("lookback"));
  const lookback = [5, 10, 20].includes(requestedLookback) ? requestedLookback : 20;
  const requestedDailyTop = Number(request.nextUrl.searchParams.get("dailyTop"));
  const dailyTop = [5, 10, 20].includes(requestedDailyTop) ? requestedDailyTop : 10;
  const requestedDate = request.nextUrl.searchParams.get("date")?.replaceAll("-", "/");
  const cacheKey = `${requestedDate ?? "latest"}|${lookback}|${dailyTop}`;
  if (payloadCache && payloadCache.key === cacheKey && payloadCache.expiresAt > Date.now()) {
    return Response.json(payloadCache.payload, { headers: RESPONSE_CACHE_HEADERS });
  }
  const persistentKey = `${PERSISTENT_CACHE_VERSION}:${cacheKey}`;
  const [storedPayload, fingerprint] = await Promise.all([
    readLatestMarketRanking(persistentKey).catch(() => null),
    sourceFingerprint(),
  ]);
  if (storedPayload && Array.isArray(storedPayload.rows) && storedPayload.rows.length > 0
    && (fingerprint === null || storedPayload.sourceFingerprint === fingerprint)) {
    payloadCache = { key: cacheKey, expiresAt: Date.now() + 30 * 60_000, payload: storedPayload };
    return Response.json(storedPayload, { headers: RESPONSE_CACHE_HEADERS });
  }
  const [snapshotResult, indicatorResult] = await Promise.all([
    readTechnicalMarketSnapshots(390).catch(() => []),
    readTechnicalMarketIndicators().catch(() => []),
  ]);
  const snapshots = snapshotResult as Parameters<typeof buildMaScoreRanking>[0];
  const indicators = indicatorResult as Indicator[];
  const historical = buildMaScoreRanking(snapshots, requestedDate);
  const latestIndicatorDate = indicators.map((row) => row.dataDate).sort().at(-1) ?? "";
  const date = requestedDate && historical.availableDates.includes(requestedDate)
    ? requestedDate
    : latestIndicatorDate || historical.date;
  let rows: MaScoreRankingRow[] = historical.rows;
  if (date === latestIndicatorDate) {
    const historicalByCode = new Map(rows.map((row) => [row.code, row]));
    rows = indicatorRows(indicators).map((row) => {
      const complete = historicalByCode.get(row.code);
      return complete ? {
        ...row,
        score: complete.score,
        baseScore: complete.baseScore,
        arrangementScore: complete.arrangementScore,
        arrangementBonus: complete.arrangementBonus,
        newHighPeriods: complete.newHighPeriods,
      } : row;
    }).sort((a, b) => b.score - a.score || b.close - a.close || a.code.localeCompare(b.code));
  }
  const regularEndingDate = historical.availableDates.find((item) => item <= date) ?? historical.date;
  const regulars = buildMaScoreRegulars(snapshots, regularEndingDate, lookback, dailyTop);
  const availableDates = [...new Set([latestIndicatorDate, ...historical.availableDates].filter(Boolean))].sort((a, b) => b.localeCompare(a)).slice(0, 30);
  const previousDate = historical.availableDates.find((item) => item < date) ?? null;
  const previousRows = previousDate ? buildMaScoreRanking(snapshots, previousDate).rows : [];
  const previousRankByCode = new Map(previousRows.map((row, index) => [row.code, index + 1]));
  const responsePayload = {
    ok: rows.length > 0,
    dataDate: date,
    updatedAt: indicators.map((row) => row.updatedAt).filter(Boolean).sort().at(-1) ?? new Date().toISOString(),
    fetchedAt: new Date().toISOString(),
    sourceFingerprint: fingerprint,
    lookback,
    dailyTop,
    maPeriods: MA_SCORE_PERIODS,
    newHighPeriods: NEW_HIGH_PERIODS,
    availableDates,
    rows: rows.map((row, index) => ({ ...row, rank: index + 1, previousRank: previousRankByCode.get(row.code) ?? null })),
    previousDate,
    previousScores: previousRows.map((row) => ({ code: row.code, score: row.score })),
    regulars: regulars.regulars,
    regularDates: regulars.dates,
    coverage: {
      currentStocks: rows.length,
      historicalStocks: historical.rows.length,
      historicalDays: snapshots.length,
      regularSampleDays: regulars.dates.length,
    },
  };
  payloadCache = { key: cacheKey, expiresAt: Date.now() + 30 * 60_000, payload: responsePayload };
  await saveLatestMarketRanking(responsePayload, persistentKey).catch((error) => {
    console.error("ma_score_ranking_cache_save_failed", error);
  });
  return Response.json(responsePayload, { headers: RESPONSE_CACHE_HEADERS });
}
