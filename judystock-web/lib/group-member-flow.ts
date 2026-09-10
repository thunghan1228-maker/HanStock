export type GroupDaytradeFlowRow = {
  ticker?: unknown;
  name?: unknown;
  trade_date?: unknown;
  large_buy_amount?: unknown;
  large_sell_amount?: unknown;
  total_turnover_amount?: unknown;
  close_price?: unknown;
  main_force_data_available?: unknown;
  main_force_data_status?: unknown;
};

export function rocDateToIso(value: unknown) {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (!/^\d{7}$/.test(digits)) return "";
  const year = Number(digits.slice(0, 3)) + 1911;
  const month = Number(digits.slice(3, 5));
  const day = Number(digits.slice(5, 7));
  if (year < 2000 || month < 1 || month > 12 || day < 1 || day > 31) return "";
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * 族群明細必須沿用隔日沖排行相同的「歷史逐筆大單」口徑。
 * 後台若明確標示尚未回補，就不能把缺資料顯示成 0。
 */
export function readAuthoritativeGroupNetAmount(row: GroupDaytradeFlowRow | null | undefined) {
  if (!row || row.main_force_data_available === false || row.main_force_data_available === 0) return null;
  const buy = Number(row.large_buy_amount);
  const sell = Number(row.large_sell_amount);
  const turnover = Number(row.total_turnover_amount);
  if (!Number.isFinite(buy) || !Number.isFinite(sell) || !Number.isFinite(turnover) || turnover <= 0) return null;
  return buy - sell;
}

export function readAuthoritativeGroupTurnoverAmount(row: GroupDaytradeFlowRow | null | undefined) {
  const turnover = Number(row?.total_turnover_amount);
  return Number.isFinite(turnover) && turnover > 0 ? turnover : null;
}
