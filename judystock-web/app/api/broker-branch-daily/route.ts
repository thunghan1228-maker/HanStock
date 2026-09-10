import { readLatestBrokerBranchDaily, saveBrokerBranchDaily, type BrokerBranchDailyRecord } from "../../../db/broker-branch-daily";

function response(rows: BrokerBranchDailyRecord[], source: "hub" | "stored" | "pending", message?: string) {
  return Response.json({
    ok: rows.length > 0,
    rows,
    tradeDate: rows[0]?.tradeDate ?? null,
    updatedAt: new Date().toISOString(),
    source,
    message: message ?? (rows.length ? undefined : "正式券商分點日資料尚未寫入"),
  }, { headers: { "Cache-Control": "no-store" } });
}

function validRow(row: BrokerBranchDailyRecord) {
  return /^[0-9A-Z]{2,12}$/.test(row.ticker)
    && /^\d{4}\/\d{2}\/\d{2}$/.test(row.tradeDate)
    && Number.isFinite(row.netAmount)
    && (row.netLots == null || Number.isFinite(row.netLots))
    && Number.isFinite(row.concentration)
    && Number.isFinite(row.activeBranches);
}

export async function GET() {
  const configuredHub = typeof process !== "undefined" ? process.env.HANSTOCK_HUB_URL : undefined;
  const token = typeof process !== "undefined" ? process.env.HANSTOCK_SYNC_TOKEN : undefined;
  const hubBases = [...new Set(["https://hanstock.xyz", configuredHub, "https://hanstock-production.up.railway.app"].filter((value): value is string => Boolean(value)))];
  let pendingMessage = "資料中心尚無完整分點日資料";
  for (const hubBase of hubBases) {
    try {
      const headers = token ? { "x-hanstock-sync-token": token } : undefined;
      const hubResponse = await fetch(`${hubBase.replace(/\/$/, "")}/api/hub/broker-branch-daily`, { headers, cache: "no-store", signal: AbortSignal.timeout(18_000) });
      if (!hubResponse.ok) throw new Error("broker_branch_daily_hub_unavailable");
      const payload = await hubResponse.json() as { rows?: BrokerBranchDailyRecord[]; complete?: boolean };
      const rows = Array.isArray(payload.rows) ? payload.rows.filter(validRow) : [];
      if (rows.length) {
        // 儲存層短暫失敗時仍把已取得的正式資料交給畫面，避免成功資料反而消失。
        try { await saveBrokerBranchDaily(rows); } catch { /* 下一輪再保存。 */ }
        return response(rows, "hub");
      }
      if (payload.complete === false) pendingMessage = "資料中心正在回補最新交易日分點資料";
    } catch {
      // Hub 暫時不可用時，改讀最後一次成功保存的正式日資料。
    }
  }
  try {
    const stored = await readLatestBrokerBranchDaily();
    if (stored.length) return response(stored, "stored", "資料中心暫時無法連線，顯示最近交易日分點資料");
  } catch {
    // 首次部署或本機無 D1 時維持明確等待狀態。
  }
  return response([], "pending", pendingMessage);
}
