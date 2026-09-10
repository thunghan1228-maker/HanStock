import { NextRequest, NextResponse } from "next/server";
import { fetchCachedMarket } from "../../../lib/market-fetch-cache";

type Interval = "1m" | "5m" | "1d";

type RawCandle = {
  date?: string;
  ts?: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
};

type CandlePayload = {
  result?: { data?: { json?: { candles?: RawCandle[] } } };
};

const HANSTOCK_ORIGIN = "https://hanstock-battle-minimal.thunghan8.chatgpt.site";

function barTimestamp(dateLabel: string) {
  // Accepts "YYYY/MM/DD HH:mm", "MM/DD HH:mm", or "YYYY/MM/DD".
  const match = dateLabel.match(/^(?:(\d{4})[/-])?(\d{1,2})[/-](\d{1,2})(?: (\d{1,2}):(\d{2}))?$/);
  if (!match) return null;
  const year = match[1] ?? String(new Date(Date.now() + 8 * 3_600_000).getUTCFullYear());
  const month = match[2].padStart(2, "0");
  const day = match[3].padStart(2, "0");
  const hour = (match[4] ?? "00").padStart(2, "0");
  const minute = (match[5] ?? "00").padStart(2, "0");
  const ts = Date.parse(`${year}-${month}-${day}T${hour}:${minute}:00+08:00`);
  return Number.isFinite(ts) ? ts : null;
}

export async function GET(request: NextRequest) {
  const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
  const interval = (request.nextUrl.searchParams.get("interval") ?? "5m") as Interval;
  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) {
    return NextResponse.json({ ok: false, error: "invalid_ticker", bars: [] }, { status: 400 });
  }
  if (interval !== "1m" && interval !== "5m" && interval !== "1d") {
    return NextResponse.json({ ok: false, error: "invalid_interval", bars: [] }, { status: 400 });
  }

  try {
    const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval } }));
    const response = await fetchCachedMarket(`${HANSTOCK_ORIGIN}/api/trpc/stocks.candles?input=${input}`, {
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Kline/1.0" },
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    }, { ticker, activeMs: interval === "1d" ? 60_000 : 15_000 });
    if (!response.ok) throw new Error(`candles_${response.status}`);
    const payload = await response.json() as CandlePayload;
    const raw = payload.result?.data?.json?.candles ?? [];

    const bars = raw.flatMap((candle) => {
      const label = candle.date ?? "";
      const ts = typeof candle.ts === "number" && candle.ts > 0
        ? (candle.ts < 1_000_000_000_000 ? candle.ts * 1000 : candle.ts)
        : barTimestamp(label);
      const { open, high, low, close } = candle;
      if (!ts || ![open, high, low, close].every((value) => typeof value === "number" && Number.isFinite(value) && value > 0)) return [];
      return [{
        ts,
        open: open as number,
        high: high as number,
        low: low as number,
        close: close as number,
        volume: typeof candle.volume === "number" && Number.isFinite(candle.volume) ? candle.volume : 0,
      }];
    }).sort((a, b) => a.ts - b.ts);

    return NextResponse.json({ ok: true, ticker, interval, bars }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "kline_candles_failed", bars: [] },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
