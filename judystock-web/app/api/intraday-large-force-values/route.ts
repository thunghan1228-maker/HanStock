import { NextRequest, NextResponse } from "next/server";
import { readPreviousTechnicalMarketSnapshot } from "../../../db/technical-market-history";
import { homeworkPriceChange } from "../../../lib/intraday-force-homework";
import { backfillRankingForceClose, readAvailableRankingForceClose } from "../../../lib/ranking-force-backfill";
import {
  calculateIntradayLargeForceValue,
  type IntradayLargeForceMinuteBar,
} from "../../../lib/intraday-large-force";
import {
  readIntradayLargeForceMonitorRows,
  readIntradayLargeForceScanProgress,
} from "../../../db/intraday-large-force-scan";

const HUB_BASES = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];
const MAX_TICKERS = 200;
const LIVE_CACHE_MS = 5_000;
const HISTORICAL_CACHE_MS = 6 * 60 * 60_000;

type ForceValueRow = {
  ticker: string;
  tradeDate: string;
  forcePct: number | null;
  barTs: number | null;
  price?: number | null;
  buyAmount?: number | null;
  sellAmount?: number | null;
  netAmount?: number | null;
  turnoverAmount?: number | null;
  source?: "historical-ticks" | "minute-bars";
};

type BatchPayload = { data?: Record<string, IntradayLargeForceMinuteBar[]> };
type SinglePayload = {
  bars?: IntradayLargeForceMinuteBar[];
  data?: { bars?: IntradayLargeForceMinuteBar[] };
};

const valueCache = new Map<string, { expiresAt: number; row: ForceValueRow }>();
const pendingValues = new Map<string, Promise<Map<string, ForceValueRow>>>();

function taipeiTradeDate() {
  const date = new Date(Date.now() + 8 * 60 * 60_000);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function requestedTickers(value: string | null) {
  return [...new Set((value ?? "").split(",")
    .map((ticker) => ticker.trim().toUpperCase())
    .filter((ticker) => /^[1-9]\d{3}[A-Z]?$/.test(ticker)))]
    .slice(0, MAX_TICKERS);
}

async function loadBatchBars(tickers: string[]) {
  const barsByTicker = new Map<string, IntradayLargeForceMinuteBar[]>();
  for (const base of HUB_BASES) {
    const missing = tickers.filter((ticker) => !barsByTicker.has(ticker));
    if (missing.length === 0) break;
    try {
      const response = await fetch(new URL("/api/hub/bars1m/batch", base), {
        method: "POST",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "User-Agent": "HanStock-Intraday-Large-Force-Values/1.0",
        },
        body: JSON.stringify({ codes: missing }),
        signal: AbortSignal.timeout(18_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as BatchPayload;
      for (const ticker of missing) {
        const bars = payload.data?.[ticker];
        if (Array.isArray(bars) && bars.length > 0) barsByTicker.set(ticker, bars);
      }
    } catch {
      // 同批改試正式備援主機；個別股票沒有資料時仍回傳待補狀態。
    }
  }
  return barsByTicker;
}

async function loadSingleTickerBars(ticker: string, tradeDate: string, diagnostics?: Map<string, string[]>) {
  const attempts: string[] = [];
  for (const base of HUB_BASES) {
    try {
      const response = await fetch(new URL(`/api/hub/bars1m/${encodeURIComponent(ticker)}`, base), {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Intraday-Large-Force-Values/1.1" },
        signal: AbortSignal.timeout(3_500),
      });
      if (!response.ok) {
        attempts.push(`${base}:${response.status}`);
        continue;
      }
      const payload = await response.json() as SinglePayload;
      const bars = payload.bars ?? payload.data?.bars;
      attempts.push(`${base}:200:${Array.isArray(bars) ? bars.length : "invalid"}`);
      if (Array.isArray(bars) && calculateIntradayLargeForceValue(bars, tradeDate)) {
        diagnostics?.set(ticker, attempts);
        return bars;
      }
    } catch (error) {
      attempts.push(`${base}:${error instanceof Error ? error.name : "error"}`);
      // 正式個股端點短暫失敗時再試備援主機。
    }
  }
  diagnostics?.set(ticker, attempts);
  return [];
}

async function fillMissingSingleTickerBars(
  tickers: string[],
  barsByTicker: Map<string, IntradayLargeForceMinuteBar[]>,
  tradeDate: string,
  diagnostics?: Map<string, string[]>,
) {
  // 個股端點每次只訂閱一檔；限制同時連線數，避免「今日即時」頁籤一次
  // 要補數百檔時壓垮 Hub。創高黑龍約數十檔可在數輪內完成。
  const concurrency = 12;
  let cursor = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, tickers.length) }, async () => {
    while (cursor < tickers.length) {
      const ticker = tickers[cursor++];
      const bars = await loadSingleTickerBars(ticker, tradeDate, diagnostics);
      if (bars.length > 0) barsByTicker.set(ticker, bars);
    }
  }));
  return barsByTicker;
}

async function resolveForceValues(tickers: string[], tradeDate: string, diagnostics?: Map<string, string[]>) {
  const now = Date.now();
  const close = Date.parse(`${tradeDate}T13:30:00+08:00`);
  const settled = now >= close + 300_000;
  const rows = new Map<string, ForceValueRow>();
  const [snapshot, closed] = await Promise.all([
    readIntradayLargeForceMonitorRows(tradeDate).catch(() => ({ rows: [] })),
    settled ? readAvailableRankingForceClose(tickers, tradeDate).catch(() => []) : Promise.resolve([]),
  ]);
  for (const row of snapshot.rows) {
    if (tickers.includes(row.ticker) && row.tradeDate === tradeDate && Number.isFinite(row.forcePct) && row.barTs <= now) {
      rows.set(row.ticker, { ...row, source: "minute-bars" });
    }
  }
  const applyClosing = (values: typeof closed) => {
    for (const row of values) if (row.tradeDate === tradeDate && tickers.includes(row.ticker) && Number.isFinite(row.forcePct)) {
      rows.set(row.ticker, { ...rows.get(row.ticker), ...row, netAmount: row.buyAmount - row.sellAmount });
    }
  };
  applyClosing(closed);
  if (settled) {
    // Live subscriptions disappear after close. Use the same verified historical
    // totals as the ranking; never label an opening-only snapshot as a full day.
    const missing = tickers.filter(ticker => !rows.has(ticker) || (rows.get(ticker)?.barTs ?? 0) < close - 120_000);
    if (missing.length) applyClosing(await backfillRankingForceClose(missing, tradeDate, { timeoutMs: 2_500 }).catch(() => []));
  } else {
    const needsBars = tickers.filter(ticker => !rows.has(ticker) || (rows.get(ticker)?.barTs ?? 0) < now - 60_000);
    // Opening one chart must not queue behind the whole-market batch endpoint.
    const barsByTicker = needsBars.length <= 3 ? new Map<string, IntradayLargeForceMinuteBar[]>() : await loadBatchBars(needsBars);
    const missing = needsBars.filter(ticker => !calculateIntradayLargeForceValue(barsByTicker.get(ticker) ?? [], tradeDate));
    await fillMissingSingleTickerBars(missing, barsByTicker, tradeDate, diagnostics);
    for (const ticker of needsBars) {
      const value = calculateIntradayLargeForceValue(barsByTicker.get(ticker) ?? [], tradeDate);
      const previous = rows.get(ticker);
      if (value && value.barTs <= now && (!previous || value.barTs >= (previous.barTs ?? 0))) {
        rows.set(ticker, { ticker, ...value, source: "minute-bars" });
      }
    }
  }
  for (const ticker of tickers) if (!rows.has(ticker)) rows.set(ticker, { ticker, tradeDate, forcePct: null, barTs: null });
  return rows;
}

export async function GET(request: NextRequest) {
  const requestedDate = request.nextUrl.searchParams.get("tradeDate");
  const tradeDate = requestedDate && /^\d{4}-\d{2}-\d{2}$/.test(requestedDate)
    ? requestedDate
    : taipeiTradeDate();
  if (request.nextUrl.searchParams.get("scope") === "market") {
    const [snapshot, progress, previous] = await Promise.all([
      readIntradayLargeForceMonitorRows(tradeDate),
      readIntradayLargeForceScanProgress(tradeDate).catch(() => null),
      readPreviousTechnicalMarketSnapshot(tradeDate).catch(() => null),
    ]);
    const previousPrices = new Map((previous?.rows ?? []).map((row) => [row.code, row.close]));
    const now = Date.now();
    const close = Date.parse(`${tradeDate}T13:30:00+08:00`);
    const liveCutoff = Math.min(now, close);
    const currentTradeDate = tradeDate === taipeiTradeDate();
    const validRows = snapshot.rows.filter((row) => {
      if (!Number.isFinite(row.forcePct) || !Number.isFinite(row.barTs) || row.barTs <= 0) return false;
      if (!Number.isFinite(row.turnoverAmount) || row.turnoverAmount <= 0) return false;
      if (!Number.isFinite(row.buyAmount) || !Number.isFinite(row.sellAmount) || row.buyAmount + row.sellAmount <= 0) return false;
      // 當日盤中只准新鮮逐筆彙總進榜；舊的開盤殘片不再顯示成 100% 或 0%。
      return !currentTradeDate || now >= close + 300_000 || row.barTs >= liveCutoff - 4 * 60_000;
    });
    return NextResponse.json({
      ok: true,
      tradeDate,
      updatedAt: snapshot.updatedAt,
      rows: validRows.map((row) => ({ ...row, ...homeworkPriceChange(row.price, previousPrices.get(row.ticker)) })),
      progress,
    }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
  }

  const tickers = requestedTickers(request.nextUrl.searchParams.get("tickers"));
  if (tickers.length === 0) return NextResponse.json({ ok: true, tradeDate, rows: [] });

  const now = Date.now();
  const diagnostics = request.nextUrl.searchParams.get("debug") === "1" ? new Map<string, string[]>() : undefined;
  const rowsByTicker = new Map<string, ForceValueRow>();
  const missing: string[] = [];
  for (const ticker of tickers) {
    const cached = valueCache.get(`${tradeDate}:${ticker}`);
    if (cached && cached.expiresAt > now) rowsByTicker.set(ticker, cached.row);
    else missing.push(ticker);
  }

  if (missing.length > 0) {
    const key = `${tradeDate}:${[...missing].sort().join(",")}`;
    let pending = pendingValues.get(key);
    if (!pending) {
      pending = resolveForceValues(missing, tradeDate, diagnostics).finally(() => pendingValues.delete(key));
      pendingValues.set(key, pending);
    }
    for (const [ticker, row] of await pending) rowsByTicker.set(ticker, row);
    for (const ticker of missing) {
      const row = rowsByTicker.get(ticker);
      // Only verified closing totals get the long TTL. Empty or partial data
      // must be retried, including on a past trade date.
      if (row?.forcePct !== null && row?.forcePct !== undefined) {
        const ttl = row.source === "historical-ticks" ? HISTORICAL_CACHE_MS : LIVE_CACHE_MS;
        valueCache.set(`${tradeDate}:${ticker}`, { expiresAt: now + ttl, row });
      }
    }
  }

  if (valueCache.size > 3_000) {
    for (const [key, cached] of valueCache) if (cached.expiresAt <= now) valueCache.delete(key);
  }
  return NextResponse.json({
    ok: true,
    tradeDate,
    updatedAt: Date.now(),
    rows: tickers.map((ticker) => rowsByTicker.get(ticker)),
    ...(diagnostics ? { diagnostics: Object.fromEntries(diagnostics) } : {}),
  }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
}
