import { getBattleAdmin, forbiddenJson, sameOriginRequest } from "../../../admin/admin-auth";
import { writeAdminAudit } from "../../../../db/admin";

const refreshTargets = {
  "live-ranking": "/api/live-ranking",
  "market-ranking": "/api/market-ranking",
  "daytrade-brokers": "/api/daytrade-brokers?force=1",
  "daytrade-signals": "/api/daytrade-early-sell",
  triangles: "/api/triangles",
} as const;

type RefreshTarget = keyof typeof refreshTargets;

async function refreshOne(origin: string, key: RefreshTarget) {
  const delimiter = refreshTargets[key].includes("?") ? "&" : "?";
  const url = `${refreshTargets[key]}${delimiter}admin_refresh=${Date.now()}`;
  const startedAt = Date.now();
  try {
    const response = await fetch(new URL(url, origin), {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Admin/1.0" },
      signal: AbortSignal.timeout(key === "market-ranking" ? 55_000 : 30_000),
    });
    const payload = await response.json() as Record<string, unknown>;
    return {
      key,
      ok: response.ok && payload.ok !== false,
      status: response.status,
      elapsedMs: Date.now() - startedAt,
      dataDate: payload.dataDate ?? payload.tradeDate ?? payload.generatedAt ?? payload.fetchedAt ?? null,
      message: payload.message ?? payload.error ?? "更新完成",
    };
  } catch (error) {
    return {
      key,
      ok: false,
      status: 0,
      elapsedMs: Date.now() - startedAt,
      dataDate: null,
      message: error instanceof Error ? error.message : "更新失敗",
    };
  }
}

export async function POST(request: Request) {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "資料更新請求來源不正確" }, { status: 403 });
  }
  const input = await request.json().catch(() => ({})) as { target?: string };
  const requested = input.target === "all"
    ? Object.keys(refreshTargets) as RefreshTarget[]
    : [input.target].filter((key): key is RefreshTarget => Boolean(key && key in refreshTargets));
  if (requested.length === 0) {
    return Response.json({ ok: false, message: "未知的更新項目" }, { status: 400 });
  }
  const origin = new URL(request.url).origin;
  const results = await Promise.all(requested.map((key) => refreshOne(origin, key)));
  await writeAdminAudit(admin.username, "data.refresh", input.target ?? "unknown", results);
  return Response.json(
    { ok: results.every((result) => result.ok), results },
    { headers: { "Cache-Control": "no-store" } },
  );
}
