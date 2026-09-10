import { NextRequest } from "next/server";
import { readTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import { DAILY_STRATEGY_CATALOG, DAILY_STRATEGY_MODEL_VERSION, isListedOrOtcStockCode, type DailyStrategyMatch } from "../../../lib/daily-strategy-signals";
import { GET as getDispositionRisk } from "../disposition-risk/route";
import { GET as getTechnicalMarket } from "../technical-market/route";

type IndicatorPayload = {
  code?: string;
  name?: string;
  market?: "twse" | "tpex";
  date?: string;
  close?: number;
  changePct?: number;
  dailyStrategyVersion?: string | null;
  dailyStrategyDataDate?: string | null;
  dailyStrategies?: DailyStrategyMatch[];
};

type DispositionPayload = { dispositions?: Array<{ code?: string; status?: string }> };

async function backfillOneBatch(request: NextRequest, market: "twse" | "tpex") {
  const url = new URL("/api/technical-market", request.nextUrl.origin);
  url.searchParams.set("stockIndicatorBackfill", "1");
  url.searchParams.set("dailyStrategies", "1");
  url.searchParams.set("indicatorMarket", market);
  url.searchParams.set("compact", "1");
  await getTechnicalMarket(new NextRequest(url));
}

function validMatch(value: unknown): value is DailyStrategyMatch {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<DailyStrategyMatch>;
  return typeof row.kind === "string" && typeof row.name === "string"
    && (row.direction === "bull" || row.direction === "bear") && typeof row.summary === "string";
}

export async function GET(request: NextRequest) {
  const requestedBackfill = request.nextUrl.searchParams.get("backfill");
  if (requestedBackfill === "twse" || requestedBackfill === "tpex") {
    await backfillOneBatch(request, requestedBackfill);
  }

  const indicators = (await readTechnicalMarketIndicators().catch(() => []))
    .filter((indicator) => isListedOrOtcStockCode(indicator.code));
  const ready = indicators.filter((indicator) => indicator.payload.dailyStrategyVersion === DAILY_STRATEGY_MODEL_VERSION);
  // During a multi-batch rebuild, do not wait up to nine seconds for the four
  // disposition feeds on every batch.  The ordinary first read and the final
  // completed batch still attach the disposition-only strategy.
  const shouldLoadDisposition = !requestedBackfill || ready.length === indicators.length;
  const dispositionResponse = shouldLoadDisposition ? await getDispositionRisk().catch(() => null) : null;
  const dispositionPayload = dispositionResponse
    ? await dispositionResponse.json().catch(() => ({} as DispositionPayload)) as DispositionPayload
    : {} as DispositionPayload;
  const activeDisposition = new Set((dispositionPayload.dispositions ?? [])
    .filter((row) => row.status === "處置中" || row.status === "即將處置")
    .map((row) => String(row.code ?? "")));
  const blackDragon = DAILY_STRATEGY_CATALOG.find((item) => item.kind === "dispositionBlackDragon")!;

  const rows = ready.flatMap((indicator) => {
    const payload = indicator.payload as IndicatorPayload;
    const matches = Array.isArray(payload.dailyStrategies)
      ? payload.dailyStrategies.filter((match) => validMatch(match) && String(match.kind) !== "motherChild")
      : [];
    if (activeDisposition.has(indicator.code) && matches.some((match) => match.kind === "turnoverBlack")
      && !matches.some((match) => match.kind === "dispositionBlackDragon")) {
      matches.push({ ...blackDragon, summary: "處置股出現換手黑龍型態，獨立提示" });
    }
    return matches.map((match) => ({
      code: indicator.code,
      name: String(payload.name ?? indicator.code),
      market: indicator.market,
      tradeDate: String(payload.dailyStrategyDataDate ?? indicator.dataDate),
      close: Number.isFinite(Number(payload.close)) ? Number(payload.close) : null,
      changePct: Number.isFinite(Number(payload.changePct)) ? Number(payload.changePct) : null,
      strategyKind: match.kind,
      strategyName: match.name,
      direction: match.direction,
      summary: match.summary,
    }));
  });
  const totals = { twse: 0, tpex: 0 };
  const completed = { twse: 0, tpex: 0 };
  indicators.forEach((indicator) => {
    totals[indicator.market] += 1;
    if (indicator.payload.dailyStrategyVersion === DAILY_STRATEGY_MODEL_VERSION) completed[indicator.market] += 1;
  });
  const missing = { twse: Math.max(0, totals.twse - completed.twse), tpex: Math.max(0, totals.tpex - completed.tpex) };
  const nextMarket = missing.twse > 0 ? "twse" : missing.tpex > 0 ? "tpex" : null;
  const dataDate = ready.map((row) => String(row.payload.dailyStrategyDataDate ?? row.dataDate)).sort().at(-1) ?? "—";
  const strategyCounts = Object.fromEntries(DAILY_STRATEGY_CATALOG.map((strategy) => [
    strategy.kind,
    rows.filter((row) => row.strategyKind === strategy.kind).length,
  ]));

  return Response.json({
    ok: ready.length > 0,
    modelVersion: DAILY_STRATEGY_MODEL_VERSION,
    dataDate,
    updatedAt: new Date().toISOString(),
    strategyCatalog: DAILY_STRATEGY_CATALOG,
    strategyCounts,
    coverage: {
      completed: completed.twse + completed.tpex,
      total: totals.twse + totals.tpex,
      missing: missing.twse + missing.tpex,
      byMarket: { twse: { completed: completed.twse, total: totals.twse }, tpex: { completed: completed.tpex, total: totals.tpex } },
      nextMarket,
    },
    rows,
  }, { headers: { "Cache-Control": "private, no-store" } });
}
