export type ReleaseWeekRange = "本週出關" | "下週出關" | "下下週出關";

const RELEASE_WEEK_OFFSETS: Record<ReleaseWeekRange, number> = {
  "本週出關": 0,
  "下週出關": 1,
  "下下週出關": 2,
};

function normalizeDateKey(value: string) {
  const match = value.replaceAll("/", "-").match(/^(20\d{2})-(\d{2})-(\d{2})$/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
}

export function addCalendarDays(value: string, days: number) {
  const key = normalizeDateKey(value);
  if (!key) return "";
  const [year, month, day] = key.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

export function taipeiDateKey(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function mondayOfWeek(value: string) {
  const key = normalizeDateKey(value);
  if (!key) return "";
  const [year, month, day] = key.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return addCalendarDays(key, -((weekday + 6) % 7));
}

export function releaseWeekWindow(range: ReleaseWeekRange, today = taipeiDateKey()) {
  const monday = mondayOfWeek(today);
  const start = addCalendarDays(monday, RELEASE_WEEK_OFFSETS[range] * 7);
  return { start, end: addCalendarDays(start, 4) };
}

export function isReleaseInWeek(value: string, range: ReleaseWeekRange, today = taipeiDateKey()) {
  const key = normalizeDateKey(value);
  if (!key) return false;
  const window = releaseWeekWindow(range, today);
  return key >= window.start && key <= window.end;
}

export function formatReleaseWeekWindow(range: ReleaseWeekRange, today = taipeiDateKey()) {
  const { start, end } = releaseWeekWindow(range, today);
  const short = (value: string) => value ? `${value.slice(5, 7)}/${value.slice(8, 10)}` : "—";
  return `${short(start)}～${short(end)}`;
}

export const releaseWeekRanges = Object.keys(RELEASE_WEEK_OFFSETS) as ReleaseWeekRange[];
