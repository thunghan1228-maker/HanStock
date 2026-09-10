"use client";

import { useEffect } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import {
  INTRADAY_TRACKING_CHANGED_EVENT,
  INTRADAY_TRACKING_LOCAL_CHANGED_EVENT,
  INTRADAY_TRACKING_MIGRATED_KEY,
  INTRADAY_TRACKING_PENDING_KEY,
  INTRADAY_TRACKING_STORAGE_KEY,
  INTRADAY_TRACKING_SYNC_STATUS_EVENT,
  defaultIntradayTrackingSettings,
  normalizeIntradayTrackingSettings,
  type IntradayTrackingSettings,
  type IntradayTrackingSyncStatus,
} from "../lib/intraday-tracking";
import { WATCHLIST_DEVICE_ID_KEY, type WatchlistDeviceKind } from "../lib/watchlists";

type CloudPayload = {
  ok: boolean;
  exists?: boolean;
  authRequired?: boolean;
  message?: string;
  settings?: IntradayTrackingSettings;
  revision?: number;
  updatedAt?: number;
};

type PendingSettings = {
  settings: IntradayTrackingSettings;
  queuedAt: number;
};

type TrackingWindow = Window & {
  __HANSTOCK_INTRADAY_TRACKING_SYNC_STATUS__?: IntradayTrackingSyncStatus;
};

function detectDeviceKind(): WatchlistDeviceKind {
  const agent = navigator.userAgent;
  if (/iPad/i.test(agent) || (/Macintosh/i.test(agent) && navigator.maxTouchPoints > 1)) return "ipad";
  if (/iPhone/i.test(agent)) return "iphone";
  return "desktop";
}

function getDeviceId() {
  const existing = window.localStorage.getItem(WATCHLIST_DEVICE_ID_KEY);
  if (existing) return existing;
  const value = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `device-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  window.localStorage.setItem(WATCHLIST_DEVICE_ID_KEY, value);
  return value;
}

function readLocalSettings() {
  try {
    const stored = window.localStorage.getItem(INTRADAY_TRACKING_STORAGE_KEY);
    return stored ? normalizeIntradayTrackingSettings(JSON.parse(stored)) : defaultIntradayTrackingSettings();
  } catch {
    return defaultIntradayTrackingSettings();
  }
}

function readPendingSettings(): PendingSettings | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(INTRADAY_TRACKING_PENDING_KEY) ?? "null") as Partial<PendingSettings> | null;
    if (!parsed || typeof parsed.queuedAt !== "number") return null;
    return {
      settings: normalizeIntradayTrackingSettings(parsed.settings),
      queuedAt: parsed.queuedAt,
    };
  } catch {
    return null;
  }
}

function publishStatus(status: IntradayTrackingSyncStatus) {
  (window as TrackingWindow).__HANSTOCK_INTRADAY_TRACKING_SYNC_STATUS__ = status;
  window.dispatchEvent(new CustomEvent<IntradayTrackingSyncStatus>(INTRADAY_TRACKING_SYNC_STATUS_EVENT, { detail: status }));
}

export function IntradayTrackingCloudSync() {
  useEffect(() => {
    const deviceKind = detectDeviceKind();
    const deviceId = getDeviceId();
    let pending = readPendingSettings();
    let migrated = window.localStorage.getItem(INTRADAY_TRACKING_MIGRATED_KEY) === "1";
    let busy = false;
    let rerun = false;
    let stopped = false;

    const savePending = () => {
      try {
        if (pending) window.localStorage.setItem(INTRADAY_TRACKING_PENDING_KEY, JSON.stringify(pending));
        else window.localStorage.removeItem(INTRADAY_TRACKING_PENDING_KEY);
      } catch {
        // The current device keeps working if browser storage is restricted.
      }
    };

    const markMigrated = () => {
      migrated = true;
      try { window.localStorage.setItem(INTRADAY_TRACKING_MIGRATED_KEY, "1"); } catch { /* retry next session */ }
    };

    const applyCloud = (payload: CloudPayload) => {
      if (!payload.settings) return;
      const settings = normalizeIntradayTrackingSettings(payload.settings);
      try {
        window.localStorage.setItem(INTRADAY_TRACKING_STORAGE_KEY, JSON.stringify(settings));
      } catch {
        // Open panels still receive the canonical settings through the event below.
      }
      window.dispatchEvent(new CustomEvent(INTRADAY_TRACKING_CHANGED_EVENT, {
        detail: { source: "cloud", settings, revision: payload.revision },
      }));
      publishStatus({
        state: "synced",
        label: "所有裝置已同步",
        detail: "追蹤股票、訊號勾選與提醒開關已使用同一個 ChatGPT 帳號同步。",
        updatedAt: payload.updatedAt ?? Date.now(),
      });
    };

    const handleFailurePayload = (payload: CloudPayload) => {
      if (!payload.authRequired) return false;
      publishStatus({
        state: "auth-required",
        label: "登入後全裝置同步",
        detail: "請在各裝置使用同一個 ChatGPT 帳號登入。",
      });
      return true;
    };

    const request = async (method: "GET" | "POST", body?: Record<string, unknown>) => {
      const response = await fetch("/api/intraday-tracking", {
        method,
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      const payload = await response.json() as CloudPayload;
      return { response, payload };
    };

    const synchronize = async () => {
      if (stopped) return;
      if (busy) {
        rerun = true;
        return;
      }
      busy = true;
      rerun = false;
      publishStatus({
        state: pending ? "syncing" : "connecting",
        label: pending ? "正在同步修改" : "正在連接全裝置同步",
        detail: "追蹤清單與所有追蹤設定會同步到其他裝置。",
      });
      try {
        if (!navigator.onLine) throw new Error("offline");
        const cloud = await request("GET");
        if (!cloud.response.ok) {
          if (handleFailurePayload(cloud.payload)) return;
          throw new Error(cloud.payload.message || "intraday-tracking-read-failed");
        }

        if (!cloud.payload.exists) {
          const sent = pending;
          const initialized = await request("POST", {
            deviceId,
            deviceKind,
            settings: sent?.settings ?? readLocalSettings(),
          });
          if (!initialized.response.ok) {
            if (handleFailurePayload(initialized.payload)) return;
            throw new Error(initialized.payload.message || "intraday-tracking-initialize-failed");
          }
          if (sent && pending?.queuedAt === sent.queuedAt) pending = null;
          savePending();
          if (!pending) {
            markMigrated();
            applyCloud(initialized.payload);
          }
          else rerun = true;
          return;
        }

        if (!migrated && !pending) {
          const hasLegacyLocalSettings = window.localStorage.getItem(INTRADAY_TRACKING_STORAGE_KEY) !== null;
          if (hasLegacyLocalSettings) {
            const local = readLocalSettings();
            const canonical = normalizeIntradayTrackingSettings(cloud.payload.settings);
            const tickers = new Set(canonical.stocks.map((stock) => stock.ticker));
            const merged = normalizeIntradayTrackingSettings({
              stocks: [...canonical.stocks, ...local.stocks.filter((stock) => !tickers.has(stock.ticker))],
              selected: local.selected,
              remindersEnabled: local.remindersEnabled,
            });
            if (JSON.stringify(merged) !== JSON.stringify(canonical)) {
              pending = { settings: merged, queuedAt: Date.now() };
              savePending();
            }
          }
          if (!pending) markMigrated();
        }

        if (pending) {
          const sent = pending;
          const updated = await request("POST", {
            deviceId,
            deviceKind,
            settings: sent.settings,
          });
          if (!updated.response.ok) {
            if (handleFailurePayload(updated.payload)) return;
            throw new Error(updated.payload.message || "intraday-tracking-update-failed");
          }
          if (pending?.queuedAt === sent.queuedAt) pending = null;
          savePending();
          if (!pending) {
            markMigrated();
            applyCloud(updated.payload);
          }
          else rerun = true;
          return;
        }
        applyCloud(cloud.payload);
      } catch (error) {
        const offline = !navigator.onLine || (error instanceof Error && error.message === "offline");
        publishStatus({
          state: offline ? "offline" : "error",
          label: offline ? "離線修改已保留" : "同步稍後重試",
          detail: offline ? "恢復連線後會自動同步到所有裝置。" : "雲端暫時無法連接，本機追蹤資料不會遺失。",
        });
      } finally {
        busy = false;
        if (rerun && !stopped) void synchronize();
      }
    };

    const queueLocalSettings = (value?: unknown) => {
      pending = {
        settings: normalizeIntradayTrackingSettings(value ?? readLocalSettings()),
        queuedAt: Date.now(),
      };
      savePending();
      publishStatus({ state: "syncing", label: "正在同步修改", detail: "這次修改正在傳送到所有裝置。" });
      void synchronize();
    };

    const onLocalChange = (event: Event) => {
      const detail = (event as CustomEvent<{ settings?: IntradayTrackingSettings }>).detail;
      queueLocalSettings(detail?.settings);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void synchronize();
    };
    const onOnline = () => void synchronize();

    window.addEventListener(INTRADAY_TRACKING_LOCAL_CHANGED_EVENT, onLocalChange);
    window.addEventListener("focus", onOnline);
    window.addEventListener("pageshow", onOnline);
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    const timer = createVisibilityGatedInterval(onVisible, 4_000);
    void synchronize();

    return () => {
      stopped = true;
      timer.cancel();
      window.removeEventListener(INTRADAY_TRACKING_LOCAL_CHANGED_EVENT, onLocalChange);
      window.removeEventListener("focus", onOnline);
      window.removeEventListener("pageshow", onOnline);
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return null;
}
