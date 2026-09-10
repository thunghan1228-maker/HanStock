export const CHIP_SERVER_REFRESH_INTERVAL_MS = 5 * 60 * 1_000;

export type TaipeiMarketClock = {
  date: string;
  weekday: string;
  minutes: number;
};

export function taipeiMarketClock(now = Date.now()): TaipeiMarketClock {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).formatToParts(new Date(now));
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${value("year")}-${value("month")}-${value("day")}`,
    weekday: value("weekday"),
    minutes: Number(value("hour")) * 60 + Number(value("minute")),
  };
}

export function shouldTriggerChipServerRefresh(lastAttemptAt: number, now = Date.now()) {
  const clock = taipeiMarketClock(now);
  const weekday = clock.weekday !== "Sat" && clock.weekday !== "Sun";
  const afterOfficialCloseWindow = clock.minutes >= 15 * 60 + 10;
  const preopenRecoveryWindow = clock.minutes < 9 * 60;
  return weekday
    && (afterOfficialCloseWindow || preopenRecoveryWindow)
    && now - lastAttemptAt >= CHIP_SERVER_REFRESH_INTERVAL_MS;
}

function normalizedDate(value: string | null | undefined) {
  const match = value?.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/u);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : null;
}

function displayedDate(value: string | null | undefined) {
  return normalizedDate(value)?.replaceAll("-", "/") ?? "最近交易日";
}

export function chipAutomaticUpdateMessage(dataDate: string | null | undefined, current: boolean, now = Date.now()) {
  const clock = taipeiMarketClock(now);
  const date = normalizedDate(dataDate);
  const weekend = clock.weekday === "Sat" || clock.weekday === "Sun";
  const beforeRelease = !weekend && clock.minutes < 15 * 60 + 10;

  if (current && date === clock.date) {
    return {
      label: "今日盤後資料已更新",
      detail: `資料日 ${displayedDate(dataDate)}；伺服器會繼續自動確認。`,
    };
  }
  if (beforeRelease && date && date < clock.date) {
    return {
      label: "盤中沿用上一交易日",
      detail: `目前顯示 ${displayedDate(dataDate)} 是正確的；今日完整盤後資料將於 15:10 後由伺服器主動更新。`,
    };
  }
  if (weekend && date) {
    return {
      label: "休市沿用最近交易日",
      detail: `目前顯示最近交易日 ${displayedDate(dataDate)}；下一交易日盤後自動更新。`,
    };
  }
  return {
    label: "盤後資料整理中",
    detail: "伺服器每 5 分鐘主動重試，正式資料完整後自動切換，不需要手動按更新。",
  };
}
