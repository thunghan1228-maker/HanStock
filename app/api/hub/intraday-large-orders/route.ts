import { readEarlySellSignals } from "../../../../db/early-sell-history";
import { readIntradayLargeForceScanProgress } from "../../../../db/intraday-large-force-scan";
import { taipeiTradeDate } from "../../../../lib/early-sell-signals";

const INSTANT_KINDS = ["instantLargeBuy", "instantLargeSell"] as const;

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const limit = Math.max(1, Math.min(5000, Math.trunc(Number(params.get("limit") ?? 5000) || 5000)));
  const requestedDate = params.get("trade_date")?.trim() || taipeiTradeDate();
  try {
    const signals = await readEarlySellSignals({
      tradeDate: requestedDate,
      kinds: [...INSTANT_KINDS],
      limit,
    });
    const progress = await readIntradayLargeForceScanProgress(requestedDate).catch(() => null);
    return Response.json({
      status: "ok",
      tradeDate: requestedDate,
      signals,
      collector: progress ? {
        tradeDate: progress.tradeDate,
        status: progress.status,
        processed: progress.processed,
        total: progress.total,
        candidateCount: progress.signalCount,
        updatedAt: progress.updatedAt,
      } : {
        tradeDate: requestedDate,
        status: "idle",
        processed: 0,
        total: 0,
        candidateCount: signals.length,
        updatedAt: 0,
      },
    }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ status: "ok", tradeDate: requestedDate, signals: [], collector: null, storageUnavailable: true }, { headers: { "Cache-Control": "no-store" } });
  }
}
