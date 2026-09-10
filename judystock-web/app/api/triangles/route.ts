const HUB_BASES = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];

export async function GET() {
  for (const base of HUB_BASES) {
    try {
      const response = await fetch(new URL("/api/screener/triangles/latest?limit=100", base), {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.2" },
        signal: AbortSignal.timeout(12_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as {
        status?: string;
        generated_at?: string;
        updated_at?: string;
        data_date?: string;
        data_time?: string;
        sample_at?: string;
        source_date?: string;
        trade_date?: string;
        summary?: unknown;
        rows?: Array<Record<string, unknown>>;
      };
      if (payload.status !== "ok" || !Array.isArray(payload.rows)) continue;
      const firstRow = payload.rows[0];
      const sampleAt = payload.sample_at
        ?? payload.data_time
        ?? payload.data_date
        ?? payload.source_date
        ?? payload.trade_date
        ?? (typeof firstRow?.sample_at === "string" ? firstRow.sample_at : undefined)
        ?? (typeof firstRow?.data_time === "string" ? firstRow.data_time : undefined)
        ?? (typeof firstRow?.data_date === "string" ? firstRow.data_date : undefined)
        ?? (typeof firstRow?.source_date === "string" ? firstRow.source_date : undefined)
        ?? (typeof firstRow?.trade_date === "string" ? firstRow.trade_date : undefined);
      return Response.json(
        {
          ok: true,
          sampleAt,
          updatedAt: payload.updated_at ?? payload.generated_at,
          generatedAt: payload.generated_at,
          summary: payload.summary,
          rows: payload.rows,
        },
        { headers: { "Cache-Control": "no-store" } },
      );
    } catch {
      // 改試下一個正式來源。
    }
  }
  return Response.json(
    { ok: false, rows: [], message: "三角收斂名單暫時無法取得" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
