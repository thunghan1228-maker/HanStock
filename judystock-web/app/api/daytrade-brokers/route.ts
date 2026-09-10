import { NextResponse } from "next/server";
import { readDaytradeFlow, saveDaytradeFlow, type DaytradeFlowRecord } from "../../../db/daytrade-flow";

type Category = DaytradeFlowRecord["category"];
type HubFlowRow = { ticker?: string; name?: string; market?: string; trade_date?: string; category?: string; close_price?: number; reference_price?: number; limit_up_price?: number; day_change_pct?: number; large_buy_amount?: number; large_sell_amount?: number; total_turnover_amount?: number; late_large_buy_amount?: number; price_impact_pct?: number; previous_large_buy_amount?: number; next_day_large_sell_amount?: number; suspicion_score?: number; main_force_data_available?: boolean | number; main_force_data_status?: string };
type HubPayload = { status?: string; scan_status?: string; data_date?: string; updated_at?: string; requested_count?: number; processed_count?: number; data_missing_count?: number; rows?: HubFlowRow[]; errors?: string[] };
const HUB_BASES = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];
const finite = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : 0;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const validCategories = new Set<Category>(["漲停鎖定", "曾達漲停", "強勢大單"]);

function scoreRow(row: HubFlowRow): DaytradeFlowRecord | null {
  const ticker = String(row.ticker ?? "").trim().toUpperCase(), tradeDate = String(row.trade_date ?? "").trim();
  const category = String(row.category ?? "") as Category;
  const largeBuyAmount = Math.max(0, finite(row.large_buy_amount)), largeSellAmount = Math.max(0, finite(row.large_sell_amount)), turnoverAmount = Math.max(0, finite(row.total_turnover_amount));
  if (!/^[0-9A-Z]{2,12}$/.test(ticker) || !tradeDate || turnoverAmount <= 0 || !validCategories.has(category)) return null;
  const netLargeAmount = largeBuyAmount - largeSellAmount, participationRate = largeBuyAmount / turnoverAmount * 100, netRate = Math.max(0, netLargeAmount / turnoverAmount * 100);
  const lateBuyConcentration = largeBuyAmount > 0 ? Math.max(0, finite(row.late_large_buy_amount)) / largeBuyAmount * 100 : 0, priceImpact = Math.max(0, finite(row.price_impact_pct));
  const calculatedScore = clamp(participationRate / 25, 0, 1) * 40 + clamp(netRate / 15, 0, 1) * 25 + clamp(lateBuyConcentration / 80, 0, 1) * 20 + clamp(priceImpact / 5, 0, 1) * 15;
  const suspicionScore = finite(row.suspicion_score) || calculatedScore;
  const previousBuy = Math.max(0, finite(row.previous_large_buy_amount)), nextDaySell = Math.max(0, finite(row.next_day_large_sell_amount));
  const mainForceDataAvailable = row.main_force_data_available === undefined ? true : row.main_force_data_available !== false && row.main_force_data_available !== 0;
  const mainForceDataStatus = String(row.main_force_data_status ?? (mainForceDataAvailable ? "historical_ticks" : "pending_backfill"));
  return { ticker, name: String(row.name ?? ticker), market: String(row.market ?? "—"), tradeDate, category, closePrice: finite(row.close_price), referencePrice: finite(row.reference_price), limitUpPrice: finite(row.limit_up_price), dayChangePct: finite(row.day_change_pct), largeBuyAmount, largeSellAmount, netLargeAmount, turnoverAmount, participationRate, lateBuyConcentration, suspicionScore, estimatedNextDaySellAmount: mainForceDataAvailable ? Math.max(0, netLargeAmount) * clamp((suspicionScore - 20) / 80, .15, .85) : 0, confirmedReversalRate: previousBuy > 0 ? Math.min(previousBuy, nextDaySell) / previousBuy * 100 : null, updatedAt: Date.now(), mainForceDataAvailable, mainForceDataStatus };
}
function present(record: DaytradeFlowRecord) {
  const referencePrice = record.referencePrice && record.referencePrice > 0
    ? record.referencePrice
    : record.dayChangePct > -100 ? record.closePrice / (1 + record.dayChangePct / 100) : record.closePrice;
  return { ...record, dayChange: record.closePrice - referencePrice, suspicionLabel: record.category, fiveDayRates: [] as number[] };
}

export async function GET(request: Request) {
  const force = new URL(request.url).searchParams.get("force") === "1";
  let latestProgress: Pick<HubPayload, "scan_status" | "requested_count" | "processed_count" | "data_missing_count" | "data_date" | "updated_at"> = {};
  for (const base of HUB_BASES) try {
    const response = await fetch(`${base}/api/hub/daytrade-flow-ranking?limit=1000${force ? "&force=true" : ""}`, { cache: "no-store", headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.0" }, signal: AbortSignal.timeout(20_000) });
    if (!response.ok) continue;
    const payload = await response.json() as HubPayload; latestProgress = payload;
    const records = (payload.rows ?? []).map(scoreRow).filter((row): row is DaytradeFlowRecord => Boolean(row));
    if (!records.length) continue;
    await saveDaytradeFlow(records).catch(() => undefined);
    return NextResponse.json({ ok: true, dataDate: payload.data_date ?? records[0].tradeDate, updatedAt: payload.updated_at ?? new Date().toISOString(), scanStatus: payload.scan_status, requestedCount: payload.requested_count ?? 0, processedCount: payload.processed_count ?? 0, dataMissingCount: payload.data_missing_count ?? records.filter((row) => !row.mainForceDataAvailable).length, rows: records.map(present) }, { headers: { "Cache-Control": "no-store" } });
  } catch { /* 改走下一個正式來源或已保存資料。 */ }
  try { const stored = await readDaytradeFlow(1000); if (stored.length) return NextResponse.json({ ok: true, dataDate: stored[0].tradeDate, updatedAt: new Date(Math.max(...stored.map((row) => row.updatedAt))).toISOString(), scanStatus: latestProgress.scan_status, requestedCount: latestProgress.requested_count ?? 0, processedCount: latestProgress.processed_count ?? 0, dataMissingCount: latestProgress.data_missing_count ?? stored.filter((row) => !row.mainForceDataAvailable).length, rows: stored.map(present), storedFallback: true }, { headers: { "Cache-Control": "no-store" } }); } catch { /* 首個交易日尚無保存資料。 */ }
  const requested = latestProgress.requested_count ?? 0, processed = latestProgress.processed_count ?? 0;
  return NextResponse.json({ ok: false, dataDate: latestProgress.data_date ?? null, updatedAt: latestProgress.updated_at ?? new Date().toISOString(), scanStatus: latestProgress.scan_status ?? "not_started", requestedCount: requested, processedCount: processed, dataMissingCount: latestProgress.data_missing_count ?? 0, rows: [], message: requested > 0 ? `全市場正在掃描 ${processed}/${requested}，符合條件的股票會分批出現並永久保存；不需要券商分點付費資料。` : "全市場掃描正在啟動；完成後會自動顯示並永久保存，不需要券商分點付費資料。" }, { headers: { "Cache-Control": "no-store" } });
}
