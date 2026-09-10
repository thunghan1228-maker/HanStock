import { getBattleAdmin, forbiddenJson } from "../../../admin/admin-auth";
import { readRecentAdminAudit, readStorageSummary } from "../../../../db/admin";

type JsonRecord = Record<string, unknown>;

const statusTargets = [
  { key: "live-ranking", label: "盤中個股與族群排行", path: "/api/live-ranking" },
  { key: "market-ranking", label: "盤後法人籌碼排行", path: "/api/market-ranking" },
  { key: "daytrade-brokers", label: "疑似隔日沖大單", path: "/api/daytrade-brokers" },
  { key: "daytrade-signals", label: "隔日沖盤中訊號", path: "/api/daytrade-early-sell" },
  { key: "triangles", label: "三角收斂選股", path: "/api/triangles" },
] as const;

function rowCount(payload: JsonRecord) {
  if (Array.isArray(payload.rows)) return payload.rows.length;
  if (Array.isArray(payload.signals)) return payload.signals.length;
  if (payload.rankings && typeof payload.rankings === "object") {
    return JSON.stringify(payload.rankings).match(/"rank"/g)?.length ?? 0;
  }
  return null;
}

function dataDate(payload: JsonRecord) {
  return String(
    payload.dataDate ?? payload.tradeDate ?? payload.sourceDate ?? payload.generatedAt ?? payload.fetchedAt ?? "—",
  );
}

async function inspectTarget(origin: string, target: typeof statusTargets[number]) {
  const startedAt = Date.now();
  try {
    const response = await fetch(new URL(target.path, origin), {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Admin/1.0" },
      signal: AbortSignal.timeout(target.key === "market-ranking" ? 55_000 : 25_000),
    });
    const payload = await response.json() as JsonRecord;
    return {
      ...target,
      ok: response.ok && payload.ok !== false,
      status: response.status,
      dataDate: dataDate(payload),
      rowCount: rowCount(payload),
      elapsedMs: Date.now() - startedAt,
      message: String(payload.message ?? payload.error ?? payload.scanStatus ?? "資料讀取正常"),
    };
  } catch (error) {
    return {
      ...target,
      ok: false,
      status: 0,
      dataDate: "—",
      rowCount: null,
      elapsedMs: Date.now() - startedAt,
      message: error instanceof Error ? error.message : "檢查失敗",
    };
  }
}

export async function GET(request: Request) {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  const origin = new URL(request.url).origin;
  const [services, storage, audit] = await Promise.all([
    Promise.all(statusTargets.map((target) => inspectTarget(origin, target))),
    readStorageSummary(),
    readRecentAdminAudit(20),
  ]);
  return Response.json(
    { ok: true, checkedAt: new Date().toISOString(), services, storage, audit },
    { headers: { "Cache-Control": "no-store" } },
  );
}
