import { NextRequest, NextResponse } from "next/server";
import { readLatestWeeklyMainForceHistory, readWeeklyMainForceTickerHistory, saveWeeklyMainForceHistory, type WeeklyMainForceHistoryRecord } from "../../../db/weekly-main-force-history";
import { assessWeeklyMainForce } from "../../../lib/weekly-main-force-score";

const noStore = { "Cache-Control": "private, no-store, max-age=0" };
type InputRow = { ticker?: unknown; institutional?: unknown; brokerBranch?: unknown; tdccLargeHolder?: unknown };

function sameOrigin(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try { return new URL(origin).origin === request.nextUrl.origin; } catch { return false; }
}

function score(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) && number >= -100 && number <= 100 ? Math.round(number * 10) / 10 : null;
}

function record(weekEndDate: string, input: InputRow): WeeklyMainForceHistoryRecord | null {
  const ticker = String(input.ticker ?? "").trim().toUpperCase();
  const institutionalScore = score(input.institutional);
  const brokerBranchScore = score(input.brokerBranch);
  const tdccLargeHolderScore = score(input.tdccLargeHolder);
  if (!/^[0-9A-Z]{4,7}$/.test(ticker) || institutionalScore === null || brokerBranchScore === null || tdccLargeHolderScore === null) return null;
  const assessment = assessWeeklyMainForce({ institutional: institutionalScore, brokerBranch: brokerBranchScore, tdccLargeHolder: tdccLargeHolderScore });
  if (assessment.score === null) return null;
  return { weekEndDate, ticker, institutionalScore, brokerBranchScore, tdccLargeHolderScore, compositeScore: assessment.score, label: assessment.label };
}

export async function GET(request: NextRequest) {
  try {
    const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
    if (!ticker) {
      const rows = await readLatestWeeklyMainForceHistory();
      return NextResponse.json({ ok: true, weekEndDate: rows[0]?.weekEndDate ?? null, rows }, { headers: noStore });
    }
    if (!/^[0-9A-Z]{4,7}$/.test(ticker)) return NextResponse.json({ ok: false, history: [], error: "ticker_invalid" }, { status: 400, headers: noStore });
    return NextResponse.json({ ok: true, ticker, history: await readWeeklyMainForceTickerHistory(ticker) }, { headers: noStore });
  } catch (error) {
    return NextResponse.json({ ok: false, history: [], error: error instanceof Error ? error.message : "weekly_main_force_history_unavailable" }, { status: 503, headers: noStore });
  }
}

export async function POST(request: NextRequest) {
  try {
    if (!sameOrigin(request)) return NextResponse.json({ ok: false, error: "same_origin_required" }, { status: 403, headers: noStore });
    const body = await request.json() as { weekEndDate?: unknown; rows?: InputRow[] };
    const weekEndDate = String(body.weekEndDate ?? "");
    if (!/^\d{4}\/\d{2}\/\d{2}$/.test(weekEndDate)) return NextResponse.json({ ok: false, error: "week_end_date_invalid" }, { status: 422, headers: noStore });
    const records = (Array.isArray(body.rows) ? body.rows : []).slice(0, 3_000).flatMap((row) => {
      const valid = record(weekEndDate, row);
      return valid ? [valid] : [];
    });
    if (!records.length) return NextResponse.json({ ok: false, error: "weekly_main_force_rows_required" }, { status: 422, headers: noStore });
    await saveWeeklyMainForceHistory(records);
    const requestedTicker = records.length === 1 ? records[0].ticker : null;
    const history = requestedTicker ? await readWeeklyMainForceTickerHistory(requestedTicker) : [];
    return NextResponse.json({ ok: true, saved: records.length, ticker: requestedTicker, history }, { headers: noStore });
  } catch (error) {
    return NextResponse.json({ ok: false, history: [], error: error instanceof Error ? error.message : "weekly_main_force_history_save_failed" }, { status: 503, headers: noStore });
  }
}
