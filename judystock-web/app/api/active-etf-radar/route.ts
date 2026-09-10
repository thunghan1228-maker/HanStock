const hubBases = () => [...new Set([
  "https://hanstock.xyz",
  typeof process !== "undefined" ? process.env.HANSTOCK_HUB_URL : undefined,
  "https://hanstock-production.up.railway.app",
].filter((value): value is string => Boolean(value)))];

export async function GET(request: Request) {
  const search = new URL(request.url).searchParams;
  const days = Math.min(10, Math.max(1, Number(search.get("days") ?? 5) || 5));
  const date = search.get("date")?.trim() ?? "";
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return Response.json({ ok: false, error: "date_invalid" }, { status: 400 });
  }
  const query = new URLSearchParams({ days: String(days) });
  if (date) query.set("date", date);
  for (const base of hubBases()) {
    try {
      const response = await fetch(`${base.replace(/\/$/, "")}/api/hub/active-etf-radar?${query}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(55_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as Record<string, unknown>;
      if (payload.ok === true && Array.isArray(payload.rows)) {
        return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
      }
    } catch {
      // Try the next configured Hub address.
    }
  }
  return Response.json(
    { ok: false, error: "active_etf_radar_pending" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}
