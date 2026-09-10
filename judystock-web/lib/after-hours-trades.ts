import type { EarlySellSignalRecord } from "../db/early-sell-history";
import { normalizeInstantLargeOrderSignal } from "./instant-large-thresholds.mjs";
import { hasCapturedMatchingTopGroup } from "./main-force-group-ranks";

export type AfterHoursForceQuote = { forcePct: number; price: number };

/** Official unmatched order imbalance, not an inference about investor identity. */
export function calculateAfterHoursForcePct(executed: number, unexecutedBuy: number, unexecutedSell: number): number | null {
  if (![executed, unexecutedBuy, unexecutedSell].every(value => Number.isFinite(value) && value >= 0)
    || (unexecutedBuy > 0 && unexecutedSell > 0)) return null;
  const unmatched = unexecutedBuy + unexecutedSell;
  if (executed + unmatched === 0) return null;
  return (unexecutedBuy - unexecutedSell) / (executed + unmatched) * 100;
}

function volume(value: unknown, blankIsZero = false): number {
  if (typeof value !== "number" && typeof value !== "string") return NaN;
  const text = String(value).trim().replaceAll(",", "");
  if (!text) return blankIsZero ? 0 : NaN;
  return /^\d+(?:\.\d+)?$/.test(text) ? Number(text) : NaN;
}

/** Reject undated TWSE OpenAPI snapshots: they can still contain yesterday's data. */
export function parseAfterHoursForceQuotes(payload: unknown, market: "twse" | "tpex", tradeDate: string): Map<string, AfterHoursForceQuote> {
  const result = new Map<string, AfterHoursForceQuote>();
  const add = (ticker: unknown, priceValue: unknown, executed: unknown, buy: unknown, sell: unknown, blankIsZero: boolean) => {
    const code = String(ticker ?? "").trim();
    const price = volume(priceValue);
    const forcePct = calculateAfterHoursForcePct(volume(executed, blankIsZero), volume(buy, blankIsZero), volume(sell, blankIsZero));
    if (/^[0-9A-Z]{2,12}$/.test(code) && price > 0 && forcePct !== null) result.set(code, { forcePct, price });
  };
  if (market === "twse") {
    const data = payload as { stat?: string; date?: string; fields?: string[]; data?: unknown[][] } | null;
    if (data?.stat !== "OK" || data.date !== tradeDate.replaceAll("-", "") || !Array.isArray(data.fields) || !Array.isArray(data.data)) return result;
    const indexes = ["證券代號", "成交價", "成交數量", "最後揭示買量", "最後揭示賣量"].map(field => data.fields!.indexOf(field));
    if (indexes.some(index => index < 0)) return result;
    for (const row of data.data) if (Array.isArray(row)) {
      add(row[indexes[0]], row[indexes[1]], row[indexes[2]], row[indexes[3]], row[indexes[4]], true);
    }
  } else if (Array.isArray(payload)) {
    const rocDate = `${Number(tradeDate.slice(0, 4)) - 1911}${tradeDate.slice(5).replaceAll("-", "")}`;
    for (const row of payload) {
      if (!row || typeof row !== "object" || row.Date !== rocDate) continue;
      add(row.SecuritiesCompanyCode, row.Close, row.TradeVolume, row.BidVolumeUnexecute, row.OfferVolumeUnexecute, false);
    }
  }
  return result;
}

export function afterHoursForceFromNote(note: string): number | null {
  const match = note.match(/(?:^|｜)盤後大戶力\s+([+-]?\d+(?:\.\d+)?)%(?=｜|$)/u);
  const value = match ? Number(match[1]) : NaN;
  return Number.isFinite(value) && Math.abs(value) <= 100 ? value : null;
}

/** A recorded fixed-price after-hours execution, not a reconstructed intraday signal. */
export function isAfterHoursTradeSignal(value: unknown): value is EarlySellSignalRecord {
  if (!value || typeof value !== "object") return false;
  const row = value as EarlySellSignalRecord;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(row.tradeDate) || !/^[0-9A-Z]{2,12}$/.test(row.ticker)
    || !["instantLargeBuy", "instantLargeSell"].includes(row.kind)
    || typeof row.name !== "string" || typeof row.label !== "string" || typeof row.note !== "string"
    || !Number.isFinite(row.barTs) || !Number.isFinite(row.price) || row.price <= 0) return false;
  const start = Date.parse(`${row.tradeDate}T14:30:00+08:00`);
  return row.barTs >= start && row.barTs < start + 60_000
    && Boolean(normalizeInstantLargeOrderSignal(row)) && hasCapturedMatchingTopGroup(row);
}

export function normalizeAfterHoursTrades(value: unknown, tradeDate: string, forceQuotes?: ReadonlyMap<string, AfterHoursForceQuote>): EarlySellSignalRecord[] {
  if (!Array.isArray(value)) return [];
  const byKey = new Map<string, EarlySellSignalRecord>();
  for (const row of value) {
    if (!isAfterHoursTradeSignal(row) || row.tradeDate !== tradeDate) continue;
    const normalized = normalizeInstantLargeOrderSignal(row) as EarlySellSignalRecord;
    let note = normalized.note.includes("14:30 盤後定價成交") ? normalized.note : `${normalized.note}｜14:30 盤後定價成交`;
    const quote = forceQuotes?.get(row.ticker);
    // A same-date official fixed price must also agree with this execution.
    if (quote && Number.isFinite(quote.forcePct) && Math.abs(quote.forcePct) <= 100 && Math.abs(quote.price - row.price) < 0.000001) {
      note = note.replace(/(?:^|｜)盤後大戶力\s+[+-]?\d+(?:\.\d+)?%/gu, "");
      note += `｜盤後大戶力 ${quote.forcePct > 0 ? "+" : ""}${quote.forcePct.toFixed(1)}%`;
    }
    byKey.set(`${row.ticker}:${row.kind}:${row.barTs}`, { ...normalized, note });
  }
  return [...byKey.values()];
}
