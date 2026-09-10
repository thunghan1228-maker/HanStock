export type FundamentalRiverSnapshotInput = {
  ticker: string;
  name: string;
  market: "twse" | "tpex";
  groupName: string;
  reportPeriod: string;
  ttmEps: number | null;
  waterline: number | null;
  position: string;
  distancePct: number | null;
  close: number | null;
  maScore: number | null;
  pe: number | null;
  pb: number | null;
};

function getD1() {
  return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB ?? null;
}

function taipeiQueryDate(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

export async function recordFundamentalRiverQuery(input: FundamentalRiverSnapshotInput) {
  const d1 = getD1();
  if (!d1) return false;
  const now = Date.now();
  const previous = await d1.prepare("SELECT report_period AS reportPeriod, report_changed_at AS reportChangedAt FROM fundamental_river_snapshots WHERE ticker = ? LIMIT 1")
    .bind(input.ticker).first<{ reportPeriod: string; reportChangedAt: number }>();
  const reportChangedAt = !previous || previous.reportPeriod !== input.reportPeriod ? now : Number(previous.reportChangedAt) || now;
  await d1.batch([
    d1.prepare(`INSERT INTO fundamental_river_queries
      (query_date, ticker, name, market, group_name, query_count, last_queried_at)
      VALUES (?, ?, ?, ?, ?, 1, ?)
      ON CONFLICT(query_date, ticker) DO UPDATE SET
        name=excluded.name, market=excluded.market, group_name=excluded.group_name,
        query_count=fundamental_river_queries.query_count+1, last_queried_at=excluded.last_queried_at`)
      .bind(taipeiQueryDate(), input.ticker, input.name, input.market, input.groupName, now),
    d1.prepare(`INSERT INTO fundamental_river_snapshots
      (ticker, name, market, group_name, report_period, ttm_eps, waterline, position, distance_pct, close, ma_score, pe, pb, updated_at, report_changed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(ticker) DO UPDATE SET
        name=excluded.name, market=excluded.market, group_name=excluded.group_name,
        report_period=excluded.report_period, ttm_eps=excluded.ttm_eps, waterline=excluded.waterline,
        position=excluded.position, distance_pct=excluded.distance_pct, close=excluded.close,
        ma_score=excluded.ma_score, pe=excluded.pe, pb=excluded.pb,
        updated_at=excluded.updated_at, report_changed_at=excluded.report_changed_at`)
      .bind(input.ticker, input.name, input.market, input.groupName, input.reportPeriod, input.ttmEps, input.waterline, input.position, input.distancePct, input.close, input.maScore, input.pe, input.pb, now, reportChangedAt),
  ]);
  return true;
}

export async function readFundamentalRiverActivity() {
  const d1 = getD1();
  if (!d1) return { popular: [], recentReports: [] };
  const since = new Date();
  since.setUTCDate(since.getUTCDate() - 7);
  const sinceDate = taipeiQueryDate(since);
  const [popular, reports] = await Promise.all([
    d1.prepare(`SELECT ticker, name, market, group_name AS groupName, SUM(query_count) AS queryCount, MAX(last_queried_at) AS lastQueriedAt
      FROM fundamental_river_queries WHERE query_date >= ?
      GROUP BY ticker, name, market, group_name ORDER BY queryCount DESC, lastQueriedAt DESC LIMIT 12`)
      .bind(sinceDate).all<{ ticker: string; name: string; market: string; groupName: string; queryCount: number; lastQueriedAt: number }>(),
    d1.prepare(`SELECT ticker, name, market, group_name AS groupName, report_period AS reportPeriod,
      ttm_eps AS ttmEps, position, close, report_changed_at AS reportChangedAt
      FROM fundamental_river_snapshots WHERE report_changed_at >= ?
      ORDER BY report_changed_at DESC LIMIT 16`)
      .bind(Date.now() - 7 * 24 * 60 * 60 * 1_000).all<{ ticker: string; name: string; market: string; groupName: string; reportPeriod: string; ttmEps: number | null; position: string; close: number | null; reportChangedAt: number }>(),
  ]);
  return { popular: popular.results, recentReports: reports.results };
}
