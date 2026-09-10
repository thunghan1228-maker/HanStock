import { normalizeWatchlistStock, type WatchlistDeviceKind, type WatchlistStock } from "./watchlists";

export const INTRADAY_TRACKING_STORAGE_KEY = "hanstock-intraday-stock-tracking-v1";
export const INTRADAY_TRACKING_PENDING_KEY = "hanstock-intraday-tracking-pending-v1";
export const INTRADAY_TRACKING_MIGRATED_KEY = "hanstock-intraday-tracking-cloud-migrated-v1";
export const INTRADAY_TRACKING_CHANGED_EVENT = "hanstock-intraday-tracking-changed";
export const INTRADAY_TRACKING_LOCAL_CHANGED_EVENT = "hanstock-intraday-tracking-local-changed";
export const INTRADAY_TRACKING_SYNC_STATUS_EVENT = "hanstock-intraday-tracking-sync-status";

export type TrackingKind =
  | "mainForce"
  | "fourGate"
  | "instantLarge"
  | "extraLargeSell"
  | "extraLargeBuy"
  | "largeForce"
  | "immediateBuy200"
  | "immediateSell200";

export const TRACKING_KINDS: TrackingKind[] = [
  "mainForce",
  "fourGate",
  "instantLarge",
  "extraLargeSell",
  "extraLargeBuy",
  "largeForce",
  "immediateBuy200",
  "immediateSell200",
];

export type IntradayTrackingSettings = {
  stocks: WatchlistStock[];
  selected: TrackingKind[];
  remindersEnabled: boolean;
};

export type IntradayTrackingSyncState =
  | "connecting"
  | "syncing"
  | "synced"
  | "auth-required"
  | "offline"
  | "error";

export type IntradayTrackingSyncStatus = {
  state: IntradayTrackingSyncState;
  label: string;
  detail: string;
  updatedAt?: number;
};

export type StoredIntradayTrackingState = {
  userEmail: string;
  settings: IntradayTrackingSettings;
  revision: number;
  updatedByDeviceId: string;
  updatedByDeviceKind: WatchlistDeviceKind;
  createdAt: number;
  updatedAt: number;
};

export function defaultIntradayTrackingSettings(): IntradayTrackingSettings {
  return {
    stocks: [],
    selected: [...TRACKING_KINDS],
    remindersEnabled: false,
  };
}

export function normalizeIntradayTrackingSettings(value: unknown): IntradayTrackingSettings {
  if (!value || typeof value !== "object") return defaultIntradayTrackingSettings();
  const raw = value as Partial<IntradayTrackingSettings>;
  const seen = new Set<string>();
  const stocks = (Array.isArray(raw.stocks) ? raw.stocks : [])
    .map(normalizeWatchlistStock)
    .filter((stock): stock is WatchlistStock => {
      if (!stock || seen.has(stock.ticker)) return false;
      seen.add(stock.ticker);
      return true;
    })
    .slice(0, 1_000);
  const validKinds = new Set<TrackingKind>(TRACKING_KINDS);
  const selected = Array.isArray(raw.selected)
    ? [...new Set(raw.selected.filter((kind): kind is TrackingKind => validKinds.has(kind as TrackingKind)))]
    : [...TRACKING_KINDS];
  // 舊版設定沒有盤中大戶力；第一次讀取時自動加入，避免新訊號上線後
  // 使用者的既有追蹤清單看得到選項卻收不到提醒。後續保存即會固定記錄。
  if (Array.isArray(raw.selected) && !raw.selected.includes("largeForce")) selected.push("largeForce");
  return {
    stocks,
    selected,
    remindersEnabled: raw.remindersEnabled === true,
  };
}
