const hubBases = () => [...new Set([
  "https://hanstock.xyz",
  typeof process !== "undefined" ? process.env.HANSTOCK_HUB_URL : undefined,
  "https://hanstock-production.up.railway.app",
].filter((value): value is string => Boolean(value)))];

export async function GET(request: Request) {
  const ticker = new URL(request.url).searchParams.get("ticker")?.trim().toUpperCase() ?? "";
  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) return Response.json({ ok: false, error: "ticker_invalid" }, { status: 400 });
  for (const base of hubBases()) {
    try {
      const response = await fetch(`${base.replace(/\/$/, "")}/api/hub/active-etf-flow?ticker=${encodeURIComponent(ticker)}&days=5`, {
        cache: "no-store",
        signal: AbortSignal.timeout(55_000),
      });
      if (!response.ok) continue;
      const payload = await response.json() as Record<string, unknown>;
      if (payload.ok === true) return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
    } catch {
      // Try the next configured Hub address.
    }
  }
  return Response.json({ ok: false, error: "active_etf_flow_pending" }, { status: 503, headers: { "Cache-Control": "no-store" } });
}
