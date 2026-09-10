import { NextRequest, NextResponse } from "next/server";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ ticker: string }> },
) {
  const { ticker: rawTicker } = await context.params;
  const ticker = rawTicker.trim().toUpperCase();

  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) {
    return new NextResponse("Invalid ticker", { status: 400 });
  }

  const target = new URL(`/api/kline-embed/${encodeURIComponent(ticker)}`, request.url);
  const interval = request.nextUrl.searchParams.get("interval");
  const name = request.nextUrl.searchParams.get("name");

  target.searchParams.set("interval", interval === "1m" ? "1m" : interval === "1d" ? "1d" : "5m");
  if (name) target.searchParams.set("name", name.slice(0, 40));

  return NextResponse.redirect(target, 307);
}
