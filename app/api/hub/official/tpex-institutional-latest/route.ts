const SNAPSHOT_URL = "https://raw.githubusercontent.com/thunghan1228-maker/HanStock/main/data/tpex-institutional-latest.json";
const OFFICIAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading";

function normalizeOpenApiDate(value: unknown) {
  const text = String(value ?? "").trim();
  if (!/^\d{8}$/.test(text)) return null;
  return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
}

function strip(value: unknown) {
  return String(value ?? "").replace(/<[^>]*>/g, "").replace(/&nbsp;|&#160;/gi, " ").trim();
}

function number(value: unknown) {
  const text = strip(value).replace(/[−－]/g, "-").replace(/,|\s/g, "");
  const parsed = Number(text || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizeOpenApi(payload: unknown) {
  if (!Array.isArray(payload)) return [];
  const foreign = "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference";
  const trust = "SecuritiesInvestmentTrustCompanies-Difference";
  const dealer = "Dealers-Difference";
  return payload.flatMap((item) => {
    const row = item as Record<string, unknown>;
    const date = normalizeOpenApiDate(row.Date);
    const code = strip(row.SecuritiesCompanyCode).toUpperCase();
    if (!date || !/^\d{4}$/.test(code) || !(foreign in row) || !(trust in row) || !(dealer in row)) return [];
    return [{ code, name: strip(row.CompanyName), date, foreign: number(row[foreign]), trust: number(row[trust]), dealer: number(row[dealer]), hedge: 0 }];
  });
}

export async function GET() {
  for (const source of [SNAPSHOT_URL, OFFICIAL_URL]) {
    try {
      const response = await fetch(source, { headers: { Accept: "application/json", "User-Agent": "HanStock-Tpex-Hub/1.0" }, cache: "no-store", signal: AbortSignal.timeout(10_000) });
      if (!response.ok) continue;
      const payload = await response.json() as unknown;
      if (source === SNAPSHOT_URL && payload && typeof payload === "object" && Array.isArray((payload as { rows?: unknown[] }).rows)) {
        return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
      }
      const rows = normalizeOpenApi(payload);
      const latestDate = rows.map((row) => row.date).sort().at(-1);
      const latestRows = latestDate ? rows.filter((row) => row.date === latestDate) : [];
      if (latestDate && latestRows.length >= 600) return Response.json({ status: "ok", date: latestDate, rows: latestRows }, { headers: { "Cache-Control": "no-store" } });
    } catch {
      // Try the next authoritative source.
    }
  }
  return Response.json({ status: "unavailable", rows: [] }, { status: 503, headers: { "Cache-Control": "no-store" } });
}
