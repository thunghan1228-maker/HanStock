import { readTechnicalMarketIndicators } from "../../../db/technical-market-indicators";
import { BLACK_DRAGON_MODEL_VERSION, BLACK_DRAGON_RECENT_SESSIONS, buildBlackDragonRows, type BlackDragonSourceRow } from "../../../lib/black-dragon";
import { isListedOrOtcStockCode } from "../../../lib/daily-strategy-signals";
import { parseHanStockOfficialPrimaryGroupMap } from "../../../lib/stock-primary-group";
import stockGroupsSource from "../../../data/stock_groups.py?raw";
import marketRankingSnapshot from "../../data/market-ranking-snapshot.json";

type StoredBlackDragonPayload = {
  blackDragonModelVersion?: unknown;
  blackDragonDataDate?: unknown;
  blackDragonSignal?: unknown;
};

const stockNames = new Map((marketRankingSnapshot.rows as Array<{ code?: string; name?: string }>).flatMap((row) => {
  const code = String(row.code ?? "").trim().toUpperCase();
  const name = String(row.name ?? "").trim().replace(/\*$/, "");
  return code && name ? [[code, name] as const] : [];
}));
const officialPrimaryGroupByCode = parseHanStockOfficialPrimaryGroupMap(stockGroupsSource);

function validSignal(value: unknown): value is BlackDragonSourceRow {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<BlackDragonSourceRow>;
  return typeof row.code === "string" && (row.market === "twse" || row.market === "tpex");
}

export async function GET() {
  const indicators = (await readTechnicalMarketIndicators().catch(() => []))
    .filter((indicator) => isListedOrOtcStockCode(indicator.code) && officialPrimaryGroupByCode.has(indicator.code));
  const ready = indicators.filter((indicator) => (indicator.payload as StoredBlackDragonPayload).blackDragonModelVersion === BLACK_DRAGON_MODEL_VERSION);
  const rows = buildBlackDragonRows(ready.flatMap((indicator): BlackDragonSourceRow[] => {
    const signal = (indicator.payload as StoredBlackDragonPayload).blackDragonSignal;
    return validSignal(signal) ? [{
      ...signal,
      code: indicator.code,
      name: stockNames.get(indicator.code) ?? signal.name,
      groupName: officialPrimaryGroupByCode.get(indicator.code),
      market: indicator.market,
    }] : [];
  }));
  const completed = { twse: 0, tpex: 0 };
  const totals = { twse: 0, tpex: 0 };
  indicators.forEach((indicator) => {
    totals[indicator.market] += 1;
    if ((indicator.payload as StoredBlackDragonPayload).blackDragonModelVersion === BLACK_DRAGON_MODEL_VERSION) completed[indicator.market] += 1;
  });
  const dataDate = ready.map((indicator) => String((indicator.payload as StoredBlackDragonPayload).blackDragonDataDate ?? ""))
    .filter(Boolean).sort().at(-1) ?? "—";
  return Response.json({
    ok: ready.length > 0,
    modelVersion: BLACK_DRAGON_MODEL_VERSION,
    dataDate,
    updatedAt: ready.map((indicator) => indicator.updatedAt).filter(Boolean).sort().at(-1) ?? new Date().toISOString(),
    recentSessions: BLACK_DRAGON_RECENT_SESSIONS,
    rows,
    coverage: {
      completed: ready.length,
      total: indicators.length,
      missing: Math.max(0, indicators.length - ready.length),
      byMarket: {
        twse: { completed: completed.twse, total: totals.twse },
        tpex: { completed: completed.tpex, total: totals.tpex },
      },
    },
  }, { headers: { "Cache-Control": "private, no-store" } });
}
