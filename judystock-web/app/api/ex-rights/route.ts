import exRightsSnapshot from "../../data/ex-rights-snapshot.json";

type JsonRow = Record<string, unknown>;

type ExRightRow = {
  code: string;
  name: string;
  date: string;
  type: string;
  market: "上市" | "上櫃";
};

function rocDate(value: unknown) {
  const text = String(value ?? "").replace(/\D/g, "");
  if (text.length !== 7) return "";
  return `${Number(text.slice(0, 3)) + 1911}/${text.slice(3, 5)}/${text.slice(5, 7)}`;
}

function taipeiToday() {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}/${value.month}/${value.day}`;
}

async function fetchJson(url: string) {
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/3.2" },
    signal: AbortSignal.timeout(35_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`ex_rights_http_${response.status}`);
  const payload = await response.json() as unknown;
  if (!Array.isArray(payload)) throw new Error("ex_rights_payload_invalid");
  return payload as JsonRow[];
}

export async function GET() {
  const [twseResult, tpexResult] = await Promise.allSettled([
    fetchJson("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"),
    fetchJson("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"),
  ]);
  const today = taipeiToday();
  const rows: ExRightRow[] = [];

  if (twseResult.status === "fulfilled") {
    twseResult.value.forEach((row) => {
      const date = rocDate(row.Date);
      const code = String(row.Code ?? "").trim().toUpperCase();
      if (!code || !date || date < today) return;
      rows.push({ code, name: String(row.Name ?? "").trim(), date, type: String(row.Exdividend ?? "除權息").trim() || "除權息", market: "上市" });
    });
  }
  if (tpexResult.status === "fulfilled") {
    tpexResult.value.forEach((row) => {
      const date = rocDate(row.ExRrightsExDividendDate);
      const code = String(row.SecuritiesCompanyCode ?? "").trim().toUpperCase();
      if (!code || !date || date < today) return;
      rows.push({ code, name: String(row.CompanyName ?? "").trim(), date, type: String(row.ExRrightsExDividend ?? "除權息").trim() || "除權息", market: "上櫃" });
    });
  }

  const verifiedSnapshot = exRightsSnapshot as { generatedAt: string; rows: ExRightRow[] };
  if (twseResult.status === "rejected") {
    rows.push(...verifiedSnapshot.rows.filter((row) => row.market === "上市" && row.date >= today));
  }
  if (tpexResult.status === "rejected") {
    rows.push(...verifiedSnapshot.rows.filter((row) => row.market === "上櫃" && row.date >= today));
  }

  rows.sort((a, b) => a.date.localeCompare(b.date) || a.code.localeCompare(b.code));
  const nearest = new Map<string, ExRightRow>();
  rows.forEach((row) => { if (!nearest.has(row.code)) nearest.set(row.code, row); });
  const result = [...nearest.values()];
  return Response.json(
    {
      ok: rows.length > 0,
      updatedAt: new Date().toISOString(),
      rows: result,
      sources: {
        twse: twseResult.status === "fulfilled" ? "official-live" : "verified-snapshot",
        tpex: tpexResult.status === "fulfilled" ? "official-live" : "verified-snapshot",
      },
      snapshotGeneratedAt: verifiedSnapshot.generatedAt,
    },
    {
      status: rows.length > 0 ? 200 : 503,
      headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=1800" },
    },
  );
}
