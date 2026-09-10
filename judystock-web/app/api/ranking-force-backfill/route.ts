import { backfillRankingForceClose } from "../../../lib/ranking-force-backfill";

export async function POST(request: Request) {
  try {
    const body = await request.json() as { tradeDate?: string; tickers?: unknown[] };
    const date = String(body.tradeDate ?? "");
    const close = Date.parse(`${date}T13:30:00+08:00`);
    const tickers = [...new Set((Array.isArray(body.tickers) ? body.tickers : []).map(String).filter(ticker => /^[1-9]\d{3}$/.test(ticker)))];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(close) || close + 300_000 > Date.now() || !tickers.length || tickers.length > 25) return Response.json({ ok: false, error: "invalid_backfill_request" }, { status: 400 });
    const rows = await backfillRankingForceClose(tickers, date);
    return Response.json({ ok: true, tradeDate: date, requested: tickers.length, completed: rows.length, rows }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ ok: false, error: "history_backfill_unavailable" }, { status: 502 });
  }
}
