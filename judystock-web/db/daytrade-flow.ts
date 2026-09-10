export type DaytradeFlowRecord = {
  ticker: string; name: string; market: string; tradeDate: string;
  category: "漲停鎖定" | "曾達漲停" | "強勢大單";
  closePrice: number; referencePrice?: number; limitUpPrice: number; dayChangePct: number;
  largeBuyAmount: number; largeSellAmount: number; netLargeAmount: number;
  turnoverAmount: number; participationRate: number; lateBuyConcentration: number;
  suspicionScore: number; estimatedNextDaySellAmount: number;
  confirmedReversalRate: number | null; updatedAt: number;
  mainForceDataAvailable: boolean; mainForceDataStatus: string;
};
let schemaReady: Promise<void> | null = null;
function getD1() { return (globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database }).__HANSTOCK_DB ?? null; }
async function database() {
  const d1 = getD1();
  if (!d1) throw new Error("daytrade_flow_storage_unavailable");
  if (!schemaReady) schemaReady = (async () => {
    await d1.batch([
      d1.prepare(`CREATE TABLE IF NOT EXISTS daytrade_flow_daily_v2 (ticker TEXT NOT NULL, name TEXT NOT NULL, market TEXT NOT NULL, trade_date TEXT NOT NULL, category TEXT NOT NULL, close_price REAL NOT NULL, limit_up_price REAL NOT NULL, day_change_pct REAL NOT NULL, large_buy_amount REAL NOT NULL, large_sell_amount REAL NOT NULL, net_large_amount REAL NOT NULL, turnover_amount REAL NOT NULL, participation_rate REAL NOT NULL, late_buy_concentration REAL NOT NULL, suspicion_score REAL NOT NULL, estimated_next_day_sell_amount REAL NOT NULL, confirmed_reversal_rate REAL, updated_at INTEGER NOT NULL, main_force_data_available INTEGER NOT NULL DEFAULT 1, main_force_data_status TEXT NOT NULL DEFAULT 'historical_ticks', PRIMARY KEY (ticker, trade_date))`),
      d1.prepare("CREATE INDEX IF NOT EXISTS daytrade_flow_v2_date_category_idx ON daytrade_flow_daily_v2 (trade_date DESC, category, suspicion_score DESC)"),
    ]);
    const columns = await d1.prepare("PRAGMA table_info(daytrade_flow_daily_v2)").all<{ name: string }>();
    const names = new Set((columns.results ?? []).map((column) => column.name));
    if (!names.has("main_force_data_available")) await d1.prepare("ALTER TABLE daytrade_flow_daily_v2 ADD COLUMN main_force_data_available INTEGER NOT NULL DEFAULT 1").run();
    if (!names.has("main_force_data_status")) await d1.prepare("ALTER TABLE daytrade_flow_daily_v2 ADD COLUMN main_force_data_status TEXT NOT NULL DEFAULT 'historical_ticks'").run();
  })().catch((error) => { schemaReady = null; throw error; });
  await schemaReady;
  return d1;
}
export async function saveDaytradeFlow(records: DaytradeFlowRecord[]) {
  if (!records.length) return;
  const d1 = await database();
  for (let start = 0; start < records.length; start += 60) await d1.batch(records.slice(start, start + 60).map((r) => d1.prepare(`INSERT INTO daytrade_flow_daily_v2 (ticker,name,market,trade_date,category,close_price,limit_up_price,day_change_pct,large_buy_amount,large_sell_amount,net_large_amount,turnover_amount,participation_rate,late_buy_concentration,suspicion_score,estimated_next_day_sell_amount,confirmed_reversal_rate,updated_at,main_force_data_available,main_force_data_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticker, trade_date) DO UPDATE SET name=excluded.name, market=excluded.market, category=excluded.category, close_price=excluded.close_price, limit_up_price=excluded.limit_up_price, day_change_pct=excluded.day_change_pct, large_buy_amount=excluded.large_buy_amount, large_sell_amount=excluded.large_sell_amount, net_large_amount=excluded.net_large_amount, turnover_amount=excluded.turnover_amount, participation_rate=excluded.participation_rate, late_buy_concentration=excluded.late_buy_concentration, suspicion_score=excluded.suspicion_score, estimated_next_day_sell_amount=excluded.estimated_next_day_sell_amount, confirmed_reversal_rate=excluded.confirmed_reversal_rate, updated_at=excluded.updated_at, main_force_data_available=excluded.main_force_data_available, main_force_data_status=excluded.main_force_data_status`).bind(r.ticker,r.name,r.market,r.tradeDate,r.category,r.closePrice,r.limitUpPrice,r.dayChangePct,r.largeBuyAmount,r.largeSellAmount,r.netLargeAmount,r.turnoverAmount,r.participationRate,r.lateBuyConcentration,r.suspicionScore,r.estimatedNextDaySellAmount,r.confirmedReversalRate,r.updatedAt,r.mainForceDataAvailable ? 1 : 0,r.mainForceDataStatus)));
}
export async function readDaytradeFlow(limit = 500) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT ticker,name,market,trade_date AS tradeDate,category,close_price AS closePrice,limit_up_price AS limitUpPrice,day_change_pct AS dayChangePct,large_buy_amount AS largeBuyAmount,large_sell_amount AS largeSellAmount,net_large_amount AS netLargeAmount,turnover_amount AS turnoverAmount,participation_rate AS participationRate,late_buy_concentration AS lateBuyConcentration,suspicion_score AS suspicionScore,estimated_next_day_sell_amount AS estimatedNextDaySellAmount,confirmed_reversal_rate AS confirmedReversalRate,updated_at AS updatedAt,main_force_data_available AS mainForceDataAvailable,main_force_data_status AS mainForceDataStatus FROM daytrade_flow_daily_v2 WHERE trade_date=(SELECT MAX(trade_date) FROM daytrade_flow_daily_v2) ORDER BY CASE category WHEN '漲停鎖定' THEN 1 WHEN '曾達漲停' THEN 2 ELSE 3 END, suspicion_score DESC LIMIT ?`).bind(Math.max(1, Math.min(2000, limit))).all<DaytradeFlowRecord>();
  return result.results.map((row) => ({ ...row, mainForceDataAvailable: Boolean(row.mainForceDataAvailable) }));
}

export async function readPreviousDaytradeFlow(signalTradeDate: string, limit = 1000) {
  const d1 = await database();
  const result = await d1.prepare(`SELECT ticker,name,market,trade_date AS tradeDate,category,close_price AS closePrice,limit_up_price AS limitUpPrice,day_change_pct AS dayChangePct,large_buy_amount AS largeBuyAmount,large_sell_amount AS largeSellAmount,net_large_amount AS netLargeAmount,turnover_amount AS turnoverAmount,participation_rate AS participationRate,late_buy_concentration AS lateBuyConcentration,suspicion_score AS suspicionScore,estimated_next_day_sell_amount AS estimatedNextDaySellAmount,confirmed_reversal_rate AS confirmedReversalRate,updated_at AS updatedAt,main_force_data_available AS mainForceDataAvailable,main_force_data_status AS mainForceDataStatus FROM daytrade_flow_daily_v2 WHERE trade_date=(SELECT MAX(trade_date) FROM daytrade_flow_daily_v2 WHERE trade_date < ?) ORDER BY net_large_amount DESC, ticker ASC LIMIT ?`)
    .bind(signalTradeDate, Math.max(1, Math.min(2000, limit)))
    .all<DaytradeFlowRecord>();
  return result.results.map((row) => ({ ...row, mainForceDataAvailable: Boolean(row.mainForceDataAvailable) }));
}
