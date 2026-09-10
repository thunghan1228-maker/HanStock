import { after, NextRequest, NextResponse } from "next/server";
import { fetchCachedMarket, marketCacheWindow } from "../../../lib/market-fetch-cache";
import { readIntradayForceHistory, readForceRefreshState, saveForceRefreshState, saveDailyForce, saveIntradayForce, type IntradayForceRecord } from "../../../db/force-history";
import { createSavedForceCache } from "../../../lib/saved-force-cache";

type ForceInterval = "1m" | "5m";

type Candle = {
  ts?: number;
  date?: string;
  mainNetVolume?: number;
  mainBuyAmount?: number;
  mainSellAmount?: number;
  mainNetAmount?: number;
  mainTickCount?: number;
  ultraTickCount?: number;
  mainForceAvailable?: boolean;
  amountsAvailable?: boolean;
};

type CandlePayload = {
  result?: { data?: { json?: { candles?: Candle[] } } };
};

type HubForceBar = {
  ts?: number;
  bar_ts?: number;
  main_net_volume?: number;
  main_buy_amount?: number;
  main_sell_amount?: number;
  main_net_amount?: number;
  main_tick_count?: number;
  ultra_tick_count?: number;
  main_force_available?: boolean;
};

type HubForcePayload = {
  status?: string;
  bars?: HubForceBar[];
  data?: { bars?: HubForceBar[] };
};

const HUB_BASES = [
  // hanstock.xyz 原本掛在 Vercel 上，已因費用爭議永久關閉，直接打 Railway。
  "https://hanstock-production.up.railway.app",
];

function hasForce(candle: Candle | undefined) {
  return !!candle && candle.mainForceAvailable !== false && Number.isFinite(candle.mainNetVolume)
    && (candle.mainForceAvailable === true || candle.mainNetVolume !== 0 || !!candle.mainBuyAmount || !!candle.mainSellAmount || !!candle.mainTickCount);
}

function hasStoredForce(row: IntradayForceRecord | undefined) {
  return !!row && (row.observed === 1 || row.netVolume !== 0 || row.buyAmount !== 0 || row.sellAmount !== 0 || row.mainTickCount > 0);
}

function isoTradeDate(shortDate: string) {
  const match = shortDate.match(/^(\d{1,2})\/(\d{1,2})$/);
  if (!match) return shortDate;
  const taipeiNow = new Date(Date.now() + 8 * 60 * 60 * 1_000);
  return `${taipeiNow.getUTCFullYear()}-${match[1].padStart(2, "0")}-${match[2].padStart(2, "0")}`;
}

function isoBarTime(value: string) {
  const match = value.match(/^(\d{1,2}\/\d{1,2})\s+(\d{1,2}:\d{2})/);
  return match ? `${isoTradeDate(match[1])} ${match[2]}` : value;
}

function barTimestamp(value: string) {
  const match = value.match(/^(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const taipeiNow = new Date(Date.now() + 8 * 60 * 60 * 1_000);
  const timestamp = Date.UTC(
    taipeiNow.getUTCFullYear(),
    Number(match[1]) - 1,
    Number(match[2]),
    Number(match[3]) - 8,
    Number(match[4]),
  );
  return Number.isFinite(timestamp) ? timestamp : null;
}

function epochMilliseconds(value: number) {
  return value > 0 && value < 1_000_000_000_000 ? value * 1_000 : value;
}

function barMinute(value: string | undefined) {
  const match = value?.match(/(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function minuteLabel(minute: number) {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function hubBarDate(timestamp: number) {
  const taipei = new Date(timestamp + 8 * 60 * 60 * 1_000);
  return `${String(taipei.getUTCMonth() + 1).padStart(2, "0")}/${String(taipei.getUTCDate()).padStart(2, "0")} ${String(taipei.getUTCHours()).padStart(2, "0")}:${String(taipei.getUTCMinutes()).padStart(2, "0")}`;
}

async function fetchHubForceCandles(ticker: string, interval: ForceInterval) {
  // 歷史與今日即時資料並行讀取；歷史庫短暫忙碌時，今日主力副圖仍可先顯示。
  const paths = interval === "1m"
    ? [
        { path: `/api/hub/force/bars/${encodeURIComponent(ticker)}?interval=1m&days=31&limit=20000&backfill=false`, timeout: 4_000 },
        { path: `/api/hub/bars1m/${encodeURIComponent(ticker)}`, timeout: 7_000 },
      ]
    : [
        { path: `/api/hub/force/bars/${encodeURIComponent(ticker)}?interval=5m&days=31&limit=20000&backfill=false`, timeout: 4_000 },
        { path: `/api/hub/bars/${encodeURIComponent(ticker)}`, timeout: 7_000 },
      ];
  const payloads = await Promise.all(paths.map(async ({ path, timeout }) => {
    // The second hostname is a fallback, not another copy of the same feed.
    for (const base of HUB_BASES) {
      try {
        const response = await fetchCachedMarket(`${base}${path}`, {
          headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/2.1" },
          cache: "no-store",
          signal: AbortSignal.timeout(timeout),
        }, { ticker, history: path.includes("/force/bars/") });
        if (!response.ok) continue;
        const payload = await response.json() as HubForcePayload;
        if ((payload.bars ?? payload.data?.bars)?.length) return payload;
      } catch {
        // Try the backup only if the primary did not provide usable data.
      }
    }
    return null;
  }));
  const merged = new Map<number, Candle>();
  for (const payload of payloads) {
    const bars = payload?.bars ?? payload?.data?.bars;
    if (!Array.isArray(bars) || bars.length === 0) continue;
    const normalized = bars.flatMap((bar): Candle[] => {
      const timestamp = epochMilliseconds(Number(bar.ts ?? bar.bar_ts));
      const netLots = Number(bar.main_net_volume);
      if (!Number.isFinite(timestamp) || timestamp <= 0 || !Number.isFinite(netLots) || bar.main_force_available === false) return [];
      return [{
        ts: timestamp,
        date: hubBarDate(timestamp),
        // Hub 的主力量單位為「張」；原始 K 線元件內部仍以「股」計算。
        mainNetVolume: netLots * 1_000,
        mainBuyAmount: Number(bar.main_buy_amount) || 0,
        mainSellAmount: Number(bar.main_sell_amount) || 0,
        mainNetAmount: Number.isFinite(Number(bar.main_net_amount))
          ? Number(bar.main_net_amount)
          : (Number(bar.main_buy_amount) || 0) - (Number(bar.main_sell_amount) || 0),
        mainTickCount: Number(bar.main_tick_count) || 0,
        ultraTickCount: Number(bar.ultra_tick_count) || 0,
        mainForceAvailable: true,
        amountsAvailable: bar.main_buy_amount != null && bar.main_sell_amount != null,
      }];
    });
    for (const candle of normalized) {
      if (typeof candle.ts === "number") merged.set(candle.ts, candle);
    }
  }
  return merged.size > 0
    ? [...merged.values()].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0))
    : null;
}

async function fetchLegacyForceCandles(ticker: string, interval: ForceInterval) {
  const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval } }));
  const response = await fetchCachedMarket(`https://www.hanstock.xyz/api/trpc/stocks.candles?input=${input}`, {
    headers: { Accept: "application/json", "User-Agent": "HanStock-Battle/1.0" },
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  }, { ticker, activeMs: 15_000 });
  if (!response.ok) throw new Error(`candles_${response.status}`);
  const payload = await response.json() as CandlePayload;
  return payload.result?.data?.json?.candles ?? [];
}

async function fetchRawForceCandles(ticker: string, interval: ForceInterval) {
  // 舊 K 線來源可能仍持有部署前已存在的歷史日；與 Railway 永久資料合併，
  // 並讓 Railway/即時值覆蓋同一分鐘，完成一次後就會同步進 D1 保存。
  const [hub, legacy] = await Promise.all([
    fetchHubForceCandles(ticker, interval).catch(() => null),
    fetchLegacyForceCandles(ticker, interval).catch(() => []),
  ]);
  const merged = new Map<number, Candle>();
  for (const candle of [...legacy, ...(hub ?? [])]) {
    const ts = typeof candle.ts === "number" ? epochMilliseconds(candle.ts) : barTimestamp(candle.date ?? "");
    if (!ts) continue;
    const previous = merged.get(ts);
    // Price-only responses must not replace previously observed force fields.
    if (!previous || hasForce(candle) || !hasForce(previous)) merged.set(ts, { ...candle, ts });
  }
  const result = [...merged.values()].sort((a, b) => (a.ts ?? barTimestamp(a.date ?? "") ?? 0) - (b.ts ?? barTimestamp(b.date ?? "") ?? 0));
  return result;
}

const forceSourceCache = new Map<string, { expires: number; phase: string; pending: Promise<Candle[]> }>();
function cachedForceCandles(ticker: string, interval: ForceInterval) {
  const key = `${ticker}:${interval}`, cached = forceSourceCache.get(key);
  const window = marketCacheWindow({ ticker, activeMs: 15_000 });
  if (cached && cached.expires > Date.now() && cached.phase === window.phase) return cached.pending;
  const entry = { expires: Infinity, phase: window.phase, pending: Promise.resolve<Candle[]>([]) };
  entry.pending = fetchRawForceCandles(ticker, interval).then(rows => {
    entry.expires = rows.length ? Date.now() + window.ttl : 0;
    return rows;
  }, error => { entry.expires = 0; throw error; });
  if (forceSourceCache.size >= 64) forceSourceCache.delete(forceSourceCache.keys().next().value!);
  forceSourceCache.set(key, entry);
  return entry.pending;
}

function aggregateForceMinutes(minutes: Candle[]) {
  const groups = new Map<number, Candle>();
  for (const row of minutes) {
    if (!hasForce(row) || !row.ts) continue;
    const minute = barMinute(row.date);
    if (minute === null || minute < 540 || minute > 810) continue;
    const ts = minute === 810 ? row.ts - 5 * 60_000 : Math.floor(row.ts / 300_000) * 300_000;
    const old = groups.get(ts);
    const hasAmounts = row.amountsAvailable !== false && row.mainBuyAmount != null && row.mainSellAmount != null;
    groups.set(ts, { ts, date: hubBarDate(ts), mainForceAvailable: true,
      mainNetVolume: (old?.mainNetVolume ?? 0) + (row.mainNetVolume ?? 0),
      mainBuyAmount: (old?.mainBuyAmount ?? 0) + (row.mainBuyAmount ?? 0),
      mainSellAmount: (old?.mainSellAmount ?? 0) + (row.mainSellAmount ?? 0),
      mainNetAmount: (old?.mainNetAmount ?? 0) + (row.mainNetAmount ?? 0),
      mainTickCount: (old?.mainTickCount ?? 0) + (row.mainTickCount ?? 0),
      amountsAvailable: hasAmounts && old?.amountsAvailable !== false,
    });
  }
  return [...groups.values()];
}

async function fetchForceCandles(ticker: string, interval: ForceInterval) {
  if (interval === "1m") return cachedForceCandles(ticker, interval);
  const [five, minutes] = await Promise.all([cachedForceCandles(ticker, "5m"), cachedForceCandles(ticker, "1m")]);
  const merged = new Map(five.map(row => [row.ts, row]));
  const recoveredMinutes = await loadCompletedDays(ticker, "1m", minutes);
  for (const row of aggregateForceMinutes(recoveredMinutes)) {
    const old = merged.get(row.ts);
    if (!hasForce(old)) merged.set(row.ts, row);
    else if (old && old.amountsAvailable !== true && row.amountsAvailable && old.mainNetVolume === row.mainNetVolume) {
      merged.set(row.ts, { ...old, mainBuyAmount: row.mainBuyAmount, mainSellAmount: row.mainSellAmount, mainNetAmount: row.mainNetAmount, amountsAvailable: true });
    }
  }
  return [...merged.values()].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}

function completeTaiwanSession(candles: Candle[], interval: ForceInterval, latestDay: string, stored: IntradayForceRecord[] = []) {
  const step = interval === "1m" ? 1 : 5;
  const sessionStart = 9 * 60;
  const sessionClose = interval === "1m" ? 13 * 60 + 30 : 13 * 60 + 25;
  const liveByMinute = new Map<number, Candle>();
  for (const candle of candles) {
    const minute = barMinute(candle.date);
    if (minute === null || minute < sessionStart || minute > sessionClose) continue;
    liveByMinute.set(minute, candle);
  }
  const storedByMinute = new Map<number, IntradayForceRecord>();
  for (const record of stored) {
    const minute = barMinute(record.barTime);
    if (minute === null || minute < sessionStart || minute > sessionClose) continue;
    storedByMinute.set(minute, record);
  }
  if (liveByMinute.size === 0 && storedByMinute.size === 0) return [];
  const latestLiveMinute = Math.max(sessionStart, ...liveByMinute.keys());
  const latestStoredMinute = storedByMinute.size > 0 ? Math.max(...storedByMinute.keys()) : sessionStart;
  const sessionEnd = Math.min(sessionClose, Math.max(latestLiveMinute, latestStoredMinute));
  const completed: Candle[] = [];
  for (let minute = sessionStart; minute <= sessionEnd; minute += step) {
    const observed = liveByMinute.get(minute);
    const live = hasForce(observed) ? observed : undefined;
    const storedRow = storedByMinute.get(minute);
    const saved = hasStoredForce(storedRow) ? storedRow : undefined;
    if (!live && !saved) continue;
    const date = `${latestDay} ${minuteLabel(minute)}`;
    const liveNet = typeof live?.mainNetVolume === "number" && Number.isFinite(live.mainNetVolume) ? live.mainNetVolume : null;
    const savedNet = typeof saved?.netVolume === "number" && Number.isFinite(saved.netVolume) ? saved.netVolume : null;
    const liveBuyAmount = live?.amountsAvailable !== false && typeof live?.mainBuyAmount === "number" && Number.isFinite(live.mainBuyAmount) ? live.mainBuyAmount : null;
    const liveSellAmount = live?.amountsAvailable !== false && typeof live?.mainSellAmount === "number" && Number.isFinite(live.mainSellAmount) ? live.mainSellAmount : null;
    const savedBuyAmount = typeof saved?.buyAmount === "number" && Number.isFinite(saved.buyAmount) ? saved.buyAmount : null;
    const savedSellAmount = typeof saved?.sellAmount === "number" && Number.isFinite(saved.sellAmount) ? saved.sellAmount : null;
    const buyAmount = liveBuyAmount ?? savedBuyAmount ?? 0;
    const sellAmount = liveSellAmount ?? savedSellAmount ?? 0;
    completed.push({
      // 舊版只有 MM/DD HH:mm。補回台北時區 epoch，讓歷史主力值能與 K 棒穩定對齊。
      ts: typeof live?.ts === "number" && Number.isFinite(live.ts) ? live.ts : barTimestamp(date) ?? undefined,
      date,
      mainNetVolume: liveNet !== null && (live?.mainForceAvailable === true || liveNet !== 0) ? liveNet : savedNet ?? liveNet ?? 0,
      mainBuyAmount: buyAmount,
      mainSellAmount: sellAmount,
      mainNetAmount: live?.amountsAvailable !== false && typeof live?.mainNetAmount === "number" && Number.isFinite(live.mainNetAmount)
        ? live.mainNetAmount
        : typeof saved?.netAmount === "number" && Number.isFinite(saved.netAmount)
          ? saved.netAmount
          : buyAmount - sellAmount,
      mainTickCount: live?.mainForceAvailable === true && typeof live.mainTickCount === "number" && Number.isFinite(live.mainTickCount)
        ? live.mainTickCount
        : saved?.mainTickCount ?? (typeof live?.mainTickCount === "number" ? live.mainTickCount : 0),
      ultraTickCount: typeof live?.ultraTickCount === "number" && Number.isFinite(live.ultraTickCount) ? live.ultraTickCount : 0,
      mainForceAvailable: true,
      amountsAvailable: liveBuyAmount !== null && liveSellAmount !== null || !!saved && (saved.amountsAvailable === 1 || saved.buyAmount !== 0 || saved.sellAmount !== 0),
    });
  }
  return completed;
}

async function loadCompletedDays(ticker: string, interval: ForceInterval, candles: Candle[], saved?: IntradayForceRecord[]) {
  const dated = candles.filter((candle) => typeof candle.date === "string");
  const records = saved ?? await readIntradayForceHistory(ticker, interval).catch(() => []);
  const days = [...new Set([...dated.flatMap((candle) => candle.date ? [candle.date.slice(0, 5)] : []), ...records.map(row => row.tradeDate.slice(5).replace('-', '/'))])].sort();
  const completed: Candle[] = [];
  for (const day of days) {
    const tradeDate = isoTradeDate(day);
    completed.push(...completeTaiwanSession(
      dated.filter((candle) => candle.date?.startsWith(day)), interval, day, records.filter(row => row.tradeDate === tradeDate),
    ));
  }
  return completed;
}

async function persistForceCandles(ticker: string, interval: ForceInterval, candles: Candle[], previous: Candle[] = []) {
  candles = candles.filter(hasForce);
  if (candles.length === 0) return;
  const saved = new Map(previous.map(row => [row.date, row]));
  const fields = ["mainNetVolume", "mainBuyAmount", "mainSellAmount", "mainNetAmount", "mainTickCount"] as const;
  const changed = (row: Candle) => {
    const old = saved.get(row.date);
    return !old || fields.some(field => Math.round(old[field] ?? 0) !== Math.round(row[field] ?? 0));
  };
  const updatedAt = Date.now();
  const grouped = new Map<string, Candle[]>();
  for (const candle of candles) {
    const day = candle.date?.slice(0, 5);
    if (!day) continue;
    grouped.set(day, [...(grouped.get(day) ?? []), candle]);
  }
  await Promise.all([...grouped.entries()].flatMap(([day, dayCandles]) => {
    const changes = dayCandles.filter(changed);
    if (!changes.length) return [];
    const tradeDate = isoTradeDate(day);
    return [
    saveDailyForce({
      ticker,
      tradeDate,
      netVolume: dayCandles.reduce((total, candle) => total + (candle.mainNetVolume ?? 0), 0),
      barCount: dayCandles.length,
      sourceInterval: interval,
      lastBarAt: dayCandles.at(-1)?.date ?? "",
      updatedAt,
    }),
    saveIntradayForce(changes.flatMap((candle) => candle.date ? [{
      ticker,
      tradeDate,
      interval,
      barTime: isoBarTime(candle.date),
      netVolume: candle.mainNetVolume ?? 0,
      buyAmount: candle.mainBuyAmount ?? 0,
      sellAmount: candle.mainSellAmount ?? 0,
      netAmount: candle.mainNetAmount ?? (candle.mainBuyAmount ?? 0) - (candle.mainSellAmount ?? 0),
      mainTickCount: candle.mainTickCount ?? 0,
      updatedAt,
      observed: 1,
      amountsAvailable: candle.amountsAvailable ? 1 : 0,
    }] : [])),
  ];
  }));
}

function mergeObservedForceCandles(current: Candle[], incoming: Candle[]) {
  const rows = new Map(current.filter(hasForce).map(row => [row.date, row]));
  for (const row of incoming.filter(hasForce)) rows.set(row.date, row);
  return [...rows.values()].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}

const savedForceCache = createSavedForceCache<Candle[]>({ empty: () => [], weight: candles => candles.length, merge: mergeObservedForceCandles });

async function readSavedFirst(ticker: string, interval: ForceInterval) {
  const policy = marketCacheWindow({ ticker, activeMs: 15_000 });
  return savedForceCache(`${ticker}:${interval}`, policy, async () => {
    const [records, state] = await Promise.all([
      readIntradayForceHistory(ticker, interval),
      readForceRefreshState(ticker, interval).catch(() => null),
    ]);
    const value = await loadCompletedDays(ticker, interval, [], records);
    return { value, available: value.length > 0, refreshedAt: state?.refreshedAt ?? 0, phase: state?.phase ?? "" };
  }, async previous => {
    const source = await fetchForceCandles(ticker, interval);
    if (!source.some(hasForce)) throw new Error("force_source_unavailable");
    // Compare with the actual database, never a cache that may contain an unacknowledged write.
    const records = await readIntradayForceHistory(ticker, interval);
    const durable = await loadCompletedDays(ticker, interval, [], records);
    const completed = await loadCompletedDays(ticker, interval, mergeObservedForceCandles(previous, source), records);
    const refreshedAt = Date.now();
    let persisted = false;
    try {
      await persistForceCandles(ticker, interval, completed, durable);
      await saveForceRefreshState(ticker, interval, refreshedAt, policy.phase);
      persisted = true;
    } catch { /* Do not mark failed writes fresh: keep the values and retry against durable rows. */ }
    return { value: completed, available: completed.length > 0,
      refreshedAt: persisted ? refreshedAt : 0, phase: persisted ? policy.phase : "" };
  }, task => after(task));
}

export async function GET(request: NextRequest) {
  const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
  const interval: ForceInterval = request.nextUrl.searchParams.get("interval") === "1m" ? "1m" : "5m";
  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) {
    return NextResponse.json({ ok: false, error: "invalid ticker", bars: [] }, { status: 400 });
  }

  try {
    const snapshot = await readSavedFirst(ticker, interval);
    const completed = snapshot.value;
    const latestDay = completed.at(-1)?.date?.slice(0, 5) ?? "";
    const sameDay = completed.filter((candle) => candle.date?.startsWith(latestDay));
    // Keep the complete intraday session so the floating force panel can follow
    // every visible 1-minute crosshair position instead of only the last hour.
    const selected = completed.slice(interval === "5m" ? -10_800 : -54_000);
    const dayNet = sameDay.reduce((total, candle) => total + (candle.mainNetVolume ?? 0), 0) / 1_000_000;
    let runningDayNet = 0;
    const cumulativeByDate = new Map<string, number>();
    let runningDate = "";
    for (const candle of selected) {
      const candleDate = candle.date?.slice(0, 5) ?? "";
      if (candleDate !== runningDate) {
        runningDate = candleDate;
        runningDayNet = 0;
      }
      runningDayNet += candle.mainNetVolume ?? 0;
      if (candle.date) cumulativeByDate.set(candle.date, runningDayNet / 1_000_000);
    }
    const absolute = selected.map((candle) => Math.abs(candle.mainNetVolume ?? 0)).filter((value) => value > 0).sort((a,b)=>a-b);
    const ultraThreshold = absolute[Math.max(0, Math.floor(absolute.length * 0.8) - 1)] ?? Infinity;
    const bars = selected.map((candle) => ({
      ts: candle.ts ?? null,
      date: candle.date ?? "--",
      mainForceAvailable: candle.mainForceAvailable === true,
      net: candle.mainForceAvailable === true ? (candle.mainNetVolume ?? 0) / 1_000_000 : null,
      buyAmount: candle.mainBuyAmount ?? 0,
      sellAmount: candle.mainSellAmount ?? 0,
      netAmount: candle.mainNetAmount ?? (candle.mainBuyAmount ?? 0) - (candle.mainSellAmount ?? 0),
      amountsAvailable: candle.amountsAvailable === true,
      dayNet: candle.date ? cumulativeByDate.get(candle.date) ?? 0 : 0,
      ultra: (candle.ultraTickCount ?? 0) > 0 || (Math.abs(candle.mainNetVolume ?? 0) >= ultraThreshold && ultraThreshold > 0),
    }));
    const updatedAt = selected.at(-1)?.date ?? "";
    return NextResponse.json(
      { ok: true, ticker, interval, bars, dayNet, updatedAt,
        refreshedAt: snapshot.refreshedAt, source: "saved-force-first" },
      { headers: { "Cache-Control": "public, max-age=5, stale-while-revalidate=10" } },
    );
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "candles_failed", bars: [] },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
