const HUB_BASES = ["https://hanstock.xyz", "https://hanstock-production.up.railway.app"];
const ALLOWED_STATUSES = new Set(["接近突破", "突破待量", "放量突破"]);

export async function GET(request: Request) {
  const requestedStatus = new URL(request.url).searchParams.get("status")?.trim() ?? "";
  if (requestedStatus && !ALLOWED_STATUSES.has(requestedStatus)) {
    return Response.json({ ok: false, rows: [], message: "不支援的盤中三角收斂狀態" }, { status: 400 });
  }

  for (const base of HUB_BASES) {
    try {
      const source = new URL("/api/screener/triangles/intraday/latest", base);
      source.searchParams.set("limit", "300");
      if (requestedStatus) source.searchParams.set("status", requestedStatus);
      const response = await fetch(source, {
        cache: "no-store",
        headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-IntradayTriangle/1.0" },
        signal: AbortSignal.timeout(20_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as {
        status?: string;
        trade_date?: string;
        generated_at?: string;
        bucket_ts?: number;
        summary?: unknown;
        rows?: unknown[];
      };
      if (payload.status !== "ok" || !Array.isArray(payload.rows)) continue;
      return Response.json({
        ok: true,
        tradeDate: payload.trade_date,
        generatedAt: payload.generated_at,
        bucketTs: payload.bucket_ts,
        summary: payload.summary,
        rows: payload.rows,
      }, { headers: { "Cache-Control": "no-store" } });
    } catch {
      // 改試下一個正式來源。
    }
  }
  return Response.json(
    { ok: false, rows: [], message: "盤中三角收斂名單尚未產生，開盤後每 5 分鐘自動更新" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
