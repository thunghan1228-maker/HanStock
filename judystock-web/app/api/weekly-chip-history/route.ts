import { NextRequest, NextResponse } from "next/server";
import {
  listWeeklyChipArchiveSummaries,
  listWeeklyChipArchives,
  readWeeklyChipTickerHistory,
  saveWeeklyChipArchives,
  type WeeklyChipArchive,
} from "../../../db/weekly-chip-history";

const noStore = { "Cache-Control": "private, no-store, max-age=0" };

function validArchive(value: unknown): value is WeeklyChipArchive {
  if (!value || typeof value !== "object") return false;
  const archive = value as WeeklyChipArchive;
  return /^\d{4}\/\d{2}\/\d{2}$/.test(archive.weekEndDate)
    && Array.isArray(archive.stocks)
    && archive.stocks.length <= 3000
    && Array.isArray(archive.groups)
    && archive.groups.length <= 200
    && archive.weights && typeof archive.weights === "object";
}

export async function GET(request: NextRequest) {
  try {
    if (request.nextUrl.searchParams.get("archives") === "1") {
      const [archives, history] = await Promise.all([
        listWeeklyChipArchives(Math.max(1, Math.min(12, Number(request.nextUrl.searchParams.get("limit")) || 3))),
        listWeeklyChipArchiveSummaries(),
      ]);
      return NextResponse.json({ ok: true, archives, history }, { headers: noStore });
    }
    const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
    if (ticker) {
      if (!/^[0-9A-Z]{4,7}$/.test(ticker)) return NextResponse.json({ ok: false, error: "ticker_invalid" }, { status: 400, headers: noStore });
      const history = await readWeeklyChipTickerHistory(ticker);
      return NextResponse.json({ ok: true, ticker, history }, { headers: noStore });
    }
    const history = await listWeeklyChipArchiveSummaries();
    return NextResponse.json({ ok: true, history }, { headers: noStore });
  } catch (error) {
    return NextResponse.json({ ok: false, history: [], error: error instanceof Error ? error.message : "weekly_history_unavailable" }, { status: 503, headers: noStore });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json() as { archives?: unknown[] };
    const archives = (body.archives ?? []).filter(validArchive).slice(0, 3);
    if (!archives.length) return NextResponse.json({ ok: false, error: "archives_required" }, { status: 422, headers: noStore });
    const history = await saveWeeklyChipArchives(archives);
    return NextResponse.json({ ok: true, history }, { headers: noStore });
  } catch (error) {
    return NextResponse.json({ ok: false, history: [], error: error instanceof Error ? error.message : "weekly_history_save_failed" }, { status: 503, headers: noStore });
  }
}
