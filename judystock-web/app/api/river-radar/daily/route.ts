import { NextRequest } from "next/server";
import { GET as getTechnicalMarket } from "../../technical-market/route";
import { riverStatus, RIVER_ENGINE_VERSION } from "../../../../lib/river-radar";
import { readLatestRiverRadarDaily, saveRiverRadarDaily, type StoredRiverRadarRow } from "../../../../db/river-radar-history";

type RiverTechnicalRow = {
  code: string;
  market: "twse" | "tpex";
  date: string;
  open: number;
  close: number;
  changePct: number;
  maScore: number | null;
  maLabel: string;
  technicalReady: boolean;
  availableTradingDays: number;
  riverScore: number | null;
  riverStatus: string;
  riverSide: "bull" | "neutral" | "bear" | null;
  riverOpeningPct: number | null;
  riverComponents: Record<string, number> | null;
  riverConfirmationState: string;
  riverTrendDays: number;
  previousRiverScore: number | null;
  previousRiverStatus: string | null;
  isNewRiverBull: boolean;
  isNewRiverBear: boolean;
  isRiverExit: boolean;
};

type TechnicalPayload = {
  ok?: boolean;
  dataDate?: string;
  updatedAt?: string;
  historyTradingDays?: number;
  historyReady?: boolean;
  rows?: RiverTechnicalRow[];
};

export async function GET(request: NextRequest) {
  const refresh = request.nextUrl.searchParams.get("refresh") === "1";
  // A completed river snapshot is the primary read path. Filtering a generated
  // trading day must not rebuild 240 sessions or rewrite 2,000+ D1 rows on
  // every page open/tab change. The background worker alone requests refresh.
  const stored = await readLatestRiverRadarDaily().catch(() => null);
  let payload: TechnicalPayload = {};
  let freshRows: RiverTechnicalRow[] = [];
  if (refresh || (stored?.rows.length ?? 0) <= 1_000) {
    const upstreamUrl = new URL("/api/technical-market", request.nextUrl.origin);
    upstreamUrl.searchParams.set("readOnly", "1");
    const upstream = await getTechnicalMarket(new NextRequest(upstreamUrl));
    payload = await upstream.json() as TechnicalPayload;
    freshRows = (payload.rows ?? []).filter((row) => row.technicalReady && row.riverScore !== null && row.riverSide !== null);
    const snapshotImproved = freshRows.length > (stored?.rows.length ?? 0);
    const snapshotChanged = stored?.dataDate !== payload.dataDate || stored?.engineVersion !== RIVER_ENGINE_VERSION;
    if (freshRows.length > 1_000 && payload.dataDate && (snapshotImproved || snapshotChanged)) {
      await saveRiverRadarDaily(payload.dataDate, freshRows as StoredRiverRadarRow[]).catch(() => false);
    }
  }
  const useFreshRows = freshRows.length > 1_000;
  const allRows = (useFreshRows ? freshRows : stored?.rows ?? []) as RiverTechnicalRow[];
  const resolvedDataDate = useFreshRows ? payload.dataDate : stored?.dataDate ?? payload.dataDate;
  const storedHistoryTradingDays = allRows.reduce((maximum, row) => Math.max(maximum, Number(row.availableTradingDays) || 0), 0);
  const resolvedHistoryTradingDays = useFreshRows ? payload.historyTradingDays ?? storedHistoryTradingDays : storedHistoryTradingDays;
  const resolvedHistoryReady = useFreshRows ? payload.historyReady === true : allRows.length > 1_000 && resolvedHistoryTradingDays >= 240;

  const side = request.nextUrl.searchParams.get("side") ?? "bull";
  const view = request.nextUrl.searchParams.get("view") ?? "all";
  const defaultThreshold = side === "bear" ? 40 : 60;
  const threshold = Number(request.nextUrl.searchParams.get("threshold") ?? defaultThreshold);
  const strongThreshold = Number(request.nextUrl.searchParams.get("strongThreshold") ?? (side === "bear" ? 25 : 75));
  const refineScoreRaw = Number(request.nextUrl.searchParams.get("refineScore") ?? 0);
  const refineScore = Number.isFinite(refineScoreRaw) ? Math.min(100, Math.max(0, refineScoreRaw)) : 0;
  const maLevel = request.nextUrl.searchParams.get("maLevel") ?? "all";
  const minTrendDaysRaw = Number(request.nextUrl.searchParams.get("minTrendDays") ?? 0);
  const minTrendDays = Number.isFinite(minTrendDaysRaw) ? Math.min(240, Math.max(0, Math.floor(minTrendDaysRaw))) : 0;
  const minOpeningPctRaw = Number(request.nextUrl.searchParams.get("minOpeningPct") ?? 0);
  const minOpeningPct = Number.isFinite(minOpeningPctRaw) ? Math.min(100, Math.max(0, minOpeningPctRaw)) : 0;
  const dayMomentum = request.nextUrl.searchParams.get("dayMomentum") ?? "all";
  const limit = Math.min(500, Math.max(1, Number(request.nextUrl.searchParams.get("limit") ?? 100)));

  const previousSide = (row: RiverTechnicalRow) => row.previousRiverScore === null
    ? row.previousRiverStatus === "強多" || row.previousRiverStatus === "偏多" ? "bull"
      : row.previousRiverStatus === "強空" || row.previousRiverStatus === "偏空" ? "bear" : "neutral"
    : riverStatus(Number(row.previousRiverScore)).side;
  const matchesSide = (row: RiverTechnicalRow, scoreThreshold: number) => side === "bear"
    ? row.riverSide === "bear" && Number(row.riverScore) <= scoreThreshold
    : row.riverSide === "bull" && Number(row.riverScore) >= scoreThreshold;
  const isSelectedExit = (row: RiverTechnicalRow) => row.isRiverExit && previousSide(row) === side;
  const matchesMaLevel = (row: RiverTechnicalRow) => {
    if (maLevel === "all") return true;
    if (row.maScore === null) return false;
    if (side === "bear") {
      if (maLevel === "basic") return row.maScore <= 5;
      if (maLevel === "strong") return row.maScore <= 3;
      return row.maScore === 0;
    }
    if (maLevel === "basic") return row.maScore >= 10;
    if (maLevel === "strong") return row.maScore >= 12;
    return row.maScore === 15;
  };
  const matchesOpening = (row: RiverTechnicalRow) => {
    if (minOpeningPct <= 0) return true;
    if (row.riverOpeningPct === null) return false;
    return side === "bear" ? row.riverOpeningPct <= -minOpeningPct : row.riverOpeningPct >= minOpeningPct;
  };
  const matchesDayMomentum = (row: RiverTechnicalRow) => {
    if (dayMomentum === "all") return true;
    if (dayMomentum === "strong") return side === "bear" ? row.changePct <= -2 : row.changePct >= 2;
    return side === "bear" ? row.changePct < 0 : row.changePct > 0;
  };

  const filtered = allRows.filter((row) => {
    if (view === "exit") return isSelectedExit(row);
    if (!matchesSide(row, threshold)) return false;
    if (refineScore > 0 && !matchesSide(row, refineScore)) return false;
    if (view === "new" && !(side === "bear" ? row.isNewRiverBear : row.isNewRiverBull)) return false;
    if (view === "continuous" && row.riverTrendDays < 2) return false;
    if (view !== "new" && row.riverTrendDays < minTrendDays) return false;
    if (!matchesMaLevel(row)) return false;
    if (!matchesOpening(row)) return false;
    if (!matchesDayMomentum(row)) return false;
    return true;
  });
  filtered.sort((a, b) => {
    if (view === "continuous" && b.riverTrendDays !== a.riverTrendDays) return b.riverTrendDays - a.riverTrendDays;
    const aScore = view === "exit" ? Number(a.previousRiverScore) : Number(a.riverScore);
    const bScore = view === "exit" ? Number(b.previousRiverScore) : Number(b.riverScore);
    return side === "bear" ? aScore - bScore : bScore - aScore;
  });

  const standardRows = allRows.filter((row) => matchesSide(row, defaultThreshold));
  const modeCounts = {
    daily: standardRows.length,
    new: standardRows.filter((row) => side === "bear" ? row.isNewRiverBear : row.isNewRiverBull).length,
    continuous: standardRows.filter((row) => row.riverTrendDays >= 2).length,
    threshold: allRows.filter((row) => matchesSide(row, strongThreshold)).length,
    exit: allRows.filter(isSelectedExit).length,
  };

  const summary = allRows.reduce((counts, row) => {
    if (Number(row.riverScore) >= 75) counts.strongBull += 1;
    else if (Number(row.riverScore) >= 60) counts.bull += 1;
    else if (Number(row.riverScore) >= 40) counts.neutral += 1;
    else if (Number(row.riverScore) >= 25) counts.bear += 1;
    else counts.strongBear += 1;
    return counts;
  }, { strongBull: 0, bull: 0, neutral: 0, bear: 0, strongBear: 0 });

  return Response.json({
    ok: allRows.length > 1_000,
    dataDate: resolvedDataDate ?? "—",
    updatedAt: useFreshRows ? payload.updatedAt ?? new Date().toISOString() : stored?.updatedAt ?? new Date().toISOString(),
    historyTradingDays: resolvedHistoryTradingDays,
    historyRequiredDays: 240,
    historyReady: resolvedHistoryReady,
    historyPending: !resolvedHistoryReady,
    engineVersion: RIVER_ENGINE_VERSION,
    scoring: {
      weights: { pricePosition: 15, shortOrder: 20, mediumLongOrder: 25, slope: 15, opening: 15, volumeVwap: 10 },
      thresholds: { strongBull: 75, bull: 60, neutral: 40, bear: 25 },
      volumeState: "目前盤後官方快照未含量能與 VWAP，該 10 分採中性 5 分並標記量能待補。",
    },
    summary,
    totalReady: allRows.length,
    totalMatches: filtered.length,
    appliedFilters: { refineScore, maLevel, minTrendDays, minOpeningPct, dayMomentum },
    modeCounts,
    snapshotFallback: !useFreshRows && Boolean(stored?.rows.length),
    snapshotCacheHit: !refresh && !useFreshRows && Boolean(stored?.rows.length),
    rows: filtered.slice(0, limit),
  }, { headers: { "Cache-Control": "public, max-age=60, s-maxage=300, stale-while-revalidate=900" } });
}
