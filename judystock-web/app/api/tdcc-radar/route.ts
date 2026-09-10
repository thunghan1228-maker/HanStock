import { saveAndReadTdccSnapshots } from "../../../db/tdcc-radar";
import { calculateTdccWeeklyChangePp } from "../../../lib/tdcc-radar-calculation";
import { fetchLatestTdccSnapshot } from "../../../lib/tdcc-radar-source";

const REFRESH_INTERVAL_MS = 10 * 60 * 1_000;
let cache: { expiresAt: number; payload: RadarPayload } | null = null;
let inFlight: Promise<RadarPayload> | null = null;

type StoredSnapshots = Awaited<ReturnType<typeof saveAndReadTdccSnapshots>>;
type RadarPayload = ReturnType<typeof buildPayload>;

function buildPayload(stored: StoredSnapshots, updatedAt: string, source: string, sourceFormat: "csv" | "json" | "stored", stale = false) {
  const latestDate = stored.dates[0] ?? "—";
  const previousDate = stored.dates[1] ?? null;
  const latest = new Map(stored.rows.filter((row) => row.dataDate === latestDate).map((row) => [row.ticker, row.largeHolderPct]));
  const previous = new Map(stored.rows.filter((row) => row.dataDate === previousDate).map((row) => [row.ticker, row.largeHolderPct]));
  const rows = [...latest.entries()].map(([code, largeHolderPct]) => ({
    code,
    largeHolderPct,
    previousPct: previous.get(code) ?? null,
    weeklyChangePp: previous.has(code) ? calculateTdccWeeklyChangePp(largeHolderPct, previous.get(code) ?? 0) : null,
  }));
  return { ok: rows.length > 1_000, dataDate: latestDate, previousDate, baselineReady: Boolean(previousDate), updatedAt, autoRefreshMinutes: 10, rows, source, sourceFormat, stale };
}

async function refreshPayload() {
  const checkedAt = new Date().toISOString();
  try {
    const fetched = await fetchLatestTdccSnapshot();
    const currentDate = fetched.records.reduce((latest, row) => row.dataDate > latest ? row.dataDate : latest, "");
    const current = fetched.records.filter((row) => row.dataDate === currentDate);
    let stored: StoredSnapshots = { dates: [currentDate], rows: current, updatedAt: Date.now() };
    try {
      stored = await saveAndReadTdccSnapshots(current);
    } catch {
      // 本機沒有 D1 時仍顯示本週資料；正式站會保存本週與前週快照。
    }
    return buildPayload(stored, checkedAt, fetched.source, fetched.sourceFormat);
  } catch (error) {
    try {
      const stored = await saveAndReadTdccSnapshots([]);
      if (stored.dates.length) return buildPayload(stored, checkedAt, "stored", "stored", true);
    } catch {
      // 沒有可用的正式站快照時才回傳錯誤。
    }
    throw error;
  }
}

async function readStoredPayload() {
  try {
    const stored = await saveAndReadTdccSnapshots([]);
    if (!stored.dates.length || !stored.rows.length) return null;
    const updatedAt = stored.updatedAt ? new Date(stored.updatedAt).toISOString() : new Date().toISOString();
    return buildPayload(stored, updatedAt, "stored", "stored");
  } catch {
    return null;
  }
}

async function getPayload(force: boolean) {
  if (!force && cache && cache.expiresAt > Date.now()) return cache.payload;
  if (!force) {
    const stored = await readStoredPayload();
    if (stored) {
      cache = { expiresAt: Date.now() + REFRESH_INTERVAL_MS, payload: stored };
      return stored;
    }
  }
  if (!inFlight) {
    inFlight = refreshPayload().then((payload) => {
      cache = { expiresAt: Date.now() + REFRESH_INTERVAL_MS, payload };
      return payload;
    }).finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const force = params.get("refresh") === "1" || params.get("auto") === "1";
  try {
    const cacheControl = force ? "no-store" : "public, max-age=60, s-maxage=600, stale-while-revalidate=21600";
    return Response.json(await getPayload(force), { headers: { "Cache-Control": cacheControl } });
  } catch (error) {
    return Response.json({ ok: false, dataDate: "—", previousDate: null, baselineReady: false, updatedAt: new Date().toISOString(), autoRefreshMinutes: 10, rows: [], error: error instanceof Error ? error.message : "tdcc_unavailable" }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}
