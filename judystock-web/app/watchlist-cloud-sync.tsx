"use client";

import { useEffect } from "react";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import {
  WATCHLIST_DEVICE_ID_KEY,
  WATCHLIST_PENDING_OPERATIONS_KEY,
  WATCHLIST_STORAGE_KEY,
  cloneDefaultWatchlists,
  diffWatchlists,
  normalizeWatchlists,
  applyWatchlistOperations,
  type WatchlistDeviceKind,
  type WatchlistFolder,
  type WatchlistOperation,
  type WatchlistSyncStatus,
} from "../lib/watchlists";

type CloudPayload = {
  ok: boolean;
  exists?: boolean;
  authRequired?: boolean;
  signInUrl?: string;
  message?: string;
  watchlists?: WatchlistFolder[];
  revision?: number;
  updatedAt?: number;
};

type WatchlistWindow = Window & {
  __HANSTOCK_WATCHLIST_SYNC_STATUS__?: WatchlistSyncStatus;
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

function readLocalWatchlists() {
  try {
    const stored = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
    return stored ? normalizeWatchlists(JSON.parse(stored)) : cloneDefaultWatchlists();
  } catch {
    return cloneDefaultWatchlists();
  }
}

function readPendingOperations() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WATCHLIST_PENDING_OPERATIONS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.slice(0, 500) as WatchlistOperation[] : [];
  } catch {
    return [];
  }
}

function publishStatus(status: WatchlistSyncStatus) {
  (window as WatchlistWindow).__HANSTOCK_WATCHLIST_SYNC_STATUS__ = status;
  window.dispatchEvent(new CustomEvent<WatchlistSyncStatus>("hanstock-watchlists-sync-status", { detail: status }));
}

export function WatchlistCloudSync() {
  useEffect(() => {
    const deviceKind = detectDeviceKind();
    const deviceId = getDeviceId();
    let baseline: WatchlistFolder[] | null = null;
    let pending = readPendingOperations();
    let deferredLocalChanges = false;
    let busy = false;
    let stopped = false;
    let rerun = false;

    const savePending = () => {
      try {
        window.localStorage.setItem(WATCHLIST_PENDING_OPERATIONS_KEY, JSON.stringify(pending.slice(-500)));
      } catch {
        // The visible local list still works when browser storage is restricted.
      }
    };

    const applyCloud = (payload: CloudPayload) => {
      if (!Array.isArray(payload.watchlists)) return;
      const normalized = applyWatchlistOperations(normalizeWatchlists(payload.watchlists), pending);
      baseline = normalized;
      try {
        window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(normalized));
      } catch {
        // React panels still receive the canonical payload through the same-window event.
      }
      window.dispatchEvent(new CustomEvent("hanstock-watchlists-changed", {
        detail: { source: "cloud", watchlists: normalized, revision: payload.revision },
      }));
      publishStatus({
        state: "synced",
        label: "所有裝置已同步",
        detail: "各裝置使用同一個 ChatGPT 帳號即可共用；任一端修改都會同步。",
        updatedAt: payload.updatedAt ?? Date.now(),
      });
    };

    const handleFailurePayload = (payload: CloudPayload) => {
      if (payload.authRequired) {
        publishStatus({
          state: "auth-required",
          label: "登入後同步",
          detail: "請在各裝置使用同一個 ChatGPT 帳號登入。",
        });
        return true;
      }
      return false;
    };

    const request = async (method: "GET" | "POST", body?: Record<string, unknown>) => {
      const response = await fetch("/api/watchlists", {
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
        state: pending.length > 0 ? "syncing" : "connecting",
        label: pending.length > 0 ? "正在同步修改" : "正在連接雲端",
        detail: "自選股會以同一個 ChatGPT 帳號在所有裝置共用。",
      });
      try {
        if (!navigator.onLine) throw new Error("offline");
        const cloud = await request("GET");
        if (!cloud.response.ok) {
          if (handleFailurePayload(cloud.payload)) return;
          throw new Error(cloud.payload.message || "watchlist-read-failed");
        }

        if (!cloud.payload.exists) {
          const sentCount = pending.length;
          const initialized = await request("POST", {
            deviceId,
            deviceKind,
            watchlists: readLocalWatchlists(),
          });
          if (!initialized.response.ok) {
            if (handleFailurePayload(initialized.payload)) return;
            throw new Error(initialized.payload.message || "watchlist-initialize-failed");
          }
          pending = pending.slice(sentCount);
          deferredLocalChanges = false;
          savePending();
          applyCloud(initialized.payload);
          return;
        }

        baseline = normalizeWatchlists(cloud.payload.watchlists);
        if (deferredLocalChanges) {
          pending = [...pending, ...diffWatchlists(baseline, readLocalWatchlists())].slice(-500);
          deferredLocalChanges = false;
          savePending();
        }
        if (pending.length > 0) {
          const sentCount = pending.length;
          const updated = await request("POST", {
            deviceId,
            deviceKind,
            operations: pending.slice(0, sentCount),
          });
          if (!updated.response.ok) {
            if (handleFailurePayload(updated.payload)) return;
            throw new Error(updated.payload.message || "watchlist-update-failed");
          }
          pending = pending.slice(sentCount);
          savePending();
          applyCloud(updated.payload);
          return;
        }
        applyCloud(cloud.payload);
      } catch (error) {
        const offline = !navigator.onLine || (error instanceof Error && error.message === "offline");
        publishStatus({
          state: offline ? "offline" : "error",
          label: offline ? "離線保留" : "同步稍後重試",
          detail: offline ? "目前沒有網路；修改已保留，恢復連線後會自動同步。" : "雲端暫時無法連接，本機名單不會遺失。",
        });
      } finally {
        busy = false;
        if (rerun && !stopped) void synchronize();
      }
    };

    const queueLocalOperations = (operations?: unknown) => {
      const local = readLocalWatchlists();
      const explicit = Array.isArray(operations) ? operations as WatchlistOperation[] : [];
      if (explicit.length === 0 && !baseline) {
        deferredLocalChanges = true;
        void synchronize();
        return;
      }
      const derived = explicit.length > 0 ? explicit : diffWatchlists(baseline ?? local, local);
      if (derived.length === 0) return;
      pending = [...pending, ...derived].slice(-500);
      baseline = local;
      savePending();
      publishStatus({ state: "syncing", label: "正在同步修改", detail: "這次修改正在傳送到其他裝置。" });
      void synchronize();
    };

    const onLocalChange = (event: Event) => {
      const detail = (event as CustomEvent<{ operations?: WatchlistOperation[] }>).detail;
      queueLocalOperations(detail?.operations);
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== "hanstock-watchlists-local-changed") return;
      queueLocalOperations(event.data.operations);
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY) queueLocalOperations();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void synchronize();
    };
    const onOnline = () => void synchronize();

    window.addEventListener("hanstock-watchlists-local-changed", onLocalChange);
    window.addEventListener("message", onMessage);
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onOnline);
    window.addEventListener("pageshow", onOnline);
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    const timer = createVisibilityGatedInterval(onVisible, 4_000);
    void synchronize();

    return () => {
      stopped = true;
      timer.cancel();
      window.removeEventListener("hanstock-watchlists-local-changed", onLocalChange);
      window.removeEventListener("message", onMessage);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onOnline);
      window.removeEventListener("pageshow", onOnline);
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return null;
}
