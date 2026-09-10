import { loadConfiguredGroups as loadSharedGroups, loadSharedLatestQuotes } from "../../../lib/live-group-quotes";
import { timedKeyedSingleFlight } from "../../../lib/timed-single-flight";
import { loadTwseClosedTradingDates } from "../../../lib/intraday-signal-session";
import { readAjOfficialQuotes, saveAjOfficialQuotes } from "../../../db/aj-official-quotes";
import {
  AJ_CURRENT_STRONG_GROUP_RANK_LIMIT,
  AJ_CURRENT_WEAK_GROUP_RANK_START,
  AJ_RECENT_STRONG_GROUP_RANK_LIMIT,
  AJ_RECENT_WEAK_GROUP_RANK_START,
  type AjLargeForceGroupTransition,
} from "../../../lib/intraday-large-force";

type QuoteRow = {
  code: string;
  changePct: number;
};

type GroupRanks = Map<string, number>;
type RankingBundle = {
  tradeDate: string;
  previousDates: string[];
  currentRanks: GroupRanks;
  previousRanks: GroupRanks[];
  groups: Map<string, string[]>;
  live: boolean;
};

const MINIMUM_CONFIGURED_GROUP_COVERAGE = 60;
const MINIMUM_OFFICIAL_QUOTE_COVERAGE = 500;
const MAX_TICKERS = 250;

function numberValue(value: unknown) {
  const text = String(value ?? "").replace(/,/g, "").trim();
  if (!/^[+-]?\d+(?:\.\d+)?$/.test(text)) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseTradeDate(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
    ? date
    : null;
}

function isoDate(date: Date) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function taipeiClock() {
  const shifted = new Date(Date.now() + 8 * 60 * 60_000);
  return {
    date: isoDate(shifted),
    weekday: shifted.getUTCDay(),
    minute: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
  };
}

async function loadConfiguredGroups() {
  return new Map([...(await loadSharedGroups())].map(([name, members]) => [name, members.map(member => member.code)]));
}

async function loadTpexDirectPayload(tradeDate: string) {
  const url = new URL("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes");
  url.searchParams.set("date", tradeDate.replaceAll("-", "/"));
  url.searchParams.set("id", "");
  url.searchParams.set("response", "json");
  const headers = {
    Accept: "application/json,text/plain,*/*",
    "User-Agent": "Mozilla/5.0",
    Origin: "https://www.tpex.org.tw",
    Referer: "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html",
  };
  // TPEx may redirect cloud JSON requests to /errors. Its official CSV
  // export remains readable and contains the requested date and same quotes.
  const direct = await fetch(url, { cache: "no-store", headers, redirect: "manual", signal: AbortSignal.timeout(8_000) }).catch(() => null);
  if (direct?.ok) {
    const payload = await direct.json().catch(() => null);
    if (payload?.date === tradeDate.replaceAll("-", "") && Array.isArray(payload?.tables)) return payload;
  }
  url.searchParams.set("response", "csv");
  const response = await fetch(url, { cache: "no-store", headers, redirect: "manual", signal: AbortSignal.timeout(12_000) });
  if (!response.ok) throw new Error(`tpex-csv-${response.status}`);
  // Only ASCII date, code and price bytes are needed from the Big5 CSV.
  const text = new TextDecoder("latin1").decode(await response.arrayBuffer());
  const [year, month, day] = tradeDate.split("-");
  const rocDate = `${Number(year) - 1911}/${month}/${day}`;
  if (!text.split(/\r?\n/).slice(0, 3).some(line => line.includes(rocDate))) throw new Error("tpex-csv-date-mismatch");
  const rows = text.split(/\r?\n/).flatMap(line => {
    if (!/^"\d{4}",/.test(line)) return [];
    const row: string[] = []; let field = ""; let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"') {
        if (quoted && line[index + 1] === '"') { field += char; index += 1; }
        else quoted = !quoted;
      } else if (char === "," && !quoted) { row.push(field); field = ""; }
      else field += char;
    }
    row.push(field);
    return [row];
  });
  return { date: tradeDate.replaceAll("-", ""), tables: [{ data: rows }] };
}

async function loadTpexOfficialPayload(tradeDate: string) {
  try { return await loadTpexDirectPayload(tradeDate); }
  catch {
    // Same read-only public-report mirror used by technical-market history.
    const source = `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=${encodeURIComponent(tradeDate.replaceAll("-", "/"))}&id=&response=json`;
    const response = await fetch(`https://r.jina.ai/${source.replaceAll("&", "%26")}`, {
      cache: "no-store", headers: { Accept: "text/plain" }, signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw new Error(`tpex-report-mirror-${response.status}`);
    const text = await response.text();
    const start = text.indexOf('{'); const end = text.lastIndexOf('}');
    if (start < 0 || end <= start) throw new Error("tpex-report-mirror-invalid");
    return JSON.parse(text.slice(start, end + 1));
  }
}

const loadOfficialDay = timedKeyedSingleFlight(6 * 60 * 60_000, async (tradeDate: string) => {
  const saved = await readAjOfficialQuotes(tradeDate).catch(() => null);
  if (saved) return { tradeDate, quotes: new Map(saved.map(quote => [quote.code, quote])) };
  const compact = tradeDate.replaceAll("-", "");
  const [twseResult, tpexResult] = await Promise.allSettled([
    fetch(`https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${compact}&type=ALLBUT0999&response=json`, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-AJ-Filter/1.0" },
      signal: AbortSignal.timeout(20_000),
    }).then((response) => response.ok ? response.json() : Promise.reject(new Error(`twse-${response.status}`))),
    loadTpexOfficialPayload(tradeDate),
  ]);

  // A failed exchange response is not a holiday or a zero-change market.
  // Reject incomplete days before the six-hour cache can retain them.
  for (const [market, result] of [["twse", twseResult], ["tpex", tpexResult]] as const) {
    if (result.status !== "fulfilled") throw new Error(`${market}-session-unavailable-${tradeDate}: ${result.reason instanceof Error ? result.reason.message : "fetch-failed"}`);
    if (String(result.value?.date) !== compact) throw new Error(`${market}-session-date-mismatch-${tradeDate}`);
    const rows = (result.value?.tables ?? []).flatMap((table: { data?: unknown[][] }) => table.data ?? []);
    if (rows.filter((row: unknown[]) => /^\d{4}$/.test(String(row?.[0] ?? "").trim())).length < 100) {
      throw new Error(`${market}-session-incomplete-${tradeDate}`);
    }
  }

  const quotes = new Map<string, QuoteRow>();
  if (twseResult.status === "fulfilled") {
    const tables = Array.isArray(twseResult.value?.tables) ? twseResult.value.tables : [];
    const rows = tables.flatMap((table: { data?: unknown[][] }) => Array.isArray(table.data) ? table.data : []);
    for (const row of rows) {
      const code = String(row?.[0] ?? "").trim();
      if (!/^\d{4}$/.test(code)) continue;
      const close = numberValue(row[8]);
      const rawChange = numberValue(row[10]);
      const change = rawChange === null ? null : String(row[9] ?? "").includes("-") ? -Math.abs(rawChange) : rawChange;
      if (close === null || change === null || close - change <= 0) continue;
      quotes.set(code, { code, changePct: change / (close - change) * 100 });
    }
  }
  if (tpexResult.status === "fulfilled") {
    const tables = Array.isArray(tpexResult.value?.tables) ? tpexResult.value.tables : [];
    const rows = tables.flatMap((table: { data?: unknown[][] }) => Array.isArray(table.data) ? table.data : []);
    for (const row of rows) {
      const code = String(row?.[0] ?? "").trim();
      if (!/^\d{4}$/.test(code)) continue;
      const close = numberValue(row[2]);
      const change = numberValue(row[3]);
      if (close === null || change === null || close - change <= 0) continue;
      quotes.set(code, { code, changePct: change / (close - change) * 100 });
    }
  }
  if (quotes.size < MINIMUM_OFFICIAL_QUOTE_COVERAGE) throw new Error(`official-session-incomplete-${tradeDate}`);
  // Persist only complete, date-verified reports so all devices reuse them.
  await saveAjOfficialQuotes(tradeDate, [...quotes.values()]).catch(() => undefined);
  return { tradeDate, quotes };
}, 20);

function calculateRanks(groups: Map<string, string[]>, quotes: Map<string, QuoteRow>) {
  const rows = [...groups].flatMap(([group, members]) => {
    const changes = members.flatMap((ticker) => {
      const quote = quotes.get(ticker);
      return quote && Number.isFinite(quote.changePct) ? [quote.changePct] : [];
    });
    return changes.length > 0
      ? [{ group, avgChange: changes.reduce((sum, value) => sum + value, 0) / changes.length }]
      : [];
  }).sort((left, right) => right.avgChange - left.avgChange || left.group.localeCompare(right.group, "zh-TW"));
  return new Map(rows.map((row, index) => [row.group, index + 1]));
}

async function loadLiveGroupRanks(groups: Map<string, string[]>, tradeDate: string) {
  // groupStrength takes >20 seconds during the session. Reuse the dashboard's
  // ordinary-stock quote sweep and rank the SAME configured memberships.
  const latest = await loadSharedLatestQuotes();
  // fetchedAt is the request time, not the trading date. Reject yesterday's
  // closing quotes even if the upstream fetched them a second ago.
  const datedQuotes = new Map([...latest.byCode].filter(([, quote]) =>
    quote.date?.replaceAll("/", "-") === tradeDate));
  if (datedQuotes.size < 400) throw new Error(`current-quote-date-coverage-${tradeDate}-${datedQuotes.size}`);
  const ranks = calculateRanks(groups, datedQuotes);
  if (ranks.size < MINIMUM_CONFIGURED_GROUP_COVERAGE) throw new Error(`group-strength-incomplete-${ranks.size}`);
  return ranks;
}

const loadPreviousSessionRanks = timedKeyedSingleFlight(6 * 60 * 60_000, async (key: string) => {
  const [tradeDate, membership] = JSON.parse(key) as [string, Array<[string, string[]]>];
  return loadPreviousSessions(new Date(`${tradeDate}T00:00:00Z`), new Map(membership));
}, 8);

async function loadPreviousSessions(start: Date, groups: Map<string, string[]>) {
  const closedDates = await loadTwseClosedTradingDates();
  const dates: string[] = [];
  const ranks: GroupRanks[] = [];
  for (let offset = 1; offset <= 14 && dates.length < 3; offset += 1) {
    const candidate = new Date(start);
    candidate.setUTCDate(start.getUTCDate() - offset);
    if (candidate.getUTCDay() === 0 || candidate.getUTCDay() === 6) continue;
    const candidateDate = isoDate(candidate);
    if (closedDates.has(candidateDate)) continue;
    const official = await loadOfficialDay(candidateDate);
    const dayRanks = calculateRanks(groups, official.quotes);
    if (dayRanks.size < MINIMUM_CONFIGURED_GROUP_COVERAGE) throw new Error(`previous-group-coverage-${candidateDate}`);
    dates.push(official.tradeDate);
    ranks.push(dayRanks);
  }
  if (dates.length !== 3) throw new Error(`previous-session-incomplete-${dates.length}`);
  return { dates, ranks };
}

async function buildRankingBundle(tradeDate: string, date: Date) {
  const groups = await loadConfiguredGroups();
  const clock = taipeiClock();
  // Today's dated quote feed retains its closing prices. A wall-clock cutoff
  // must not switch working quotes to an unavailable official report at 14:30.
  const useLatestRanks = tradeDate === clock.date
    && clock.weekday >= 1 && clock.weekday <= 5
    && clock.minute >= 8 * 60 + 45;
  const [currentRanks, previous] = await Promise.all([useLatestRanks
    ? loadLiveGroupRanks(groups, tradeDate)
    : (async () => {
        const official = await loadOfficialDay(isoDate(date));
        if (official.quotes.size < MINIMUM_OFFICIAL_QUOTE_COVERAGE) {
          throw new Error(`current-session-incomplete-${official.quotes.size}`);
        }
        return calculateRanks(groups, official.quotes);
      })(),
    loadPreviousSessionRanks(JSON.stringify([tradeDate, [...groups]])),
  ]);
  return {
    tradeDate,
    previousDates: previous.dates,
    currentRanks,
    previousRanks: previous.ranks,
    groups,
    live: useLatestRanks && clock.minute < 13 * 60 + 30,
  } satisfies RankingBundle;
}

const loadRankingBundle = timedKeyedSingleFlight(30_000, (tradeDate: string) =>
  buildRankingBundle(tradeDate, new Date(`${tradeDate}T00:00:00Z`)), 8);

function qualifyingTransition(ticker: string, bundle: RankingBundle): AjLargeForceGroupTransition {
  const memberships = [...bundle.groups].flatMap(([group, members]) => {
    if (!members.includes(ticker)) return [];
    const currentRank = bundle.currentRanks.get(group) ?? null;
    const recentRanks = bundle.previousRanks
      .map((ranks) => ranks.get(group))
      .filter((rank): rank is number => typeof rank === "number");
    return [{ group, currentRank, recentRanks }];
  });
  const bearishCandidates = memberships.flatMap(({ group, currentRank, recentRanks }) => {
    const bestRecentRank = recentRanks.length > 0 ? Math.min(...recentRanks) : null;
    const qualifiesBearish = currentRank !== null
      && currentRank >= AJ_CURRENT_WEAK_GROUP_RANK_START
      && bestRecentRank !== null
      && bestRecentRank <= AJ_RECENT_STRONG_GROUP_RANK_LIMIT;
    return qualifiesBearish ? [{ group, currentRank, bestRecentRank, recentRanks }] : [];
  }).sort((left, right) =>
    (right.currentRank - right.bestRecentRank) - (left.currentRank - left.bestRecentRank)
    || left.bestRecentRank - right.bestRecentRank
    || left.group.localeCompare(right.group, "zh-TW")
  );
  const bullishCandidates = memberships.flatMap(({ group, currentRank, recentRanks }) => {
    const worstRecentRank = recentRanks.length > 0 ? Math.max(...recentRanks) : null;
    const qualifiesBullish = currentRank !== null
      && currentRank <= AJ_CURRENT_STRONG_GROUP_RANK_LIMIT
      && worstRecentRank !== null
      && worstRecentRank >= AJ_RECENT_WEAK_GROUP_RANK_START;
    return qualifiesBullish ? [{ group, currentRank, worstRecentRank, recentRanks }] : [];
  }).sort((left, right) =>
    (right.worstRecentRank - right.currentRank) - (left.worstRecentRank - left.currentRank)
    || left.currentRank - right.currentRank
    || left.group.localeCompare(right.group, "zh-TW")
  );
  const bearish = bearishCandidates[0];
  const bullish = bullishCandidates[0];
  return {
    ticker,
    qualifyingGroup: bearish?.group ?? null,
    currentRank: bearish?.currentRank ?? null,
    bestRecentRank: bearish?.bestRecentRank ?? null,
    recentRanks: bearish?.recentRanks ?? [],
    bullishQualifyingGroup: bullish?.group ?? null,
    bullishCurrentRank: bullish?.currentRank ?? null,
    worstRecentRank: bullish?.worstRecentRank ?? null,
    bullishRecentRanks: bullish?.recentRanks ?? [],
  };
}

export async function GET(request: Request) {
  const searchParams = new URL(request.url).searchParams;
  const tradeDateText = searchParams.get("tradeDate");
  const tradeDate = parseTradeDate(tradeDateText);
  const tickers = [...new Set(String(searchParams.get("tickers") ?? "")
    .split(",")
    .map((ticker) => ticker.trim().toUpperCase())
    .filter((ticker) => /^\d{4}$/.test(ticker)))].slice(0, MAX_TICKERS);
  if (!tradeDate || !tradeDateText || tickers.length === 0) {
    return Response.json({ ok: false, rows: [], error: "invalid-request" }, { status: 400 });
  }

  try {
    const bundle = await loadRankingBundle(tradeDateText);
    return Response.json({
      ok: true,
      tradeDate: bundle.tradeDate,
      previousDates: bundle.previousDates,
      live: bundle.live,
      rule: {
        recentStrongRankMax: AJ_RECENT_STRONG_GROUP_RANK_LIMIT,
        currentWeakRankMin: AJ_CURRENT_WEAK_GROUP_RANK_START,
        recentWeakRankMin: AJ_RECENT_WEAK_GROUP_RANK_START,
        currentStrongRankMax: AJ_CURRENT_STRONG_GROUP_RANK_LIMIT,
      },
      rows: tickers.map((ticker) => qualifyingTransition(ticker, bundle)),
    }, { headers: { "Cache-Control": bundle.live ? "no-store" : "public, max-age=300" } });
  } catch (error) {
    return Response.json({
      ok: false,
      tradeDate: tradeDateText,
      rows: [],
      error: error instanceof Error ? error.message : "aj-group-transition-failed",
    }, { status: 502, headers: { "Cache-Control": "no-store" } });
  }
}
