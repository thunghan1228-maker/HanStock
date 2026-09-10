export const INTRADAY_SIGNAL_CUTOVER_MINUTE = 8 * 60 + 45;

type TaipeiClock = {
  tradeDate: string;
  weekday: string;
  minuteOfDay: number;
};

type TwseHolidayRow = {
  Name?: unknown;
  Date?: unknown;
};

const TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule";
const HOLIDAY_CACHE_MS = 12 * 60 * 60 * 1_000;
const HOLIDAY_RETRY_CACHE_MS = 10 * 60 * 1_000;
let holidayCache: { expiresAt: number; dates: Set<string> } | null = null;

function taipeiClock(now: Date): TaipeiClock {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const hour = Number(value("hour"));
  const minute = Number(value("minute"));
  return {
    tradeDate: `${value("year")}-${value("month")}-${value("day")}`,
    weekday: value("weekday"),
    minuteOfDay: hour * 60 + minute,
  };
}

function previousOpenDate(tradeDate: string, closedDates: ReadonlySet<string>) {
  const cursor = new Date(`${tradeDate}T00:00:00Z`);
  for (let attempts = 0; attempts < 20; attempts += 1) {
    cursor.setUTCDate(cursor.getUTCDate() - 1);
    const weekday = cursor.getUTCDay();
    const candidate = cursor.toISOString().slice(0, 10);
    if (weekday !== 0 && weekday !== 6 && !closedDates.has(candidate)) return candidate;
  }
  return tradeDate;
}

export function resolveIntradaySignalCutoverDate(now = new Date(), closedDates: ReadonlySet<string> = new Set()) {
  const clock = taipeiClock(now);
  const isWeekend = clock.weekday === "Sat" || clock.weekday === "Sun";
  const isClosed = isWeekend || closedDates.has(clock.tradeDate);
  if (!isClosed && clock.minuteOfDay >= INTRADAY_SIGNAL_CUTOVER_MINUTE) return clock.tradeDate;
  return previousOpenDate(clock.tradeDate, closedDates);
}

export function resolveIntradaySignalDisplayDate(
  availableDates: string[],
  now = new Date(),
  closedDates: ReadonlySet<string> = new Set(),
) {
  const clock = taipeiClock(now);
  const target = resolveIntradaySignalCutoverDate(now, closedDates);
  const dates = [...new Set(availableDates.filter((date) => /^\d{4}-\d{2}-\d{2}$/.test(date)))].sort().reverse();
  if (target === clock.tradeDate) return target;
  return dates.find((date) => date <= target) ?? target;
}

function rocDateToIso(value: unknown) {
  const matched = String(value ?? "").trim().match(/^(\d{3})(\d{2})(\d{2})$/);
  if (!matched) return "";
  return `${Number(matched[1]) + 1911}-${matched[2]}-${matched[3]}`;
}

export function closedTradingDatesFromTwseSchedule(rows: TwseHolidayRow[]) {
  const dates = new Set<string>();
  for (const row of rows) {
    const name = String(row?.Name ?? "");
    // The schedule also contains informational markers for the first and last
    // trading day. Those dates are open and must not be treated as holidays.
    if (name.includes("開始交易") || name.includes("最後交易")) continue;
    const date = rocDateToIso(row?.Date);
    if (date) dates.add(date);
  }
  return dates;
}

export async function loadTwseClosedTradingDates() {
  const now = Date.now();
  if (holidayCache && holidayCache.expiresAt > now) return holidayCache.dates;
  try {
    const response = await fetch(TWSE_HOLIDAY_URL, {
      cache: "no-store",
      headers: { Accept: "application/json", "User-Agent": "HanStock-Battle-Signal-Session/1.0" },
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) throw new Error(`twse-holiday-${response.status}`);
    const payload = await response.json() as TwseHolidayRow[];
    const dates = closedTradingDatesFromTwseSchedule(Array.isArray(payload) ? payload : []);
    holidayCache = { expiresAt: now + HOLIDAY_CACHE_MS, dates };
    return dates;
  } catch {
    const dates = holidayCache?.dates ?? new Set<string>();
    holidayCache = { expiresAt: now + HOLIDAY_RETRY_CACHE_MS, dates };
    return dates;
  }
}
