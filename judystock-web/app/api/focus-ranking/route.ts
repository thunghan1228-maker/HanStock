import { isPreopenTrialWindow, loadPreopenTrialQuotes, taipeiMarketClock } from "../../../lib/preopen-trial";
import { loadConfiguredGroups, loadSharedLatestQuotes, type QuoteRow, type GroupMember, type GroupResult } from "../../../lib/live-group-quotes";

type Direction = "strong" | "weak";
type DirectionRequest = Direction | "both";

const HANSTOCK_GROUP_TOTAL = 67;
function signedPercent(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replace(/,/g, "").replace(/[^0-9.+-]/g, ""));
  return Number.isFinite(parsed) ? parsed : null;
}

function taipeiNowParts() {
  const shifted = new Date(Date.now() + 8 * 60 * 60 * 1000);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth(),
    day: shifted.getUTCDate(),
    weekday: shifted.getUTCDay(),
    minutes: shifted.getUTCHours() * 60 + shifted.getUTCMinutes(),
  };
}

function dateKey(date: Date) {
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

async function loadOfficialDay(day: Date) {
  const compact = dateKey(day);
  const slash = `${compact.slice(0, 4)}/${compact.slice(4, 6)}/${compact.slice(6, 8)}`;
  const [twseResult, tpexResult] = await Promise.allSettled([
    fetch(`https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=${compact}&type=ALLBUT0999&response=json`, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Focus/2.1" },
      signal: AbortSignal.timeout(20_000),
    }).then((response) => response.ok ? response.json() : Promise.reject(new Error(`twse-${response.status}`))),
    fetch(`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=${encodeURIComponent(slash)}&id=&response=json`, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 HanStock-Battle-Focus/2.1" },
      signal: AbortSignal.timeout(20_000),
    }).then((response) => response.ok ? response.json() : Promise.reject(new Error(`tpex-${response.status}`))),
  ]);
  const quotes = new Map<string, QuoteRow & { code: string; changePct: number }>();
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
      quotes.set(code, { code, price: close, changePct: change / (close - change) * 100 });
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
      quotes.set(code, { code, price: close, changePct: change / (close - change) * 100 });
    }
  }
  return { compact, quotes };
}

async function loadLatestOfficialQuotes() {
  const now = taipeiNowParts();
  const start = new Date(Date.UTC(now.year, now.month, now.day));
  if (now.minutes < 14 * 60 + 30) start.setUTCDate(start.getUTCDate() - 1);
  for (let offset = 0; offset < 10; offset += 1) {
    const candidate = new Date(start);
    candidate.setUTCDate(start.getUTCDate() - offset);
    if (candidate.getUTCDay() === 0 || candidate.getUTCDay() === 6) continue;
    const result = await loadOfficialDay(candidate);
    if (result.quotes.size >= 500) {
      return {
        byCode: result.quotes,
        fetchedAt: new Date().toISOString(),
        sourceDate: `${result.compact.slice(0, 4)}/${result.compact.slice(4, 6)}/${result.compact.slice(6, 8)}`,
        liveData: false,
        priceType: "收盤價",
      };
    }
  }
  throw new Error("official-close-unavailable");
}

function shouldUseOfficialClose() {
  const now = taipeiNowParts();
  return now.weekday === 0 || now.weekday === 6 || now.minutes < 9 * 60 || now.minutes > 13 * 60 + 35;
}

function calculateGroups(groups: Map<string, GroupMember[]>, quotes: Map<string, QuoteRow & { code: string; changePct: number }>) {
  const results: GroupResult[] = [];
  for (const [name, members] of groups) {
    const valid = members.flatMap((member) => {
      const quote = quotes.get(member.code);
      return quote ? [{
        ...member,
        changePct: quote.changePct,
        price: typeof quote.price === "number" && Number.isFinite(quote.price) ? quote.price : null,
      }] : [];
    });
    if (valid.length === 0) continue;
    results.push({
      name,
      avgChange: valid.reduce((sum, member) => sum + member.changePct, 0) / valid.length,
      members: valid,
    });
  }
  return results;
}

function taipeiDate(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(date)
    : null;
}

export async function GET(request: Request) {
  const searchParams = new URL(request.url).searchParams;
  const requestedDirection = searchParams.get("direction");
  const direction: DirectionRequest = requestedDirection === "both" ? "both" : requestedDirection === "weak" ? "weak" : "strong";
  const summaryOnly = searchParams.get("summary") === "1";

  try {
    const configuredGroups = await loadConfiguredGroups();
    const latest = isPreopenTrialWindow()
      ? await (async () => {
          const codes = [...new Set([...configuredGroups.values()].flatMap((members) => members.map((member) => member.code)))];
          const trial = await loadPreopenTrialQuotes(codes);
          const clock = taipeiMarketClock();
          return {
            byCode: new Map(trial.quotes.map((row) => [row.code, row])),
            fetchedAt: new Date().toISOString(),
            sourceDate: `${clock.date.slice(0, 4)}/${clock.date.slice(4, 6)}/${clock.date.slice(6, 8)}`,
            liveData: true,
            priceType: "盤前試撮",
          };
        })()
      : shouldUseOfficialClose()
        ? await loadLatestOfficialQuotes()
        : { ...(await loadSharedLatestQuotes()), sourceDate: null as string | null };
    const allGroups = calculateGroups(configuredGroups, latest.byCode);
    const rankSide = (side: Direction, includeGroups = true) => {
      const sorted = [...allGroups].sort((a, b) => side === "strong" ? b.avgChange - a.avgChange : a.avgChange - b.avgChange);
      const directional = sorted.filter((group) => side === "strong" ? group.avgChange > 0 : group.avgChange < 0);
      const summaryRows = (directional.length ? directional : sorted).slice(0, 8);
      const averageChange = summaryRows.length
        ? summaryRows.reduce((sum, group) => sum + group.avgChange, 0) / summaryRows.length
        : 0;
      const groupPayload = (group: GroupResult) => {
        const sortedMembers = [...group.members]
          .sort((a, b) => side === "strong" ? b.changePct - a.changePct : a.changePct - b.changePct);
        const firstThree = sortedMembers.slice(0, 3);
        const thirdChange = firstThree.at(-1)?.changePct;
        const selectedMembers = typeof thirdChange === "number"
          ? [...firstThree, ...sortedMembers.slice(3).filter((member) => signedPercent(member.changePct) === signedPercent(thirdChange))]
          : firstThree;
        return {
          name: group.name,
          change: signedPercent(group.avgChange),
          stocks: selectedMembers.map((member) => ({
            symbol: `${member.code} ${member.name}`,
            change: signedPercent(member.changePct),
            price: member.price,
          })),
        };
      };
      const signalGroupRows = sorted.slice(0, 10);
      const tenthGroupChange = signalGroupRows.at(-1)?.avgChange;
      if (typeof tenthGroupChange === "number") {
        const tiedChange = signedPercent(tenthGroupChange);
        signalGroupRows.push(...sorted.slice(10).filter((group) => signedPercent(group.avgChange) === tiedChange));
      }
      return {
        groups: includeGroups ? sorted.slice(0, 6).map(groupPayload) : [],
        // 均線空（日線）使用同一份正式 67 族群資料：弱勢前十群各取前三弱股。
        signalGroups: includeGroups ? signalGroupRows.map(groupPayload) : [],
        summary: {
          groupCount: directional.length,
          averageChange,
          strength: Math.max(5, Math.min(95, Math.round(50 + averageChange * 8))),
        },
      };
    };
    const shared = {
      updatedAt: latest.fetchedAt,
      sourceDate: latest.sourceDate ?? taipeiDate(latest.fetchedAt),
      liveData: latest.liveData,
      priceType: latest.priceType,
      configuredGroupCount: configuredGroups.size,
      configuredGroupTarget: HANSTOCK_GROUP_TOTAL,
    };

    if (direction === "both") {
      const weak = rankSide("weak");
      const strong = rankSide("strong");
      return Response.json({
        ok: true,
        direction,
        ...shared,
        rankings: {
          strong,
          weak,
        },
      }, { headers: { "Cache-Control": "no-store" } });
    }

    const ranked = rankSide(direction, !summaryOnly);

    return Response.json({
      ok: summaryOnly || ranked.groups.length === 6,
      direction,
      ...shared,
      groups: ranked.groups,
      summary: ranked.summary,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json(
      { ok: false, direction, groups: [], error: error instanceof Error ? error.message : "focus-ranking-failed" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
