// Railway 是 VCP 盤後掃描的產生端，優先取用；主站只作備援，避免主站
// 邊緣快取尚未更新時回傳較舊的候選名單。
const HUB_BASES = ["https://hanstock-production.up.railway.app", "https://hanstock.xyz"];

export async function GET() {
  for (const base of HUB_BASES) {
    try {
      const response = await fetch(new URL("/api/screener/vcp/latest?limit=500", base), {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.2" },
        signal: AbortSignal.timeout(12_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as {
        status?: string;
        generated_at?: string;
        summary?: unknown;
        rows?: Array<Record<string, unknown>>;
      };
      if (payload.status !== "ok" || !Array.isArray(payload.rows)) continue;
      return Response.json({
        ok: true,
        updatedAt: payload.generated_at,
        summary: payload.summary,
        rows: payload.rows,
      }, { headers: { "Cache-Control": "no-store" } });
    } catch {
      // 改試下一個正式來源。
    }
  }
  return Response.json(
    { ok: false, rows: [], message: "VCP 掃描名單尚未產生" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
