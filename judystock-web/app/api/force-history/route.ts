import { NextRequest, NextResponse } from "next/server";
import { readDailyForce, readIntradayForceHistory } from "../../../db/force-history";
import { dailyForceHistory } from "../../../lib/daily-force-history";

export async function GET(request: NextRequest) {
  const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) {
    return NextResponse.json({ ok: false, error: "invalid_ticker", points: [] }, { status: 400 });
  }
  try {
    // /force-bars persists authoritative observations; a failed daily-summary write
    // must not hide saved minutes or replace the archive with a rolling provider window.
    const [daily, one, five] = await Promise.all([
      readDailyForce(ticker, 90),
      readIntradayForceHistory(ticker, "1m").catch(() => []),
      readIntradayForceHistory(ticker, "5m").catch(() => []),
    ]);
    return NextResponse.json({ ok: true, ticker, points: dailyForceHistory(daily, [...one, ...five]), source: "durable-force-history" },
      { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json({ ok: false, error: error instanceof Error ? error.message : "force_history_failed", points: [] },
      { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
