import { NextRequest, NextResponse } from "next/server";
import { saveFullMarketLargeFlowBaselines } from "../../../db/full-market-large-flow";
import {
  readAuthoritativeGroupNetAmount,
  readAuthoritativeGroupTurnoverAmount,
  type GroupDaytradeFlowRow,
} from "../../../lib/group-member-flow";

type FlowStatus = "ready" | "force_pending" | "turnover_pending";
type HubPayload = { data_date?: string; rows?: GroupDaytradeFlowRow[] };

const HUB_BASES = ["https://hanstock-production.up.railway.app"];
const MAX_TICKERS = 60;

function parseTickers(value: string | null) {
  return [...new Set((value ?? "").split(",").map((ticker) => ticker.trim().toUpperCase()))]
    .filter((ticker) => /^\d{4}$/.test(ticker))
    .slice(0, MAX_TICKERS);
}

async function fetchFullGroupFlow(tickers: string[]) {
  for (const base of HUB_BASES) {
    try {
      const endpoint = new URL("/api/hub/daytrade-flow-ranking", base);
      endpoint.searchParams.set("codes", tickers.join(","));
      endpoint.searchParams.set("scan_limit", String(tickers.length));
      endpoint.searchParams.set("limit", String(tickers.length));
      endpoint.searchParams.set("include_all", "true");
      const response = await fetch(endpoint, {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Group-Flow/3.0" },
        signal: AbortSignal.timeout(45_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as HubPayload;
      if (!payload.data_date || !Array.isArray(payload.rows)) continue;
      return { dataDate: payload.data_date, rows: payload.rows };
    } catch {
      // 依序改走 Railway 直连；两条正式路径都失败时保留待回补，不填假值。
    }
  }
  return { dataDate: null, rows: [] as GroupDaytradeFlowRow[] };
}

export async function GET(request: NextRequest) {
  const tickers = parseTickers(request.nextUrl.searchParams.get("tickers"));
  if (tickers.length === 0) {
    return NextResponse.json({ ok: false, error: "tickers_required", rows: [] }, { status: 400 });
  }

  // 大单净额与成交金额必须来自同一份后台逐笔汇总，避免柜买中心直连较慢时
  // 只拿到上市成交额、却把完整的上柜逐笔数据误标成「成交额待回补」。
  const fetched = await fetchFullGroupFlow(tickers);
  const sourceByTicker = new Map(fetched.rows.map((row) => [String(row.ticker ?? "").toUpperCase(), row]));
  const rows = tickers.map((ticker) => {
    const source = sourceByTicker.get(ticker);
    const sourceDate = String(source?.trade_date ?? "");
    const turnoverAmount = readAuthoritativeGroupTurnoverAmount(source);
    const netLargeAmount = readAuthoritativeGroupNetAmount(source);
    if (!turnoverAmount) {
      return { ticker, dataDate: fetched.dataDate, netLargeAmount: null, turnoverAmount: null, available: false, status: "turnover_pending" as FlowStatus };
    }
    if (netLargeAmount === null || !fetched.dataDate || sourceDate !== fetched.dataDate) {
      return { ticker, dataDate: fetched.dataDate, netLargeAmount: null, turnoverAmount, available: false, status: "force_pending" as FlowStatus };
    }
    return { ticker, dataDate: fetched.dataDate, netLargeAmount, turnoverAmount, available: true, status: "ready" as FlowStatus };
  });

  const successful = rows.flatMap((row) => {
    const source = sourceByTicker.get(row.ticker);
    const closePrice = Number(source?.close_price);
    return row.available && row.netLargeAmount !== null && Number.isFinite(closePrice) && closePrice > 0 && fetched.dataDate ? [{
      ticker: row.ticker,
      name: String(source?.name ?? row.ticker),
      tradeDate: fetched.dataDate,
      netLargeAmount: row.netLargeAmount,
      turnoverAmount: row.turnoverAmount ?? 0,
      closePrice,
    }] : [];
  });
  await saveFullMarketLargeFlowBaselines(successful).catch(() => undefined);

  return NextResponse.json({
    ok: rows.some((row) => row.available),
    dataDate: fetched.dataDate,
    requestedCount: tickers.length,
    completedCount: rows.filter((row) => row.available).length,
    rows,
  }, { headers: { "Cache-Control": "private, no-store, max-age=0" } });
}
