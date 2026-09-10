import { readEarlySellSignals } from "./early-sell-history";
import { afterHoursWatchlistStocks } from "../lib/after-hours-watchlist";
import { loadTwseClosedTradingDates, resolveIntradaySignalCutoverDate } from "../lib/intraday-signal-session";
import { refreshAfterHoursTrades } from "./after-hours-trades";

export async function loadAfterHoursWatchlist() {
  const closedDates = await loadTwseClosedTradingDates();
  const now = new Date();
  const marketTradeDate = resolveIntradaySignalCutoverDate(now, closedDates);
  // Shift the existing exchange-aware 08:45 boundary to 14:30 for this folder.
  const tradeDate = resolveIntradaySignalCutoverDate(new Date(now.getTime() - 345 * 60_000), closedDates);
  const collection = await refreshAfterHoursTrades(tradeDate).catch(() => ({status: "unavailable" as const, imported: 0}));
  const rows = await readEarlySellSignals({tradeDate, kinds: ["instantLargeBuy", "instantLargeSell"], minuteOfDay: 14 * 60 + 30, limit: 10_000});
  let listDate = tradeDate;
  let stocks = afterHoursWatchlistStocks(rows, tradeDate);
  if (!stocks.length && collection.status !== "ready") {
    const previous = await readEarlySellSignals({kinds: ["instantLargeBuy", "instantLargeSell"], minuteOfDay: 870, limit: 10_000});
    const latest = previous.find(row => row.tradeDate < tradeDate)?.tradeDate;
    if (latest) { listDate = latest; stocks = afterHoursWatchlistStocks(previous, latest); }
  }
  return {tradeDate: listDate, targetTradeDate: tradeDate, marketTradeDate, stocks, sourceStatus: collection.status,
    message: collection.status === "ready" ? "" : stocks.length
      ? `正在確認 ${tradeDate} 盤後成交，先保留 ${listDate} 已存名單。`
      : "14:30 盤後成交資料確認中，系統會自動重試。"};
}
