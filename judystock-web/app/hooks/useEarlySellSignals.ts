"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createVisibilityGatedInterval } from "../../lib/useVisibilityGatedInterval";
import { type MainForceGroupRankings } from "../../lib/main-force-group-ranks";
import { isActiveIntradayCenterSignal } from "../../lib/intraday-center-signals";
import { normalizeInstantLargeOrderSignal } from "../../lib/instant-large-thresholds.mjs";
import { hasQualifiedExtraLargeTriggerForce } from "../../lib/intraday-extra-large-sell";
import { filterAjIntradayLargeForceSignals, filterCandidateIntradayLargeForceSignals, type AjLargeForceGroupTransition } from "../../lib/intraday-large-force";
import {
  type DaytradeEarlySellSignal,
  type DaytradeEarlySellPayload,
  type IntradaySignalStockMeta,
  type IntradaySignalTechnicalMeta,
  type IntradayLargeForceValueRow,
  type AjLargeForceFilterPayload,
  type AjLargeForceFilterStatus,
  type IntradaySignalCenterMode,
  type FloatingPanelPosition,
  type FloatingPanelSize,
  EARLY_SELL_SEEN_KEY,
  EARLY_SELL_PINNED_KEY,
  EARLY_SELL_PINNED_QUEUE_KEY,
  EARLY_SELL_ALERTS_ENABLED_KEY,
  EARLY_SELL_TOAST_POSITION_KEY,
  EARLY_SELL_TOAST_SIZE_KEY,
  EARLY_SELL_LARGE_FORCE_VALUES_KEY,
  isFourGateSignal,
  isExtraLargeSellSignal,
  isExtraLargeBuySignal,
  isLargeForceSignal,
  isInstantLargeSignal,
  isFiveMinuteTwelveShortSignal,
  isFiveMinuteOnePlusTwoLongSignal,
  intradaySignalKey,
  taipeiTradeDate,
  taipeiSessionState,
} from "../../lib/early-sell-signals";

type EarlySellSignalsOptions = {
  groupRankings?: MainForceGroupRankings;
  centerOpen: boolean;
  centerMode: IntradaySignalCenterMode;
  setPopupPosition: (position: FloatingPanelPosition) => void;
  setPopupSize: (size: FloatingPanelSize) => void;
  formatTwd: (value: number) => string;
  formatWatchlistPrice: (value: number | null | undefined, fallback: string) => string;
};


const HISTORICAL_EARLY_SELL_DEMO: DaytradeEarlySellSignal[] = [
  { tradeDate: "2026-08-14", ticker: "2330", name: "台積電", kind: "daytradeEarlyBuy50", label: "盤中大單買進達前日預估隔日賣壓 50%", barTs: 1786669260000, price: 125, note: "前日預估隔日賣壓 8.00 億｜盤中大單買進 4.40 億｜比例 55.0%", demo: true },
  { tradeDate: "2026-08-14", ticker: "2303", name: "聯電", kind: "daytradeEarlySell50", label: "早盤大單賣出達前日預估隔日賣壓 50%", barTs: 1786669200000, price: 123, note: "前日預估隔日賣壓 11.94 億｜早盤大單賣出 7.71 億｜比例 64.6%", demo: true },
  { tradeDate: "2026-08-14", ticker: "2603", name: "長榮", kind: "daytradeEarlySell50", label: "早盤大單賣出達前日預估隔日賣壓 50%", barTs: 1786669200000, price: 213, note: "前日預估隔日賣壓 9,797.4 萬｜早盤大單賣出 1.27 億｜比例 129.8%", demo: true },
  { tradeDate: "2026-08-14", ticker: "3231", name: "緯創", kind: "daytradeEarlySell50", label: "早盤大單賣出達前日預估隔日賣壓 50%", barTs: 1786669200000, price: 201.5, note: "前日預估隔日賣壓 11.93 億｜早盤大單賣出 6.12 億｜比例 51.3%", demo: true },
  { tradeDate: "2026-08-14", ticker: "2615", name: "萬海", kind: "daytradeEarlySell50", label: "早盤大單賣出達前日預估隔日賣壓 50%", barTs: 1786669200000, price: 87.1, note: "前日預估隔日賣壓 7,392.2 萬｜早盤大單賣出 4,203.4 萬｜比例 56.9%", demo: true },
  { tradeDate: "2026-08-14", ticker: "3006", name: "晶豪科", kind: "daytradeEarlySell50", label: "早盤大單賣出達前日預估隔日賣壓 50%", barTs: 1786669200000, price: 280.5, note: "前日預估隔日賣壓 7,663.1 萬｜早盤大單賣出 3,986.8 萬｜比例 52.0%", demo: true },
];

function isIntradaySignalCollectionWindow() {
  const now = taipeiSessionState();
  return now.weekday !== "Sat" && now.weekday !== "Sun" && now.minutes >= 9 * 60 && now.minutes <= 13 * 60 + 35;
}

function isLargeForceBackfillWindow() {
  const now = taipeiSessionState();
  // 收盤後留 1.5 小時緩衝讓官方資料回補；過了 15:00 再沒完成就停到隔天，
  // 避免來源離線時每 8 秒重試到半夜。
  return now.weekday !== "Sat" && now.weekday !== "Sun" && now.minutes >= 9 * 60 && now.minutes <= 15 * 60;
}

function normalizeDisplayedInstantLargeSignals(signals: DaytradeEarlySellSignal[]) {
  return signals.flatMap((signal) => {
    if (!isActiveIntradayCenterSignal(signal)) return [];
    if (!isInstantLargeSignal(signal)) return [signal];
    const normalized = normalizeInstantLargeOrderSignal(signal);
    return normalized ? [normalized as DaytradeEarlySellSignal] : [];
  });
}

function mergePermanentSignalRows(
  current: DaytradeEarlySellSignal[],
  incoming: DaytradeEarlySellSignal[],
  tradeDate: string | undefined,
) {
  const activeDate = tradeDate || incoming[0]?.tradeDate || current[0]?.tradeDate;
  const retained = (activeDate ? current.filter((signal) => signal.tradeDate === activeDate) : current).filter(isActiveIntradayCenterSignal).filter(hasQualifiedExtraLargeTriggerForce);
  incoming = incoming.filter(isActiveIntradayCenterSignal).filter(hasQualifiedExtraLargeTriggerForce);
  if (incoming.length === 0) return retained;
  const merged = new Map([...retained, ...incoming].map((signal) => [
    intradaySignalKey(signal),
    signal,
  ]));
  return [...merged.values()].sort((left, right) => right.barTs - left.barTs);
}

export function useEarlySellSignals({
  groupRankings, centerOpen, centerMode,
  setPopupPosition, setPopupSize, formatTwd, formatWatchlistPrice,
}: EarlySellSignalsOptions) {
  const [queue, setQueue] = useState<DaytradeEarlySellSignal[]>([]);
  const [popupPinned, setPopupPinned] = useState(true);
  const [signalAlertsEnabled, setSignalAlertsEnabled] = useState(true);
  const [extraLargeCheck, setExtraLargeCheck] = useState<"checking" | "complete" | "incomplete">("checking");

  const [todaySignals, setTodaySignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [fourGateSignals, setFourGateSignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [mainForceSignals, setMainForceSignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [extraLargeSellSignals, setExtraLargeSellSignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [extraLargeBuySignals, setExtraLargeBuySignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [largeForceSignals, setLargeForceSignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [largeForceAjTransitions, setLargeForceAjTransitions] = useState<Record<string, AjLargeForceGroupTransition>>({});
  const [largeForceAjStatus, setLargeForceAjStatus] = useState<AjLargeForceFilterStatus>("idle");
  const [largeForceAjPreviousDates, setLargeForceAjPreviousDates] = useState<string[]>([]);
  const [instantLargeSignals, setInstantLargeSignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [instantLargeCollector, setInstantLargeCollector] = useState<DaytradeEarlySellPayload["instantLargeCollector"]>(null);
  const [signalSnapshotReady, setSignalSnapshotReady] = useState(false);
  const [signalFeed, setSignalFeed] = useState<NonNullable<DaytradeEarlySellPayload["signalFeed"]> | null>(null);
  const [signalSyncError, setSignalSyncError] = useState(false);
  const [signalCollection, setSignalCollection] = useState({ checkedAt: 0, sourceAt: 0, error: false });
  const [signalGroupRankings, setSignalGroupRankings] = useState<MainForceGroupRankings | undefined>(undefined);
  const [historySignals, setHistorySignals] = useState<DaytradeEarlySellSignal[]>([]);
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState(taipeiTradeDate);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyMessage, setHistoryMessage] = useState("");
  const [stockMeta, setStockMeta] = useState<Record<string, IntradaySignalStockMeta>>({});
  const [signalTechnicalMeta, setSignalTechnicalMeta] = useState<Record<string, IntradaySignalTechnicalMeta>>({});
  const [signalLargeForceValues, setSignalLargeForceValues] = useState<Record<string, IntradayLargeForceValueRow>>({});
  const [largeForceValueStorageReady, setLargeForceValueStorageReady] = useState(false);
  const seenKeys = useRef<Set<string>>(new Set());
  const storageReady = useRef(false);
  const popupStorageReady = useRef(false);
  const popupInitialSeeded = useRef(false);
  const signalAlertsEnabledRef = useRef(true);
  const groupRankingsRef = useRef<MainForceGroupRankings | undefined>(groupRankings);
  const signalPollInFlight = useRef(false);
  const instantLargeCollectorPollInFlight = useRef(false);
  const liveSignalCollectInFlight = useRef(false);
  const fullSignalCollectInFlight = useRef(false);
  const extraLargeBackfillInFlight = useRef(false);
  const extraLargeBackfillCompleted = useRef(false);
  const largeForceBackfillInFlight = useRef(false);
  const largeForceBackfillCompleted = useRef(false);
  const largeForceAjTradeDateRef = useRef("");
  const dismissedPopupSignalKeys = useRef<Set<string>>(new Set());
  const historyDateChanged = useRef(false);

  useEffect(() => {
    if (groupRankings?.strong?.length || groupRankings?.weak?.length) {
      groupRankingsRef.current = groupRankings;
    }
  }, [groupRankings]);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(EARLY_SELL_LARGE_FORCE_VALUES_KEY) ?? "{}") as Record<string, IntradayLargeForceValueRow>;
      const tradeDate = taipeiTradeDate();
      const restored = Object.fromEntries(Object.entries(saved).filter(([key, row]) =>
        key.startsWith(`${tradeDate}:`)
          && row?.tradeDate === tradeDate
          && typeof row.ticker === "string"
          && (row.forcePct === null || Number.isFinite(row.forcePct)),
      ));
      if (Object.keys(restored).length) setSignalLargeForceValues(restored);
    } catch {
      // iPad Safari 可能因隱私模式停用儲存；畫面仍可使用本次頁面的即時值。
    } finally {
      setLargeForceValueStorageReady(true);
    }
  }, []);

  useEffect(() => {
    if (!largeForceValueStorageReady) return;
    try {
      const tradeDate = taipeiTradeDate();
      const currentSession = Object.fromEntries(Object.entries(signalLargeForceValues).filter(([key, row]) =>
        key.startsWith(`${tradeDate}:`) && row.tradeDate === tradeDate,
      ));
      window.localStorage.setItem(EARLY_SELL_LARGE_FORCE_VALUES_KEY, JSON.stringify(currentSession));
    } catch {
      // 寫入失敗時保留 React 記憶體值，不讓標籤因儲存限制消失。
    }
  }, [largeForceValueStorageReady, signalLargeForceValues]);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const demoDate = query.get("earlySellDemo");
    let pinned = true;
    let restoredQueue: DaytradeEarlySellSignal[] = [];
    try {
      pinned = window.localStorage.getItem(EARLY_SELL_PINNED_KEY) !== "false";
      // 提醒暫停只套用於目前頁面；每次重新開啟戰鬥版都恢復自動跳出。
      // 同時清掉舊版曾永久保存在單一筆電上的關閉狀態，避免跨裝置表現不一致。
      window.localStorage.removeItem(EARLY_SELL_ALERTS_ENABLED_KEY);
      const savedPopupPosition = JSON.parse(window.localStorage.getItem(EARLY_SELL_TOAST_POSITION_KEY) ?? "null") as Partial<FloatingPanelPosition> | null;
      if (Number.isFinite(savedPopupPosition?.x) && Number.isFinite(savedPopupPosition?.y)) {
        setPopupPosition({ x: Number(savedPopupPosition?.x), y: Number(savedPopupPosition?.y) });
      }
      const savedPopupSize = JSON.parse(window.localStorage.getItem(EARLY_SELL_TOAST_SIZE_KEY) ?? "null") as Partial<FloatingPanelSize> | null;
      if (Number.isFinite(savedPopupSize?.width) && Number.isFinite(savedPopupSize?.height)) {
        setPopupSize({ width: Number(savedPopupSize?.width), height: Number(savedPopupSize?.height) });
      }
      const savedQueue = JSON.parse(window.localStorage.getItem(EARLY_SELL_PINNED_QUEUE_KEY) ?? "[]") as DaytradeEarlySellSignal[];
      if (pinned && Array.isArray(savedQueue)) {
        const tenDaysAgo = Date.now() - 10 * 86_400_000;
        restoredQueue = savedQueue.filter((signal) =>
          signal && typeof signal.ticker === "string" && typeof signal.tradeDate === "string" && Number.isFinite(signal.barTs)
            && isActiveIntradayCenterSignal(signal)
            && signal.barTs >= tenDaysAgo
            && !isFiveMinuteTwelveShortSignal(signal) && !isFiveMinuteOnePlusTwoLongSignal(signal)
            && !(signal.demo === true && signal.tradeDate === "2026-08-20" && signal.label.includes("四項通過")),
        ).slice(-2000);
      }
    } catch {
      pinned = true;
      restoredQueue = [];
    }
    signalAlertsEnabledRef.current = true;
    setSignalAlertsEnabled(true);
    setPopupPinned(pinned);
    if (demoDate === "2026-08-14") {
      setQueue([...restoredQueue, ...HISTORICAL_EARLY_SELL_DEMO]);
    } else if (restoredQueue.length) {
      setQueue(restoredQueue);
    }
    popupStorageReady.current = true;

    if (!storageReady.current) {
      try {
        const saved = JSON.parse(window.localStorage.getItem(EARLY_SELL_SEEN_KEY) ?? "[]") as string[];
        seenKeys.current = new Set(Array.isArray(saved) ? saved : []);
      } catch {
        seenKeys.current = new Set();
      }
      storageReady.current = true;
    }

    let stopped = false;
    const load = async () => {
      if (signalPollInFlight.current) return;
      signalPollInFlight.current = true;
      try {
        const currentGroupRankings = groupRankingsRef.current;
        const response = await fetch("/api/daytrade-early-sell?limit=5000&snapshot=1", {
          cache: "no-store",
          // The production snapshot may need roughly 10–15 seconds while D1
          // and the live collector are busy.  A 4-second timeout made every
          // poll abort before valid extra-large sell rows reached the UI,
          // leaving the tab badge at zero even though the API had signals.
          signal: AbortSignal.timeout(20_000),
          headers: currentGroupRankings
            ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(currentGroupRankings)) }
            : undefined,
        });
        if (!response.ok) throw new Error('signal-snapshot-unavailable');
        const payload = await response.json() as DaytradeEarlySellPayload;
        if (stopped) return;
        if (!payload.ok || !Array.isArray(payload.signals)) throw new Error('signal-snapshot-invalid');
        setSignalSyncError(false);
        setSignalSnapshotReady(true);
        setSignalFeed((current) => ({
          ...current,
          ...payload.signalFeed,
          fallbackAt: payload.signalFeed?.fallbackAt || current?.fallbackAt || 0,
          mode: payload.signalFeed?.mode === "minute-bars-fallback-pending" && current?.mode === "minute-bars-fallback"
            ? current.mode
            : payload.signalFeed?.mode,
          polledAt: Date.now(),
        }));
        const fourGate = Array.isArray(payload.fourGateSignals)
          ? [...payload.fourGateSignals].sort((left, right) => right.barTs - left.barTs)
          : [];
        const mainForce = Array.isArray(payload.mainForceSignals)
          ? [...payload.mainForceSignals].sort((left, right) => right.barTs - left.barTs)
          : payload.signals.filter((signal) => signal.kind.startsWith("mainForce")).sort((left, right) => right.barTs - left.barTs);
        const extraLargeSell = Array.isArray(payload.extraLargeSellSignals)
          ? payload.extraLargeSellSignals.filter(hasQualifiedExtraLargeTriggerForce).sort((left, right) => right.barTs - left.barTs)
          : [];
        const extraLargeBuy = Array.isArray(payload.extraLargeBuySignals)
          ? payload.extraLargeBuySignals.filter(hasQualifiedExtraLargeTriggerForce).sort((left, right) => right.barTs - left.barTs)
          : [];
        const rawLargeForce = Array.isArray(payload.largeForceSignals)
          ? [...payload.largeForceSignals].sort((left, right) => right.barTs - left.barTs)
          : [];
        const instantLarge = normalizeDisplayedInstantLargeSignals(Array.isArray(payload.instantLargeSignals)
          ? payload.instantLargeSignals
          : payload.signals.filter(isInstantLargeSignal)).sort((left, right) => right.barTs - left.barTs);
        const largeForce = filterCandidateIntradayLargeForceSignals(rawLargeForce, [
          ...payload.signals,
          ...fourGate,
          ...mainForce,
          ...extraLargeSell,
          ...extraLargeBuy,
          ...instantLarge,
        ]);
        const orderedSignals = [...payload.signals.filter((signal) => isActiveIntradayCenterSignal(signal) && !signal.kind.startsWith("mainForce") && !isInstantLargeSignal(signal) && !isLargeForceSignal(signal) && !isFiveMinuteTwelveShortSignal(signal) && !isFiveMinuteOnePlusTwoLongSignal(signal)), ...instantLarge, ...mainForce, ...extraLargeSell, ...extraLargeBuy, ...largeForce].sort((left, right) => right.barTs - left.barTs);
        // 四項精選由獨立陣列回傳；必須一起合併進「今日即時」與跳窗，
        // 否則頁籤明明有筆數，總清單與第一次開啟的提醒卻完全看不到。
        const popupSnapshotSignals = [...orderedSignals, ...fourGate].sort((left, right) => right.barTs - left.barTs);
        setTodaySignals((current) => mergePermanentSignalRows(normalizeDisplayedInstantLargeSignals(current), orderedSignals, payload.tradeDate));
        setFourGateSignals((current) => mergePermanentSignalRows(current, fourGate, payload.tradeDate));
        setMainForceSignals((current) => mergePermanentSignalRows(current, mainForce, payload.tradeDate));
        setExtraLargeSellSignals((current) => mergePermanentSignalRows(current, extraLargeSell, payload.tradeDate));
        setExtraLargeBuySignals((current) => mergePermanentSignalRows(current, extraLargeBuy, payload.tradeDate));
        setLargeForceSignals((current) => mergePermanentSignalRows(current, largeForce, payload.tradeDate));
        setInstantLargeSignals((current) => mergePermanentSignalRows(normalizeDisplayedInstantLargeSignals(current), instantLarge, payload.tradeDate));
        if (payload.instantLargeCollector) setInstantLargeCollector(payload.instantLargeCollector);
        if (payload.groupRankings?.strong?.length || payload.groupRankings?.weak?.length) {
          setSignalGroupRankings(payload.groupRankings);
        }
        if (payload.tradeDate && !historyDateChanged.current) setSelectedDate(payload.tradeDate);
        if (Array.isArray(payload.dates)) {
          setAvailableDates([...new Set(payload.dates.filter((date) => /^\d{4}-\d{2}-\d{2}$/.test(date)))].sort().reverse());
        }
        {
          // Opening the site (or explicitly resuming reminders) shows the saved
          // session once. Subsequent polls only introduce current events.
          const seedSession = !popupInitialSeeded.current;
          if (signalAlertsEnabledRef.current) {
            if (popupSnapshotSignals.length) popupInitialSeeded.current = true;
            const receivedAt = Date.now();
            setQueue((current) => {
              const currentSession = current.filter((signal) => signal.tradeDate === payload.tradeDate);
              const thresholdedCurrentSession = normalizeDisplayedInstantLargeSignals(currentSession);
              const queuedKeys = new Set(thresholdedCurrentSession.map(intradaySignalKey));
              const eligible = popupSnapshotSignals.filter(signal => signal.barTs <= receivedAt && (
                seedSession || queuedKeys.has(intradaySignalKey(signal)) || receivedAt - signal.barTs <= 120_000
              ));
              const merged = new Map([...thresholdedCurrentSession, ...eligible].map((signal) => [
                intradaySignalKey(signal),
                signal,
              ]));
              return [...merged.values()].sort((left, right) => left.barTs - right.barTs).slice(-2000);
            });
          }
        }
        const now = Date.now();
        const refinedKeys = new Set(fourGate.map((signal) => `${signal.ticker}:${signal.barTs}`));
        const freshSignals = [...orderedSignals.filter((signal) => !refinedKeys.has(`${signal.ticker}:${signal.barTs}`)), ...fourGate]
          .sort((left, right) => left.barTs - right.barTs)
          .filter((signal) => {
            const key = intradaySignalKey(signal);
            if (seenKeys.current.has(key)) return false;
            seenKeys.current.add(key);
            return now - signal.barTs >= 0 && now - signal.barTs <= 120_000;
          });
        try {
          window.localStorage.setItem(EARLY_SELL_SEEN_KEY, JSON.stringify([...seenKeys.current].slice(-500)));
        } catch {
          // 瀏覽器停用儲存時，仍以本次頁面的記憶體避免重複通知。
        }
        if (freshSignals.length && signalAlertsEnabledRef.current) setQueue((current) => {
          const queuedKeys = new Set(current.map(intradaySignalKey));
          return [...current, ...freshSignals.filter((signal) => !queuedKeys.has(intradaySignalKey(signal)))].sort((left, right) => left.barTs - right.barTs).slice(-2000);
        });
      } catch {
        if (!stopped) setSignalSyncError(true);
      } finally {
        signalPollInFlight.current = false;
      }
    };
    // 畫面每 5 秒只讀 D1 快照，保持手機與 iPad 切換零等待；另一條
    // 背景輪詢才執行正式盤中大單偵測並寫回快照。兩者不可只留前者，
    // 否則開盤後會永遠只看到三角收斂等既有資料。
    const collectImmediate = async () => {
      if (liveSignalCollectInFlight.current) return;
      if (!isIntradaySignalCollectionWindow()) return;
      liveSignalCollectInFlight.current = true;
      try {
        const currentGroupRankings = groupRankingsRef.current;
        const response = await fetch("/api/daytrade-early-sell?collect=immediate", {
          cache: "no-store",
          signal: AbortSignal.timeout(30_000),
          headers: currentGroupRankings
            ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(currentGroupRankings)) }
            : undefined,
        });
        if (!response.ok) throw new Error('signal-collection-unavailable');
        const collected = await response.json() as { ok?: boolean; latestBarAt?: number };
        if (!collected.ok) throw new Error('signal-collection-failed');
        if (!stopped) {
          setSignalCollection(current => ({ checkedAt: Date.now(), sourceAt: Math.max(current.sourceAt, collected.latestBarAt || 0), error: false }));
          void load();
        }
      } catch {
        if (!stopped) setSignalCollection(current => ({ ...current, checkedAt: Date.now(), error: true }));
      } finally {
        liveSignalCollectInFlight.current = false;
      }
    };
    const loadInstantLargeCollector = async () => {
      if (instantLargeCollectorPollInFlight.current) return;
      instantLargeCollectorPollInFlight.current = true;
      try {
        const response = await fetch("/api/daytrade-early-sell?collector=1", {
          cache: "no-store",
          signal: AbortSignal.timeout(6_000),
        });
        if (!response.ok) return;
        const payload = await response.json() as DaytradeEarlySellPayload;
        if (!stopped && payload.instantLargeCollector) setInstantLargeCollector(payload.instantLargeCollector);
      } catch {
        // 保留最後一次成功的監控檔數；後端短暫重連時不把徽章洗成 0。
      } finally {
        instantLargeCollectorPollInFlight.current = false;
      }
    };
    const collectLive = async () => {
      if (fullSignalCollectInFlight.current) return;
      if (!isIntradaySignalCollectionWindow()) return;
      fullSignalCollectInFlight.current = true;
      try {
        const currentGroupRankings = groupRankingsRef.current;
        const response = await fetch("/api/daytrade-early-sell?limit=5000", {
          cache: "no-store",
          signal: AbortSignal.timeout(55_000),
          headers: currentGroupRankings
            ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(currentGroupRankings)) }
            : undefined,
        });
        if (!response.ok) throw new Error('full-signal-collection-unavailable');
        const collected = await response.json() as DaytradeEarlySellPayload & { collectionBusy?: boolean };
        if (!stopped && !collected.collectionBusy) {
          setSignalCollection(current => ({ checkedAt: Date.now(), sourceAt: Math.max(current.sourceAt, collected.signalFeed?.upstreamAt || 0, collected.signalFeed?.fallbackAt || 0), error: !collected.ok }));
          void load();
        }
      } catch {
        // 完整偵測較慢時，開盤大單優先通道與快照仍會正常更新。
      } finally {
        fullSignalCollectInFlight.current = false;
      }
    };
    let largeForceRetryAt = 0;
    let largeForceFailures = 0;
    const collectLargeForceBackfill = async () => {
      if (Date.now() < largeForceRetryAt) return;
      if (largeForceBackfillInFlight.current || largeForceBackfillCompleted.current) return;
      if (!isLargeForceBackfillWindow()) return;
      largeForceBackfillInFlight.current = true;
      try {
        const response = await fetch("/api/daytrade-early-sell?collect=large-force", {
          cache: "no-store",
          signal: AbortSignal.timeout(45_000),
          headers: groupRankingsRef.current
            ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(groupRankingsRef.current)) }
            : undefined,
        });
        if (!response.ok) throw new Error("large-force-backfill-unavailable");
        largeForceFailures = 0;
        largeForceRetryAt = Date.now() + 10_000;
        const payload = await response.json() as DaytradeEarlySellPayload;
        if (!stopped) void load();
        // 收盤後完整跑完 09:00～13:30 就停止；盤中則由後端在冷卻後
        // 開始下一輪，持續補進稍晚才達標的訊號。
        if (payload.largeForceScan?.status === "completed" && !isIntradaySignalCollectionWindow()) {
          largeForceBackfillCompleted.current = true;
        }
      } catch {
        largeForceFailures += 1;
        largeForceRetryAt = Date.now() + Math.min(60_000, 5_000 * 2 ** Math.min(largeForceFailures, 4));
        // Back off failed history work so live reads retain capacity.
      } finally {
        largeForceBackfillInFlight.current = false;
      }
    };
    const collectExtraLargeBackfill = async () => {
      if (extraLargeBackfillInFlight.current || extraLargeBackfillCompleted.current) return;
      // 盤中由完整偵測器持續處理；收盤後、週末、休市日與下一交易日
      // 08:45 前則補跑一次保存資料，避免特大買賣單重新整理後歸零。
      if (isIntradaySignalCollectionWindow()) return;
      extraLargeBackfillInFlight.current = true;
      try {
        const response = await fetch("/api/daytrade-early-sell?collect=extra-large&limit=5000", {
          cache: "no-store",
          signal: AbortSignal.timeout(55_000),
          headers: groupRankingsRef.current
            ? { "X-HanStock-Group-Rankings": encodeURIComponent(JSON.stringify(groupRankingsRef.current)) }
            : undefined,
        });
        if (!response.ok) return;
        const payload = await response.json() as { ok?: boolean; completed?: boolean };
        if (payload.ok) {
          setExtraLargeCheck(payload.completed ? "complete" : "incomplete");
          extraLargeBackfillCompleted.current = payload.completed === true;
          if (!stopped) void load();
        }
      } catch {
        // 回補失敗時保留既有畫面；下一分鐘再試，不會把已保存訊號清空。
      } finally {
        extraLargeBackfillInFlight.current = false;
      }
    };
    void load();
    void loadInstantLargeCollector();
    void collectImmediate();
    void collectLargeForceBackfill();
    void collectExtraLargeBackfill();
    const firstFullCollector = window.setTimeout(() => void collectLive(), 8_000);
    const timer = createVisibilityGatedInterval(() => void load(), 5_000);
    const instantLargeCollectorTimer = createVisibilityGatedInterval(() => void loadInstantLargeCollector(), 10_000);
    const immediateCollectorTimer = createVisibilityGatedInterval(() => void collectImmediate(), 5_000);
    const fullCollectorTimer = createVisibilityGatedInterval(() => void collectLive(), 20_000);
    const largeForceBackfillTimer = createVisibilityGatedInterval(() => void collectLargeForceBackfill(), 8_000);
    const extraLargeBackfillTimer = createVisibilityGatedInterval(() => void collectExtraLargeBackfill(), 300_000);
    return () => {
      stopped = true;
      timer.cancel();
      instantLargeCollectorTimer.cancel();
      immediateCollectorTimer.cancel();
      fullCollectorTimer.cancel();
      largeForceBackfillTimer.cancel();
      extraLargeBackfillTimer.cancel();
      window.clearTimeout(firstFullCollector);
    };
  }, []);

  useEffect(() => {
    if (!popupStorageReady.current) return;
    try {
      window.localStorage.setItem(EARLY_SELL_PINNED_KEY, String(popupPinned));
      if (signalAlertsEnabled && popupPinned && queue.length) {
        window.localStorage.setItem(EARLY_SELL_PINNED_QUEUE_KEY, JSON.stringify(queue.slice(-2000)));
      } else {
        window.localStorage.removeItem(EARLY_SELL_PINNED_QUEUE_KEY);
      }
    } catch {
      // 儲存空間停用時，釘選仍會在目前頁面有效。
    }
  }, [popupPinned, queue, signalAlertsEnabled]);

  useEffect(() => {
    if (!centerOpen || centerMode !== "history") return;
    let active = true;
    const timer = window.setTimeout(async () => {
      setHistoryLoading(true);
      setHistoryMessage("");
      try {
        const params = new URLSearchParams({ date: selectedDate, limit: "5000" });
        const response = await fetch(`/api/daytrade-early-sell?${params}`, { cache: "no-store" });
        const payload = await response.json() as DaytradeEarlySellPayload;
        if (!active) return;
        const stored = payload.ok && Array.isArray(payload.signals) ? payload.signals.filter(isActiveIntradayCenterSignal) : [];
        const storedFourGate = payload.ok && Array.isArray(payload.fourGateSignals) ? payload.fourGateSignals : [];
        const storedMainForce = payload.ok && Array.isArray(payload.mainForceSignals) ? payload.mainForceSignals : [];
        const storedExtraLargeSell = payload.ok && Array.isArray(payload.extraLargeSellSignals) ? payload.extraLargeSellSignals.filter(hasQualifiedExtraLargeTriggerForce) : [];
        const storedExtraLargeBuy = payload.ok && Array.isArray(payload.extraLargeBuySignals) ? payload.extraLargeBuySignals.filter(hasQualifiedExtraLargeTriggerForce) : [];
        const rawStoredLargeForce = payload.ok && Array.isArray(payload.largeForceSignals) ? payload.largeForceSignals : [];
        const demo = selectedDate === "2026-08-14" && stored.length === 0
          ? HISTORICAL_EARLY_SELL_DEMO
          : [];
        const storedInstantLarge = normalizeDisplayedInstantLargeSignals(Array.isArray(payload.instantLargeSignals) ? payload.instantLargeSignals : stored.filter(isInstantLargeSignal));
        const storedLargeForce = filterCandidateIntradayLargeForceSignals(rawStoredLargeForce, [
          ...stored,
          ...storedFourGate,
          ...storedMainForce,
          ...storedExtraLargeSell,
          ...storedExtraLargeBuy,
          ...storedInstantLarge,
        ]);
        setHistorySignals([...stored.filter((signal) => !isInstantLargeSignal(signal) && !isLargeForceSignal(signal)), ...storedInstantLarge, ...storedMainForce, ...storedFourGate, ...storedExtraLargeSell, ...storedExtraLargeBuy, ...storedLargeForce, ...demo].sort((left, right) => right.barTs - left.barTs));
        if (Array.isArray(payload.dates)) {
          const dates = selectedDate === "2026-08-14" ? [...payload.dates, selectedDate] : payload.dates;
          setAvailableDates([...new Set(dates)].sort().reverse());
        }
        if (!payload.ok) setHistoryMessage(payload.message ?? "歷史訊號暫時無法讀取");
      } catch {
        if (active) {
          setHistorySignals(selectedDate === "2026-08-14" ? HISTORICAL_EARLY_SELL_DEMO : []);
          setHistoryMessage("歷史訊號暫時無法讀取，請稍後再試");
        }
      } finally {
        if (active) setHistoryLoading(false);
      }
    }, 220);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [centerMode, centerOpen, selectedDate]);

  const signalTickers = useMemo(() => [...new Set([...todaySignals, ...instantLargeSignals, ...mainForceSignals, ...fourGateSignals, ...extraLargeSellSignals, ...extraLargeBuySignals, ...largeForceSignals, ...historySignals, ...queue].map((item) => item.ticker))].sort().join(","), [extraLargeBuySignals, extraLargeSellSignals, fourGateSignals, historySignals, instantLargeSignals, largeForceSignals, mainForceSignals, queue, todaySignals]);
  const largeForceAjRequestKey = useMemo(() => {
    const tradeDate = largeForceSignals[0]?.tradeDate;
    const tickers = [...new Set(largeForceSignals.map((signal) => signal.ticker.trim().toUpperCase()).filter(Boolean))].sort();
    return tradeDate && tickers.length > 0 ? `${tradeDate}|${tickers.join(",")}` : "";
  }, [largeForceSignals]);
  useEffect(() => {
    if (!largeForceAjRequestKey) return;
    let active = true;
    let inFlight = false;
    const controller = new AbortController();
    const separator = largeForceAjRequestKey.indexOf("|");
    const tradeDate = largeForceAjRequestKey.slice(0, separator);
    const tickers = largeForceAjRequestKey.slice(separator + 1);
    const hasSameDateSnapshot = largeForceAjTradeDateRef.current === tradeDate;
    void Promise.resolve().then(() => {
      // 新股票加入或背景重驗時保留同交易日已完成的清單；iPad 網路較慢時
      // 不可先把畫面切成空白，再等同一份 AJ 比對資料回來。
      if (active && !hasSameDateSnapshot) setLargeForceAjStatus("loading");
    });
    const refresh = async () => {
      if (inFlight || !active) return;
      inFlight = true;
      try {
        const response = await fetch(`/api/intraday-large-force-aj?tradeDate=${encodeURIComponent(tradeDate)}&tickers=${encodeURIComponent(tickers)}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(45_000)]) });
        const payload = await response.json() as AjLargeForceFilterPayload;
        if (!active) return;
        if (!response.ok || !payload.ok || !Array.isArray(payload.rows)) throw new Error(payload.error ?? "aj-filter-failed");
        const nextRows = Object.fromEntries(payload.rows.map((row) => [row.ticker, row]));
        const keepExistingSnapshot = largeForceAjTradeDateRef.current === tradeDate;
        setLargeForceAjTransitions((current) => keepExistingSnapshot ? { ...current, ...nextRows } : nextRows);
        setLargeForceAjPreviousDates(Array.isArray(payload.previousDates) ? payload.previousDates : []);
        largeForceAjTradeDateRef.current = tradeDate;
        setLargeForceAjStatus("ready");
      } catch {
        if (!active) return;
        // 短暫失敗只影響本輪更新；同交易日已顯示的盤中大戶力與 AJ
        // 轉折證據都必須保留，下一分鐘再自動補上新資料。
        if (largeForceAjTradeDateRef.current === tradeDate) setLargeForceAjStatus("ready");
        else {
          setLargeForceAjTransitions({});
          setLargeForceAjPreviousDates([]);
          setLargeForceAjStatus("error");
        }
      } finally { inFlight = false; }
    };
    void refresh();
    const timer = tradeDate === taipeiTradeDate() ? createVisibilityGatedInterval(() => void refresh(), 60_000) : null;
    return () => {
      active = false;
      controller.abort();
      if (timer !== null) timer.cancel();
    };
  }, [largeForceAjRequestKey]);
  const focusedSignalTickers = useMemo(() => {
    const focusedSignals = centerMode === "instantLarge" ? instantLargeSignals
      : centerMode === "mainForce" ? mainForceSignals
      : centerMode === "fourGate" ? fourGateSignals
      : centerMode === "extraLargeSell" ? extraLargeSellSignals
      : centerMode === "extraLargeBuy" ? extraLargeBuySignals
      : centerMode === "largeForce" ? largeForceSignals

      : centerMode === "history" ? historySignals
      : [...todaySignals, ...instantLargeSignals, ...mainForceSignals, ...extraLargeSellSignals, ...extraLargeBuySignals].sort((a, b) => b.barTs - a.barTs);
    return [...new Set(focusedSignals.map((signal) => signal.ticker).filter(Boolean))].join(",");
  }, [centerMode, extraLargeBuySignals, extraLargeSellSignals, fourGateSignals, historySignals, instantLargeSignals, largeForceSignals, mainForceSignals, todaySignals]);
  useEffect(() => {
    if (!signalTickers) return;
    let active = true;
    const focusedTickers = focusedSignalTickers.split(",").filter(Boolean);
    const focusedSet = new Set(focusedTickers);
    const remainingTickers = signalTickers.split(",").filter((ticker) => ticker && !focusedSet.has(ticker));
    const toBatches = (tickers: string[]) => Array.from(
      { length: Math.ceil(tickers.length / 50) },
      (_, index) => tickers.slice(index * 50, index * 50 + 50),
    );
    const applyStocks = (stocks: IntradaySignalStockMeta[]) => {
      if (!active || stocks.length === 0) return;
      // Apply each completed batch immediately. One delayed batch must not keep
      // every row showing its ticker twice and "族群讀取中".
      setStockMeta((current) => ({
        ...current,
        ...Object.fromEntries(stocks.map((stock) => [stock.ticker, {
          ticker: stock.ticker,
          name: stock.name || stock.ticker,
          group: stock.group || "未分類",
          groups: Array.isArray(stock.groups) ? stock.groups.filter(Boolean) : stock.group ? [stock.group] : [],
          changePct: current[stock.ticker]?.changePct ?? stock.changePct ?? null,
        }])),
      }));
    };
    const loadBatches = async (batches: string[][]) => {
      await Promise.allSettled(batches.map(async (batch) => {
        const response = await fetch(`/api/stock-search?metadataOnly=1&tickers=${encodeURIComponent(batch.join(","))}`, { cache: "force-cache", signal: AbortSignal.timeout(10_000) });
        if (!response.ok) throw new Error("stock-meta-failed");
        const payload = await response.json() as { stocks?: IntradaySignalStockMeta[] };
        applyStocks(Array.isArray(payload.stocks) ? payload.stocks : []);
      }));
    };
    void (async () => {
      // The open tab gets its names/groups first; the rest of the signal center
      // warms in the background after the visible rows are already readable.
      await loadBatches(toBatches(focusedTickers));
      if (active) await loadBatches(toBatches(remainingTickers));
    })();
    return () => { active = false; };
  }, [focusedSignalTickers, signalTickers]);

  useEffect(() => {
    if (!signalTickers) return;
    let active = true;
    const wanted = new Set(signalTickers.split(",").filter(Boolean));
    Promise.all([
      fetch("/api/technical-market?fast=1", { cache: "no-store" }).then((response) => response.ok ? response.json() : Promise.reject(new Error("signal-technical-meta-failed"))).then((payload) => {
        if (active && Array.isArray(payload.rows)) {
          const dateByTicker = new Map([...historySignals, ...todaySignals, ...instantLargeSignals, ...mainForceSignals].map((signal) => [signal.ticker, signal.tradeDate]));
          const datedRows = payload.rows.filter((row: { code?: string; date?: string; changePct?: number | null }) => row.code && wanted.has(row.code) && String(row.date ?? "").replaceAll("/", "-") === dateByTicker.get(row.code) && typeof row.changePct === "number" && Number.isFinite(row.changePct));
          // Seed immediately from the existing same-session snapshot, without
          // waiting for the unrelated MA-score request or slower live quotes.
          setStockMeta((current) => ({ ...current, ...Object.fromEntries(datedRows.map((row: { code: string; name?: string; changePct: number }) => [row.code, {
            ...current[row.code], ticker: row.code, name: current[row.code]?.name || row.name || row.code,
            changePct: current[row.code]?.changePct ?? row.changePct,
          }])) }));
        }
        return payload;
      }),
      fetch("/api/ma-score-ranking?lookback=5", { cache: "no-store" }).then((response) => response.ok ? response.json() : Promise.reject(new Error("signal-ma-score-failed"))).catch(() => ({ rows: [] })),
    ])
      .then(([payload, scorePayload]: [{ rows?: Array<{ code?: string; maScore?: number | null; maCompositeScore?: number | null; riverBase?: number | null }> }, { rows?: Array<{ code?: string; score?: number | null }> }]) => {
        if (!active) return;
        const rows = Array.isArray(payload.rows) ? payload.rows : [];
        const compositeByCode = new Map((scorePayload.rows ?? []).flatMap((row) => {
          const code = String(row.code ?? "");
          const score = row.score === null || row.score === undefined ? null : Number(row.score);
          return code && score !== null && Number.isFinite(score) ? [[code, score] as const] : [];
        }));
        setSignalTechnicalMeta(Object.fromEntries(rows.flatMap((row) => {
          const code = String(row.code ?? "");
          if (!wanted.has(code)) return [];
          const rawScore = compositeByCode.get(code) ?? row.maCompositeScore ?? row.maScore;
          const score = rawScore === null || rawScore === undefined ? null : Number(rawScore);
          const base = row.riverBase === null || row.riverBase === undefined ? null : Number(row.riverBase);
          return [[code, {
            maScore: score !== null && Number.isFinite(score) ? score : null,
            riverBase: base !== null && Number.isFinite(base) && base > 0 ? base : null,
          }] as const];
        })));
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [signalTickers]);

  // Every signal category needs quote updates, including 14:30 instant orders.
  // Previously only river/daily-strategy tickers were queried, leaving other
  // records without a trigger changePct permanently blank.
  const currentRuleTickers = signalTickers;
  useEffect(() => {
    if (!currentRuleTickers) return;
    let active = true;
    let inFlight = false;
    const refreshCurrentRuleQuotes = async () => {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      try {
        const focused = focusedSignalTickers.split(",").filter(Boolean);
        const tickers = [...new Set([...focused, ...currentRuleTickers.split(",")])].filter(Boolean);
        const batches = Array.from({ length: Math.ceil(tickers.length / 50) }, (_, index) => tickers.slice(index * 50, index * 50 + 50));
        await Promise.allSettled(batches.map(async (batch) => {
          const response = await fetch(`/api/stock-search?tickers=${encodeURIComponent(batch.join(","))}`, { cache: "no-store", signal: AbortSignal.timeout(12_000) });
          if (!response.ok) return;
          const payload = await response.json() as { stocks?: IntradaySignalStockMeta[] };
          if (!active || !Array.isArray(payload.stocks)) return;
          // Publish each completed batch immediately; a slow unrelated stock
          // must not hold all rows blank. Missing updates retain known values.
          setStockMeta((current) => ({ ...current, ...Object.fromEntries(payload.stocks!.map((stock) => [stock.ticker, {
            ...stock,
            name: stock.name || current[stock.ticker]?.name || stock.ticker,
            group: stock.group || current[stock.ticker]?.group || "未分類",
            changePct: stock.changePct ?? current[stock.ticker]?.changePct ?? null,
          }])) }));
        }));
      } finally { inFlight = false; }
    };
    void refreshCurrentRuleQuotes();
    const timer = createVisibilityGatedInterval(() => void refreshCurrentRuleQuotes(), isIntradaySignalCollectionWindow() ? 10_000 : 60_000);
    return () => { active = false; timer.cancel(); };
  }, [currentRuleTickers, focusedSignalTickers]);

  const effectiveGroupRankings = useMemo<MainForceGroupRankings | undefined>(() => {
    // 首頁排行每 30 秒重新整理，通常比訊號快照內附的排行更新；只在首頁
    // 排行尚未就緒時才退回訊號快照，避免用舊排行讓早盤股票持續誤顯示。
    const strong = groupRankings?.strong?.length ? groupRankings.strong : signalGroupRankings?.strong;
    const weak = groupRankings?.weak?.length ? groupRankings.weak : signalGroupRankings?.weak;
    return strong?.length || weak?.length ? { strong, weak } : undefined;
  }, [groupRankings, signalGroupRankings]);
  const strictFourGateSignals = useMemo(() => fourGateSignals.filter(isFourGateSignal), [fourGateSignals]);
  const combinedTodaySignals = useMemo(() => mergePermanentSignalRows(
    [],
    [
      ...todaySignals,
      ...instantLargeSignals,
      ...mainForceSignals,
      ...strictFourGateSignals,
      ...extraLargeSellSignals,
      ...extraLargeBuySignals,
    ],
    todaySignals[0]?.tradeDate,
  ), [extraLargeBuySignals, extraLargeSellSignals, instantLargeSignals, mainForceSignals, strictFourGateSignals, todaySignals]);
  const popupSignals = useMemo(() => {
    const combinedQueue = normalizeDisplayedInstantLargeSignals(queue);
    return [...new Map(combinedQueue.filter((signal) => signal.strategyKind !== "motherChild"
      && !dismissedPopupSignalKeys.current.has(intradaySignalKey(signal))
      && isActiveIntradayCenterSignal(signal)).map((signal) => [
      intradaySignalKey(signal),
      signal,
    ])).values()].sort((left, right) => right.barTs - left.barTs);
  }, [queue]);
  const changeSignalAlerts = (enabled: boolean) => {
    signalAlertsEnabledRef.current = enabled;
    setSignalAlertsEnabled(enabled);
    if (enabled) {
      // 下一個 5 秒輪詢立即把目前已有的盤中訊號重新帶回跳窗。
      popupInitialSeeded.current = false;
      dismissedPopupSignalKeys.current.clear();
    } else {
      setQueue([]);
      try { window.localStorage.removeItem(EARLY_SELL_PINNED_QUEUE_KEY); } catch {}
    }
  };
  const largeForceAjSignals = largeForceAjStatus === "ready"
    ? filterAjIntradayLargeForceSignals(largeForceSignals, largeForceAjTransitions)
    : [];
  // 各頁籤以訊號種類硬性隔離；切換時不能沿用上一頁的清單內容。
  const selectedCenterSignals = centerMode === "today" ? combinedTodaySignals
    : centerMode === "instantLarge" ? instantLargeSignals.filter(isInstantLargeSignal)
    : centerMode === "mainForce" ? mainForceSignals.filter((signal) => signal.kind.startsWith("mainForce"))
    : centerMode === "fourGate" ? strictFourGateSignals
    : centerMode === "extraLargeSell" ? extraLargeSellSignals.filter(isExtraLargeSellSignal)
    : centerMode === "extraLargeBuy" ? extraLargeBuySignals.filter(isExtraLargeBuySignal)
    : centerMode === "largeForce" ? largeForceAjSignals
    : historySignals;
  const selectedCenterReady = centerMode === "history"
    ? !historyLoading
    : centerMode === "today"
      ? signalSnapshotReady
      : centerMode === "largeForce"
        ? signalSnapshotReady && (largeForceSignals.length === 0 || largeForceAjStatus === "ready" || largeForceAjStatus === "error")
        : signalSnapshotReady;
  const visibleSignals = selectedCenterSignals;
  const largeForceVisibleSignals = largeForceAjSignals;
  const largeForceRequestKey = (() => {
    const groups = new Map<string, Set<string>>();
    for (const signal of [...visibleSignals, ...popupSignals]) {
      const tickers = groups.get(signal.tradeDate) ?? new Set<string>();
      tickers.add(signal.ticker);
      groups.set(signal.tradeDate, tickers);
    }
    return [...groups].sort(([left], [right]) => left.localeCompare(right))
      .map(([tradeDate, tickers]) => `${tradeDate}:${[...tickers].sort().join(",")}`)
      .join("|");
  })();
  useEffect(() => {
    if (!largeForceRequestKey) return;
    let active = true;
    let inFlight = false;
    const requests = largeForceRequestKey.split("|").flatMap((group) => {
      const separator = group.indexOf(":");
      const tradeDate = group.slice(0, separator);
      const tickers = group.slice(separator + 1).split(",").filter(Boolean);
      return Array.from({ length: Math.ceil(tickers.length / 200) }, (_, index) => ({
        tradeDate,
        tickers: tickers.slice(index * 200, index * 200 + 200),
      }));
    });
    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const settled = await Promise.allSettled(requests.map(({ tradeDate, tickers }) =>
          fetch(`/api/intraday-large-force-values?tradeDate=${encodeURIComponent(tradeDate)}&tickers=${encodeURIComponent(tickers.join(","))}`, { cache: "no-store" })
            .then((response) => response.ok ? response.json() : Promise.reject(new Error("large-force-values-failed"))) as Promise<{ rows?: Array<IntradayLargeForceValueRow | null> }>,
        ));
        if (!active) return;
        const rows = settled.flatMap((result) => result.status === "fulfilled" ? result.value.rows ?? [] : [])
          .filter((row): row is IntradayLargeForceValueRow => Boolean(row?.ticker && row.tradeDate));
        if (rows.length === 0) return;
        setSignalLargeForceValues((current) => ({
          ...current,
          ...Object.fromEntries(rows.map((row) => [`${row.tradeDate}:${row.ticker}`, row])),
        }));
      } finally {
        inFlight = false;
      }
    };
    void refresh();
    const hasCurrentSession = requests.some(({ tradeDate }) => tradeDate === taipeiTradeDate());
    const timer = hasCurrentSession ? createVisibilityGatedInterval(() => void refresh(), 30_000) : null;
    return () => {
      active = false;
      if (timer !== null) timer.cancel();
    };
  }, [largeForceRequestKey]);

  const dismissPopupSignals = () => {
    popupSignals.forEach((signal) => dismissedPopupSignalKeys.current.add(intradaySignalKey(signal)));
    setQueue([]);
  };
  const selectHistoryDate = (date: string) => {
    historyDateChanged.current = true;
    setSelectedDate(date);
  };

  return {
    queue,
    popupPinned,
    setPopupPinned,
    signalAlertsEnabled,
    extraLargeCheck,
    todaySignals,
    fourGateSignals,
    mainForceSignals,
    extraLargeSellSignals,
    extraLargeBuySignals,
    largeForceSignals,
    largeForceAjTransitions,
    largeForceAjStatus,
    largeForceAjPreviousDates,
    instantLargeSignals,
    instantLargeCollector,
    signalSnapshotReady,
    signalFeed,
    signalSyncError,
    signalCollection,
    availableDates,
    selectedDate,
    historyLoading,
    historyMessage,
    stockMeta,
    signalTechnicalMeta,
    signalLargeForceValues,
    effectiveGroupRankings,
    strictFourGateSignals,
    combinedTodaySignals,
    popupSignals,
    changeSignalAlerts,
    largeForceAjSignals,
    selectedCenterReady,
    visibleSignals,
    largeForceVisibleSignals,
    dismissPopupSignals,
    selectHistoryDate,
  };
}
