/** Weekly research uses HanStock institutional scores (points), never TDCC percentages. */
export type ResearchStock = { code: string | null; name: string; groupName: string; market: string; score: number; increaseRank: number | null };
export type ResearchArchive = { weekEndDate: string; weights?: Record<string, number>; stocks: ResearchStock[] };
export type ResearchPoint = { date: string; score: number | null; rank: number | null; delta: number | null };
export type ResearchRow = { code: string; name: string; groupName: string; market: string; points: ResearchPoint[]; appearances: number; recentAppearances: number; available: number; average: number | null; positiveTotal: number; negativeTotal: number; threeRising: boolean; fiveOfSix: boolean; newEntry: boolean; dropped: boolean; turnedPositive: boolean; turnedNegative: boolean };

export function normalizeWeek(date: string) { return date.replaceAll('-', '/'); }
export function shiftWeek(date: string, offset: number) {
  const parsed = new Date(`${normalizeWeek(date).replaceAll('/', '-')}T00:00:00Z`);
  parsed.setUTCDate(parsed.getUTCDate() + offset * 7);
  return parsed.toISOString().slice(0, 10).replaceAll('-', '/');
}
function weightKey(weights?: Record<string, number>) {
  return weights ? Object.entries(weights).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}:${value}`).join('|') : null;
}
const round = (n: number) => Math.round(n * 10) / 10;
const top = (p: ResearchPoint | undefined, limit: number) => p?.score !== null && (p?.score ?? 0) > 0 && p?.rank != null && p.rank > 0 && p.rank <= limit;

export function buildWeeklyInsights(archives: ResearchArchive[], selectedDate: string, window = 12, topLimit = 10): ResearchRow[] {
  const selected = normalizeWeek(selectedDate);
  const byDate = new Map(archives.map(a => [normalizeWeek(a.weekEndDate), a]));
  const current = byDate.get(selected);
  if (!current) return [];
  const signature = weightKey(current.weights);
  const count = Math.max(1, Math.min(12, window));
  // Calendar slots deliberately keep missing weeks; gaps must not become a streak.
  const weeks = Array.from({ length: Math.max(count, 7) + 1 }, (_, i) => {
    const date = shiftWeek(selected, -i);
    const archive = byDate.get(date);
    const compatible = archive && signature !== null && weightKey(archive.weights) === signature;
    const stocks = new Map((compatible ? archive.stocks : []).filter(s => s.code && Number.isFinite(s.score)).map(s => [s.code!, s]));
    return { date, stocks };
  });
  const universe = new Map<string, ResearchStock>();
  weeks.slice(0, count).forEach(w => w.stocks.forEach((s, code) => { if (!universe.has(code)) universe.set(code, s); }));
  return [...universe].map(([code, stock]) => {
    const allPoints = weeks.slice(0, -1).map((w, i): ResearchPoint => {
      const row = w.stocks.get(code), previous = weeks[i + 1].stocks.get(code);
      return { date: w.date, score: row?.score ?? null, rank: row?.increaseRank ?? null, delta: row && previous ? round(row.score - previous.score) : null };
    });
    const points = allPoints.slice(0, count);
    const valid = points.filter((p): p is ResearchPoint & { score: number } => p.score !== null);
    const [now, previous] = allPoints;
    const comparable = now?.score != null && previous?.score != null;
    return { code, name: stock.name, groupName: stock.groupName, market: stock.market, points,
      appearances: points.filter(p => top(p, topLimit)).length,
      recentAppearances: allPoints.slice(0, 4).filter(p => top(p, topLimit)).length,
      available: valid.length, average: valid.length ? round(valid.reduce((n, p) => n + p.score, 0) / valid.length) : null,
      positiveTotal: round(valid.reduce((n, p) => n + Math.max(p.score, 0), 0)),
      negativeTotal: round(valid.reduce((n, p) => n + Math.min(p.score, 0), 0)),
      threeRising: allPoints.slice(0, 3).every(p => p.delta !== null && p.delta > 0),
      fiveOfSix: allPoints.slice(0, 6).every(p => p.delta !== null) && allPoints.slice(0, 6).filter(p => p.delta! > 0).length >= 5,
      newEntry: comparable && top(now, topLimit) && !top(previous, topLimit),
      dropped: comparable && !top(now, topLimit) && top(previous, topLimit),
      turnedPositive: comparable && now.score! > 0 && previous.score! < 0,
      turnedNegative: comparable && now.score! < 0 && previous.score! > 0,
    };
  }).sort((a, b) => b.appearances - a.appearances || (b.average ?? -Infinity) - (a.average ?? -Infinity) || a.code.localeCompare(b.code));
}

export function weeklyGroupBreadth(rows: ResearchRow[]) {
  const groups = new Map<string, ResearchRow[]>();
  rows.forEach(row => { if (row.groupName && !['未分類', 'ETF'].includes(row.groupName)) groups.set(row.groupName, [...(groups.get(row.groupName) ?? []), row]); });
  return [...groups].map(([name, members]) => {
    const valid = members.filter(row => row.points[0]?.delta != null);
    const rising = valid.filter(row => row.points[0].delta! > 0).length;
    return { name, members, valid: valid.length, rising, ratio: valid.length ? rising / valid.length : null };
  }).sort((a, b) => (b.ratio ?? -1) - (a.ratio ?? -1) || b.valid - a.valid || a.name.localeCompare(b.name));
}

export type WeeklyEntryKind = 'resonant' | 'single' | 'outside' | 'unclassified' | 'pending';
export function weeklyReportGroups(rows: ResearchRow[], topLimit = 20) {
  const current = rows.filter(row => row.points[0]?.score != null);
  const entries = current.filter(row => top(row.points[0], topLimit)).sort((a, b) => a.points[0].rank! - b.points[0].rank!);
  const groups = weeklyGroupBreadth(current).map(group => ({ ...group,
    delta: group.valid ? group.members.reduce((sum, row) => sum + (row.points[0]?.delta ?? 0), 0) / group.valid : null,
    entries: entries.filter(row => row.groupName === group.name),
  })).sort((a, b) => (b.delta ?? -Infinity) - (a.delta ?? -Infinity) || a.name.localeCompare(b.name));
  const ranked = groups.map((group, index) => ({ ...group, rank: group.delta === null ? null : index + 1 }));
  const byName = new Map(ranked.map(group => [group.name, group]));
  const classified = entries.map(row => {
    const group = byName.get(row.groupName);
    const kind: WeeklyEntryKind = !group ? 'unclassified' : group.rank === null ? 'pending' : group.rank > 10 ? 'outside' : group.entries.length >= 2 ? 'resonant' : 'single';
    return { row, kind, groupRank: group?.rank ?? null };
  });
  return { groups: ranked, entries: classified };
}

export function weeklyPage<T>(rows: T[], requestedPage: number, size: number) {
  const pageSize = size === 50 ? 50 : 20;
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(pages, Math.max(1, Math.trunc(requestedPage) || 1));
  return { page, pages, rows: rows.slice((page - 1) * pageSize, page * pageSize) };
}

export function weeklyScreenerUrl(codes: string[], date: string, label: string) {
  const valid = [...new Set(codes.filter(code => /^[0-9A-Z]{4,7}$/.test(code)))].slice(0, 80);
  return `/stock-screener?${new URLSearchParams({ weeklyCodes: valid.join(','), weeklyDate: date, weeklyLabel: label })}`;
}
export function parseWeeklySelection(params: URLSearchParams) {
  if (!params.has('weeklyCodes')) return null;
  const codes = [...new Set((params.get('weeklyCodes') ?? '').split(',').filter(c => /^[0-9A-Z]{4,7}$/.test(c)))].slice(0, 80);
  const date = normalizeWeek(params.get('weeklyDate') ?? '');
  return { codes, date: /^\d{4}\/\d{2}\/\d{2}$/.test(date) ? date : '未指定週次', label: (params.get('weeklyLabel') ?? '每週籌碼名單').slice(0, 60) };
}
