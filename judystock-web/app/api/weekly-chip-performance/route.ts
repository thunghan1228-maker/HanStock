import { NextRequest, NextResponse } from "next/server";
import { readWeeklyChipPrices, saveWeeklyChipPrices } from "../../../db/weekly-chip-history";

type Candle = { date?: string; close?: number };
type CandlePayload = { result?: { data?: { json?: { candles?: Candle[] } } } };

const noStore = { "Cache-Control": "private, no-store, max-age=0" };

function normalizeDate(value: string, targetYear: string) {
  const full = value.match(/(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})/);
  if (full) return `${full[1]}/${full[2].padStart(2, "0")}/${full[3].padStart(2, "0")}`;
  const short = value.match(/(?:^|\s)(\d{1,2})[\/-](\d{1,2})(?:\s|$)/);
  return short ? `${targetYear}/${short[1].padStart(2, "0")}/${short[2].padStart(2, "0")}` : "";
}

export function findWeeklyClose(candles: Candle[], targetDate: string) {
  const targetYear = targetDate.slice(0, 4);
  const match = candles.find((candle) => normalizeDate(String(candle.date ?? ""), targetYear) === targetDate);
  const close = Number(match?.close);
  return Number.isFinite(close) && close > 0 ? close : null;
}

async function loadDailyCandles(ticker: string) {
  const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval: "1d" } }));
  const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.candles?input=${input}`, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Weekly-Chip/1.0" },
    signal: AbortSignal.timeout(6_000),
  });
  if (!response.ok) throw new Error(`weekly_candles_${response.status}`);
  const payload = await response.json() as CandlePayload;
  return Array.isArray(payload.result?.data?.json?.candles) ? payload.result!.data!.json!.candles! : [];
}

async function mapWithConcurrency<T, R>(items: T[], concurrency: number, worker: (item: T) => Promise<R>) {
  const results = new Array<R>(items.length);
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      results[index] = await worker(items[index]);
    }
  }));
  return results;
}

export async function GET(request: NextRequest) {
  const startDate = request.nextUrl.searchParams.get("start") ?? "";
  const endDate = request.nextUrl.searchParams.get("end") ?? "";
  const tickers = [...new Set((request.nextUrl.searchParams.get("items") ?? "").split(",").map((value) => value.trim().toUpperCase()).filter((value) => /^[0-9A-Z]{4,7}$/.test(value)))].slice(0, 50);
  if (!/^\d{4}\/\d{2}\/\d{2}$/.test(startDate) || !/^\d{4}\/\d{2}\/\d{2}$/.test(endDate) || !tickers.length) {
    return NextResponse.json({ ok: false, error: "dates_and_items_required", rows: [] }, { status: 400, headers: noStore });
  }

  let saved = new Map<string, number>();
  try { saved = await readWeeklyChipPrices(tickers, [startDate, endDate]); } catch { /* Fetch missing prices below. */ }
  const missing = tickers.filter((ticker) => !saved.has(`${startDate}:${ticker}`) || !saved.has(`${endDate}:${ticker}`));
  const fetched = await mapWithConcurrency(missing, 20, async (ticker) => {
    try {
      const candles = await loadDailyCandles(ticker);
      return { ticker, startClose: findWeeklyClose(candles, startDate), endClose: findWeeklyClose(candles, endDate) };
    } catch {
      return { ticker, startClose: null, endClose: null };
    }
  });

  const priceWrites = fetched.flatMap((row) => [
    row.startClose === null ? null : { weekEndDate: startDate, ticker: row.ticker, closePrice: row.startClose },
    row.endClose === null ? null : { weekEndDate: endDate, ticker: row.ticker, closePrice: row.endClose },
  ].filter((row): row is { weekEndDate: string; ticker: string; closePrice: number } => row !== null));
  try { await saveWeeklyChipPrices(priceWrites); } catch { /* The response remains useful when durable storage is briefly unavailable. */ }
  for (const row of priceWrites) saved.set(`${row.weekEndDate}:${row.ticker}`, row.closePrice);

  const rows = tickers.map((ticker) => {
    const startClose = saved.get(`${startDate}:${ticker}`) ?? null;
    const endClose = saved.get(`${endDate}:${ticker}`) ?? null;
    const returnPct = startClose && endClose ? Math.round(((endClose - startClose) / startClose) * 10_000) / 100 : null;
    return { ticker, startDate, endDate, startClose, endClose, returnPct };
  });
  return NextResponse.json({ ok: rows.some((row) => row.returnPct !== null), startDate, endDate, rows }, { headers: noStore });
}
