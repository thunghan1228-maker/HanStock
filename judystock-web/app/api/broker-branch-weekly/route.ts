import { readLatestBrokerBranchWeekly, saveBrokerBranchWeekly, type BrokerBranchWeeklyRecord } from "../../../db/broker-branch-weekly";

function response(rows: BrokerBranchWeeklyRecord[], source: "hub" | "stored" | "pending", message?: string) {
  return Response.json({
    ok: rows.length > 0,
    rows,
    weekEndDate: rows[0]?.weekEndDate ?? null,
    updatedAt: new Date().toISOString(),
    source,
    message: message ?? (rows.length ? undefined : "正式券商分點週資料尚未寫入"),
  }, { headers: { "Cache-Control": "no-store" } });
}

export async function GET() {
  // The Hub is the only writer of official branch identity. Tick-derived main
  // force data is deliberately never substituted here.
  const configuredHub = typeof process !== "undefined" ? process.env.HANSTOCK_HUB_URL : undefined;
  const token = typeof process !== "undefined" ? process.env.HANSTOCK_SYNC_TOKEN : undefined;
  // hanstock.xyz is the canonical Railway service. Keep it first so a stale
  // legacy Railway hostname cannot return `complete: false` and mask the
  // finished v2 five-day dataset.
  const hubBases = [...new Set(["https://hanstock.xyz", configuredHub, "https://hanstock-production.up.railway.app"].filter((value): value is string => Boolean(value)))];
  for (const hubBase of hubBases) {
    try {
      const headers = token ? { "x-hanstock-sync-token": token } : undefined;
      const hubResponse = await fetch(`${hubBase.replace(/\/$/, "")}/api/hub/broker-branch-weekly`, { headers, cache: "no-store", signal: AbortSignal.timeout(12_000) });
      if (!hubResponse.ok) throw new Error("broker_branch_hub_unavailable");
      const payload = await hubResponse.json() as { rows?: BrokerBranchWeeklyRecord[]; complete?: boolean };
      const rows = Array.isArray(payload.rows) ? payload.rows.filter((row) => /^[0-9A-Z]{2,12}$/.test(row.ticker) && /^\d{4}\/\d{2}\/\d{2}$/.test(row.weekEndDate)) : [];
      if (rows.length) {
        await saveBrokerBranchWeekly(rows);
        return response(rows, "hub");
      }
      // 資料中心已明確回報正在重算時，立刻顯示等待，不再多等另一個
      // mirror，也不拿 D1 內上一版公式的舊資料冒充最新分數。
      if (payload.complete === false) return response([], "pending", "資料中心正在重算五日分點週資料");
    } catch {
      // Hub 暫時不可用時，改讀最後一次成功保存的正式週資料。
    }
  }
  try {
    const stored = await readLatestBrokerBranchWeekly();
    if (stored.length) return response(stored, "stored", "資料中心暫時無法連線，顯示最近週資料");
  } catch {
    // 本機預覽或首次部署尚無 D1 資料時，回傳明確等待狀態。
  }
  return response([], "pending", "資料中心尚無完整分點週資料");
}

export async function POST(request: Request) {
  try {
    const configuredToken = typeof process !== "undefined" ? process.env.HANSTOCK_SYNC_TOKEN : undefined;
    const suppliedToken = request.headers.get("x-hanstock-sync-token");
    // Never permit the public browser to insert broker-branch values. In local
    // development a missing token keeps this endpoint closed as well.
    if (!configuredToken || suppliedToken !== configuredToken) return Response.json({ ok: false, error: "sync_unauthorized" }, { status: 401 });
    const body = await request.json() as { rows?: BrokerBranchWeeklyRecord[] };
    const rows = Array.isArray(body.rows) ? body.rows.filter((r) => /^[0-9A-Z]{2,12}$/.test(r.ticker) && /^\d{4}\/\d{2}\/\d{2}$/.test(r.weekEndDate)) : [];
    if (!rows.length) return Response.json({ ok: false, error: "rows_required" }, { status: 422 });
    await saveBrokerBranchWeekly(rows);
    return Response.json({ ok: true, saved: rows.length });
  } catch (error) { return Response.json({ ok: false, error: error instanceof Error ? error.message : "broker_branch_save_failed" }, { status: 503 }); }
}
