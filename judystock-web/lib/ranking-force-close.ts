export type RankingForceClose = {
  ticker: string;
  tradeDate: string;
  barTs: number;
  forcePct: number;
  buyAmount: number;
  sellAmount: number;
  turnoverAmount: number;
  source: "historical-ticks";
};

/** The historical endpoint uses the same main-order threshold as the live chart,
 * and sums actual trade amounts, including the closing auction.
 */
export function parseRankingForceClose(row: Record<string, unknown>, tradeDate: string): RankingForceClose | null {
  const ticker = String(row.ticker ?? "");
  if (!/^[1-9]\d{3}$/.test(ticker) || row.trade_date !== tradeDate || row.main_force_data_available !== true || row.main_force_data_status !== "historical_ticks") return null;
  const fields = [row.large_buy_amount, row.large_sell_amount, row.total_turnover_amount];
  if (fields.some(value => value == null || value === "" || !Number.isFinite(Number(value)))) return null;
  const [buyAmount, sellAmount, turnoverAmount] = fields.map(Number);
  if (buyAmount < 0 || sellAmount < 0 || turnoverAmount <= 0) return null;
  return {
    ticker, tradeDate, barTs: Date.parse(`${tradeDate}T13:30:00+08:00`),
    forcePct: (buyAmount - sellAmount) / turnoverAmount * 100,
    buyAmount, sellAmount, turnoverAmount, source: "historical-ticks",
  };
}
