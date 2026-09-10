import { timedSingleFlight } from "./timed-single-flight";

type ConfiguredGroup = {
  name?: string;
  members?: Array<{ code?: string; name?: string }>;
};

export type QuoteRow = {
  code?: string;
  date?: string;
  price?: number | null;
  changePct?: number | null;
};

type TrpcPayload<T> = { result?: { data?: { json?: T } } };
export type GroupMember = { code: string; name: string };
export type GroupResult = { name: string; avgChange: number; members: Array<GroupMember & { changePct: number; price: number | null }> };

const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF"]);
const MINIMUM_CONFIGURED_GROUP_COVERAGE = 60;


export const loadConfiguredGroups = timedSingleFlight(5 * 60_000, async () => {
  const response = await fetch("https://www.hanstock.xyz/api/trpc/stocks.groups", {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Focus/2.0" },
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`configured-groups-${response.status}`);
  const payload = await response.json() as TrpcPayload<ConfiguredGroup[]>;
  const rows = payload.result?.data?.json;
  if (!Array.isArray(rows) || rows.length < 60) throw new Error("configured-groups-malformed");

  const groups = new Map<string, GroupMember[]>();
  for (const row of rows) {
    const name = row.name?.trim();
    if (!name || EXCLUDED_GROUPS.has(name) || !Array.isArray(row.members)) continue;
    const members = row.members.flatMap((member) => {
      const code = String(member.code ?? "").trim();
      const stockName = String(member.name ?? "").trim().replace(/\*$/, "");
      return /^\d{4}$/.test(code) && !code.startsWith("00") && stockName ? [{ code, name: stockName }] : [];
    });
    if (members.length > 0) groups.set(name, members);
  }
  // 後台族群名單盤後可能短暫增減一群；來源仍是同一份正式 67 族群設定，
  // 不應因目前有效群數為 67 就讓整份排行中斷。只有嚴重缺漏才拒絕。
  if (groups.size < MINIMUM_CONFIGURED_GROUP_COVERAGE) throw new Error(`configured-groups-incomplete-${groups.size}`);
  return groups;
});

async function loadQuoteBatch(tickers: string[]) {
  const input = encodeURIComponent(JSON.stringify({ json: { tickers } }));
  const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.liveQuotes?input=${input}`, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Focus/2.0" },
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`live-quotes-${response.status}`);
  const payload = await response.json() as TrpcPayload<{ fetchedAt?: string; priceType?: string; rows?: QuoteRow[] }>;
  const data = payload.result?.data?.json;
  if (!data || !Array.isArray(data.rows)) throw new Error("live-quotes-malformed");
  return data;
}

async function loadLatestQuotes(groups: Map<string, GroupMember[]>) {
  const codes = [...new Set([...groups.values()].flatMap((members) => members.map((member) => member.code)))];
  const batches = Array.from({ length: Math.ceil(codes.length / 50) }, (_, index) => codes.slice(index * 50, index * 50 + 50));
  const settled = await Promise.allSettled(batches.map(async (batch) => {
    try {
      return await loadQuoteBatch(batch);
    } catch {
      return loadQuoteBatch(batch);
    }
  }));
  const fulfilled = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const rows = fulfilled.flatMap((result) => result.rows ?? []).filter((row): row is QuoteRow & { code: string; changePct: number } =>
    Boolean(row.code && typeof row.changePct === "number" && Number.isFinite(row.changePct)),
  );
  if (rows.length < Math.min(400, Math.floor(codes.length * 0.6))) throw new Error(`quote-coverage-${rows.length}-${codes.length}`);
  return {
    byCode: new Map(rows.map((row) => [row.code, row])),
    fetchedAt: fulfilled.map((result) => result.fetchedAt).filter((value): value is string => Boolean(value)).sort().at(-1) ?? new Date().toISOString(),
    liveData: fulfilled.some((result) => result.priceType === "即時價"),
    priceType: fulfilled.some((result) => result.priceType === "即時價") ? "即時價" : "收盤價",
  };
}

// All viewers and the strong/weak signal collectors use the same quote sweep.
// Group membership changes much less frequently than prices.
export const loadSharedLatestQuotes = timedSingleFlight(3_000, async () => loadLatestQuotes(await loadConfiguredGroups()));

