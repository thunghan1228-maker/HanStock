export type GroupDispositionInput = {
  status: "即將處置" | "處置中" | "已結束" | "處置公告";
  period: string;
  releaseDate: string;
};

export type GroupDispositionDisplay = {
  label: string;
  isActive: boolean;
  tone: "active" | "upcoming" | "released" | "notice";
};

const DAY_MS = 86_400_000;

function dateParts(value: string) {
  const match = value.match(/(20\d{2})[/-](\d{2})[/-](\d{2})/);
  if (!match) return null;
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
}

function dateOrdinal(value: string) {
  const parts = dateParts(value);
  return parts ? Math.floor(Date.UTC(parts.year, parts.month - 1, parts.day) / DAY_MS) : null;
}

export function taipeiDateKey(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function formatGroupDisposition(
  row: GroupDispositionInput | undefined,
  today = taipeiDateKey(),
): GroupDispositionDisplay {
  if (!row) return { label: "非處置股", isActive: false, tone: "notice" };

  const todayOrdinal = dateOrdinal(today);
  const releaseOrdinal = dateOrdinal(row.releaseDate);
  if (row.status === "處置中") {
    const daysUntilRelease = todayOrdinal !== null && releaseOrdinal !== null
      ? Math.max(0, releaseOrdinal - todayOrdinal)
      : null;
    return {
      label: daysUntilRelease === null
        ? "處置中"
        : daysUntilRelease === 0 ? "處置中｜今日出關" : `處置中｜距出關 ${daysUntilRelease} 天`,
      isActive: true,
      tone: "active",
    };
  }

  if (row.status === "即將處置") {
    const start = row.period.match(/20\d{2}\/\d{2}\/\d{2}/)?.[0];
    return { label: start ? `即將處置｜${start} 起` : "即將處置", isActive: false, tone: "upcoming" };
  }

  if (row.status === "已結束") {
    const elapsed = todayOrdinal !== null && releaseOrdinal !== null
      ? Math.max(1, todayOrdinal - releaseOrdinal + 1)
      : null;
    return {
      label: elapsed === null ? "已出關" : `已出關｜出關第 ${elapsed} 天`,
      isActive: false,
      tone: "released",
    };
  }

  return { label: "處置公告", isActive: false, tone: "notice" };
}

export function formatGroupNetFundingRate(
  netLargeAmount: number | undefined,
  turnoverAmount: number | undefined,
  available: boolean | undefined,
) {
  if (!available || !Number.isFinite(netLargeAmount) || !Number.isFinite(turnoverAmount) || (turnoverAmount ?? 0) <= 0) {
    return { label: "待資料", value: null };
  }
  const value = (netLargeAmount ?? 0) / (turnoverAmount ?? 1) * 100;
  return { label: `${value > 0 ? "+" : ""}${value.toFixed(2)}%`, value };
}
