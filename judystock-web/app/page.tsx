"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { createVisibilityGatedInterval } from "../lib/useVisibilityGatedInterval";
import Link from "next/link";
import dynamic from "next/dynamic";
import LiveSyncStatus from "./components/LiveSyncStatus";
import { DEFAULT_BATTLE_SETTINGS, type BattleRuntimeSettings } from "../lib/battle-settings";
import { averageOfficialFlow, calculateChipRankMovements, calculateCombinedChipScore, calculateOfficialChipScore } from "../lib/chip-scoring";
import { chipAutomaticUpdateMessage } from "../lib/chip-auto-refresh";
import { extractMainForceGroupRank, findMainForceGroupRank, findWeakestGroupRank, stripMainForceGroupRank, type MainForceGroupRank, type MainForceGroupRankings } from "../lib/main-force-group-ranks";
import { extractMainForceChipRank, stripMainForceChipRank } from "../lib/main-force-chip-ranks";
import { formatFourGateSignalNote } from "../lib/four-gate-signals";
import { formatGroupDisposition, formatGroupNetFundingRate } from "../lib/group-member-meta";
import { extraLargeTriggerForce } from "../lib/intraday-extra-large-sell";
import { afterHoursForceFromNote } from "../lib/after-hours-trades";
import { rankBrokerBranchWeekly } from "../lib/broker-branch-weekly-score";
import { valuationRiverPosition } from "../lib/valuation-river";
import { WeeklyChipAnalysisPanel } from "./WeeklyChipAnalysisPanel";
import { WeeklyChipStockTrend } from "./WeeklyChipStockTrend";
import { WeeklyMainForceCards } from "./WeeklyMainForceCards";
import { ActiveEtfDetailFactor, BrokerBranchDetailFactor } from "./StockChipSupplementFactors";
import { InstitutionalHolderPanel, LargeHolderScreener, MaScreenerSignalNotifier, ValuationRiverPanel, ValuationRiverScreener } from "./StockResearchPanels";
import { IntradayStockTrackingPanel, IntradayTrackingNotifier } from "./IntradayStockTrackingPanel";
import LiveEtfHoldingsPanel from "./stock-screener/EtfHoldingsPanel";
import StockTradingBadges from "./StockTradingBadges";
import MonthlyRevenueRecords from "./monthly-revenue-records";
import HighDividendEtfPanel from "./HighDividendEtfPanel";
import { useWatchlistDetails, useIntradayForceValues } from "./useWatchlistDetails";
import { WatchlistStockEditor } from "./WatchlistStockEditor";
import { watchlistQuoteTone } from "../lib/watchlist-trend";
import { FundamentalRiverWorkspace } from "./FundamentalRiverPanel";
import { DEFAULT_WATCHLISTS, AFTER_HOURS_WATCHLIST_ID, WATCHLIST_STORAGE_KEY, normalizeWatchlists, applyWatchlistOperations, visibleAfterHoursStocks, type WatchlistFolder, type WatchlistOperation, type WatchlistStock, type WatchlistSyncStatus } from "../lib/watchlists";
import {
  type DaytradeEarlySellSignal,
  type IntradaySignalStockMeta,
  type IntradaySignalTechnicalMeta,
  type IntradayLargeForceValueRow,
  type IntradaySignalCenterMode,
  type FloatingPanelPosition,
  type FloatingPanelSize,
  EARLY_SELL_TOAST_POSITION_KEY,
  EARLY_SELL_TOAST_SIZE_KEY,
  EARLY_SELL_CENTER_POSITION_KEY,
  EARLY_SELL_CENTER_PINNED_KEY,
  INTRADAY_SIGNAL_CENTER_MODES,
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
} from "../lib/early-sell-signals";
import { useEarlySellSignals } from "./hooks/useEarlySellSignals";

type Direction = "strong" | "weak";
type RankMode = "stocks" | "groups";
type BottomMode = "ranking" | "watchlist" | "chips" | "weekly-chips" | "daytrade" | "etf-holdings" | "stock-analysis" | "triangles" | "intraday-tracking" | "revenue-records";
type StockResearchTab = "fundamental" | "river" | "holders" | "chips";
type ChipPeriod = "previous-day" | "five-days";
type ChipMarket = "上市" | "上櫃" | "ETF";
type ChipMarketFilter = "全部" | ChipMarket;
type ChipSelectionFilter = "all" | "bullish" | "bearish";
type StockChangeSort = "default" | "desc" | "asc";
type StockGroupSort = "ranking" | "group";
type ChipSortKey = "changePercent" | "score" | "fiveDayTrend" | "combinedScore" | "dailyBranchNetAmount" | "dailyBranchScore" | "weeklyMainForceScore";
type ChipSortDirection = "desc" | "asc";
type ChipSortState = { key: ChipSortKey; direction: ChipSortDirection } | null;
type FiveMinutePosition = "above-low" | "below-low";
type GroupStock = { ticker: string; name: string; price: string; priceChange?: string; change: string };
type GroupDispositionRow = {
  code: string;
  status: "即將處置" | "處置中" | "已結束" | "處置公告";
  period: string;
  releaseDate: string;
};
type GroupMemberMeta = {
  dispositionLabel: string;
  dispositionTone: "active" | "upcoming" | "released" | "notice";
  isActiveDisposition: boolean;
  netFundingLabel: string;
  netFundingValue: number | null;
  netFundingDataDate: string | null;
  netFundingStatus: "ready" | "force_pending" | "turnover_pending";
};
type GroupMemberMetaPayload = {
  dispositions?: GroupDispositionRow[];
};
type GroupMemberFlowRow = { ticker: string; dataDate: string | null; netLargeAmount: number | null; turnoverAmount: number | null; available: boolean; status: "ready" | "force_pending" | "turnover_pending" };
type GroupMemberFlowPayload = { ok?: boolean; dataDate?: string | null; requestedCount?: number; completedCount?: number; rows?: GroupMemberFlowRow[] };
type DocumentPictureInPictureApi = {
  requestWindow(options: { width: number; height: number }): Promise<Window>;
};
type FocusGroup = { name: string; change: string; stocks: Array<{ symbol: string; change: string; price?: number | null }> };
type FocusSummary = { groupCount: number; averageChange: number; strength: number };
type FocusRankingSide = { groups?: FocusGroup[]; summary?: FocusSummary };
type FocusRankingPayload = { ok: boolean; direction: Direction | "both"; updatedAt?: string; sourceDate?: string | null; liveData?: boolean; priceType?: string; groups?: FocusGroup[]; summary?: FocusSummary; rankings?: Partial<Record<Direction, FocusRankingSide>> };
type FocusSnapshot = { groups?: FocusGroup[]; summary?: FocusSummary; updatedAt?: string; sourceDate?: string | null; priceType?: string };
type ChipWeightKey = "main" | "foreign" | "trust" | "etf" | "dealer" | "hedge";
type ChipWeights = Record<ChipWeightKey, number>;
type OfficialFlowScores = Record<"foreign" | "trust" | "dealer" | "hedge", number>;
type MarketRankingSeriesPoint = OfficialFlowScores & { date: string };
type FiveDayTrendPoint = { date: string; score: number };
type MarketRankingRow = {
  code: string;
  name: string;
  market: "twse" | "tpex" | "etf";
  exchange: "twse" | "tpex";
  series: MarketRankingSeriesPoint[];
};
type MarketRankingPayload = {
  ok: boolean;
  fetchedAt: string;
  dataDate: string;
  rows: MarketRankingRow[];
  coverage: {
    total: number;
    stocks: number;
    twse: number;
    tpex: number;
    etf: number;
    tradingDays: number;
    completeMarkets: boolean;
  };
  sources: string[];
  snapshotFallback?: boolean;
  refreshAttemptedAt?: string | null;
  refreshFallbackReason?: string | null;
};

const tpexInstitutionalBrowserSources = [
  "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
  "https://r.jina.ai/http://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
];

function taipeiCompactDate() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}${value("month")}${value("day")}`;
}

async function fetchTwseInstitutionalInBrowser(signal: AbortSignal) {
  const date = taipeiCompactDate();
  const target = `http://www.twse.com.tw/rwd/zh/fund/T86?date=${date}%26selectType=ALLBUT0999%26response=json`;
  const sources = [
    `https://www.twse.com.tw/rwd/zh/fund/T86?date=${date}&selectType=ALLBUT0999&response=json`,
    `https://r.jina.ai/${target}`,
  ];
  let lastError: unknown = new Error("twse-browser-source-unavailable");
  for (const source of sources) {
    try {
      const response = await fetch(source, {
        cache: "no-store",
        mode: "cors",
        signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]),
        headers: { Accept: "application/json, text/plain, */*" },
      });
      if (!response.ok) throw new Error(`twse-browser-http-${response.status}`);
      const text = await response.text();
      const start = text.indexOf("{");
      const end = text.lastIndexOf("}");
      if (start < 0 || end <= start) throw new Error("twse-browser-payload-missing");
      const payload = JSON.parse(text.slice(start, end + 1)) as { stat?: string; date?: string; data?: unknown[] };
      if (payload.stat !== "OK" || payload.date !== date || !Array.isArray(payload.data) || payload.data.length < 700) {
        throw new Error("twse-browser-payload-incomplete");
      }
      return payload;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      lastError = error;
    }
  }
  throw lastError;
}

async function fetchTpexInstitutionalInBrowser(signal: AbortSignal) {
  let lastError: unknown = new Error("tpex-browser-source-unavailable");
  for (const source of tpexInstitutionalBrowserSources) {
    try {
      const response = await fetch(source, {
        cache: "no-store",
        mode: "cors",
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
        headers: { Accept: "application/json, text/plain, */*" },
      });
      if (!response.ok) throw new Error(`tpex-browser-http-${response.status}`);
      let text = await response.text();
      const start = text.indexOf("[");
      const end = text.lastIndexOf("]");
      if (start < 0 || end <= start) throw new Error("tpex-browser-payload-missing");
      text = text.slice(start, end + 1);
      const payload = JSON.parse(text) as unknown;
      if (!Array.isArray(payload) || payload.length < 600) throw new Error("tpex-browser-payload-incomplete");
      return payload;
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      lastError = error;
    }
  }
  throw lastError;
}

async function refreshMarketRankingWithBrowserOfficialSources(signal: AbortSignal, tradingDays = 6) {
  const [twsePayload, tpexPayload] = await Promise.all([
    fetchTwseInstitutionalInBrowser(signal),
    fetchTpexInstitutionalInBrowser(signal),
  ]);
  const response = await fetch("/api/market-ranking", {
    method: "POST",
    cache: "no-store",
    signal: AbortSignal.any([signal, AbortSignal.timeout(50_000)]),
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ twsePayload, tpexPayload, tradingDays }),
  });
  if (!response.ok) throw new Error("market-ranking-browser-official-refresh-failed");
  const payload = await response.json() as MarketRankingPayload;
  if (!payload.ok || !Array.isArray(payload.rows) || payload.rows.length === 0) {
    throw new Error("market-ranking-browser-official-refresh-empty");
  }
  return payload;
}
type MarketQuote = {
  key: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  session?: "regular" | "preopen-trial";
};
type StockRankQuoteMap = Record<string, MarketQuote>;
type LiveRankingRow = { rank: number; name: string; change: string; score: number; lead: string; leadChange: string };

function groupRankingRowsByLead(rows: LiveRankingRow[]) {
  const firstGroupPosition = new Map<string, number>();
  rows.forEach((row, index) => {
    if (!firstGroupPosition.has(row.lead)) firstGroupPosition.set(row.lead, index);
  });
  return rows.map((row, index) => ({ row, index })).sort((a, b) => {
    const groupDifference = (firstGroupPosition.get(a.row.lead) ?? a.index) - (firstGroupPosition.get(b.row.lead) ?? b.index);
    return groupDifference || a.index - b.index;
  }).map(({ row }) => row);
}
type LiveRankingPayload = {
  ok: boolean;
  fetchedAt?: string;
  sourceDate?: string;
  liveData?: boolean;
  priceType?: string;
  rankings?: Record<RankMode, Record<Direction, LiveRankingRow[]>>;
  groupRankings?: MainForceGroupRankings;
  quotes?: MarketQuote[];
};
type ChipDataStatus = "idle" | "loading" | "ready" | "fallback" | "error";
type OfficialChipRankRow = {
  ticker: string;
  name: string;
  groupName: string;
  market: ChipMarket;
  exchange: "twse" | "tpex";
  changePercent: number | null;
  priceChange: number | null;
  latestPrice: number | null;
  fiveDayTrend: number;
  combinedScore: number;
  dailyBranchNetAmount: number | null;
  dailyBranchNetLots: number | null;
  dailyBranchNetLotsEstimated: boolean;
  dailyBranchScore: number | null;
  dailyBranchTradeDate: string | null;
  dailyBranchConcentration: number | null;
  dailyBranchActiveBranches: number | null;
  weeklyMainForceScore: number | null;
  weeklyMainForceLabel: string;
  weeklyMainForceWeekEndDate: string | null;
  previousCombinedScore: number;
  todayCombinedRank: number;
  previousCombinedRank: number;
  rankChange: number;
  trendSeries: FiveDayTrendPoint[];
  factors: OfficialFlowScores;
  score: number;
  rank: number;
};
type WeeklyMainForceLatestRow = {
  ticker: string;
  weekEndDate: string;
  compositeScore: number;
  label: string;
};
type BrokerBranchDailyRow = {
  ticker: string;
  tradeDate: string;
  netAmount: number;
  netLots?: number | null;
  concentration: number;
  activeBranches: number;
};
type WeeklyMainForceHistoryRow = WeeklyMainForceLatestRow & {
  institutionalScore: number;
  brokerBranchScore: number;
  tdccLargeHolderScore: number;
};
type GroupChipMemberPayload = {
  ok: boolean;
  groupCount: number;
  groups: Array<{ name: string; codes: string[]; members?: Array<{ code: string; name: string }> }>;
};
type GroupStockPayload = { retrievedAt?: string; stocks?: GroupStock[]; averageChange?: string | null; priceType?: string | null };
type GroupChipRankRow = {
  name: string;
  latestScore: number;
  fiveDayScore: number;
  positiveCount: number;
  negativeCount: number;
  coveredCount: number;
  memberCount: number;
  trendSeries: FiveDayTrendPoint[];
};
type DaytradeBrokerRow = {
  ticker: string;
  name: string;
  market: string;
  category: "漲停鎖定" | "曾達漲停" | "強勢大單";
  closePrice: number;
  limitUpPrice: number;
  dayChangePct: number;
  dayChange: number;
  largeBuyAmount: number;
  largeSellAmount: number;
  netLargeAmount: number;
  turnoverAmount: number;
  participationRate: number;
  lateBuyConcentration: number;
  suspicionScore: number;
  suspicionLabel: string;
  estimatedNextDaySellAmount: number;
  confirmedReversalRate: number | null;
  fiveDayRates: number[];
  mainForceDataAvailable: boolean;
  mainForceDataStatus: string;
};
type DaytradeBrokerPayload = { ok: boolean; dataDate?: string; updatedAt?: string; scanStatus?: string; requestedCount?: number; processedCount?: number; dataMissingCount?: number; rows?: DaytradeBrokerRow[]; message?: string };
type DaytradeViewMode = "selected" | "all";
type TriangleStatus = "放量突破" | "突破待量" | "接近突破" | "形成中";
type TriangleRow = {
  stock_code: string;
  stock_name: string;
  status: TriangleStatus;
  score: number;
  close: number;
  distance_to_upper_pct: number;
  volume_ratio_20d: number;
};
type TrianglePayload = {
  ok: boolean;
  sampleAt?: string;
  updatedAt?: string;
  generatedAt?: string;
  summary?: { requested_count: number; matched_count: number; unavailable_count: number };
  rows?: TriangleRow[];
  message?: string;
};

function intradaySignalCenterModeFor(signal: DaytradeEarlySellSignal): IntradaySignalCenterMode {
  if (signal.strategyKind === "blackDragon") return "blackDragon";
  if (isInstantLargeSignal(signal)) return "instantLarge";
  if (isExtraLargeSellSignal(signal)) return "extraLargeSell";
  if (isExtraLargeBuySignal(signal)) return "extraLargeBuy";
  if (isLargeForceSignal(signal)) return "largeForce";
  if (isFourGateSignal(signal)) return "fourGate";
  if (signal.kind.startsWith("mainForce")) return "mainForce";
  if (isFiveMinuteTwelveShortSignal(signal)) return "fiveMinuteTwelveShort";
  if (isFiveMinuteOnePlusTwoLongSignal(signal)) return "fiveMinuteOnePlusTwoLong";
  return "today";
}

function isSelectedDaytradeRow(
  row: DaytradeBrokerRow,
  thresholds: BattleRuntimeSettings["daytradeThresholds"] = DEFAULT_BATTLE_SETTINGS.daytradeThresholds,
) {
  if (!row.mainForceDataAvailable) return false;
  const netBuyRate = daytradeNetFundingRate(row);
  const passesCommon = row.turnoverAmount >= thresholds.turnoverAmount
    && row.netLargeAmount >= thresholds.netLargeAmount
    && netBuyRate >= thresholds.netBuyRate
    && row.suspicionScore >= thresholds.suspicionScore;
  if (!passesCommon) return false;
  if (row.category !== "強勢大單") return true;
  return row.dayChangePct >= thresholds.strongDayChangePct
    && row.lateBuyConcentration >= thresholds.strongLateBuyConcentration;
}

function daytradeNetFundingRate(row: Pick<DaytradeBrokerRow, "netLargeAmount" | "turnoverAmount">) {
  return row.turnoverAmount > 0 ? row.netLargeAmount / row.turnoverAmount * 100 : 0;
}

const DEFAULT_CHIP_WEIGHTS: ChipWeights = DEFAULT_BATTLE_SETTINGS.chipWeights;

const CHIP_UPDATE_SCHEDULE = {
  summary: "每個交易日 15:00 起陸續接收收盤價、上市／上櫃三大法人資料；券商分點與主力資料到齊後，預計 16:10～16:30 完成加權重算。",
  etfNote: "目前全市場排行已接正式法人四項資料；主力分點與主動式 ETF 持股仍待來源，到齊後會再納入重算。",
};
const CHIP_AUTO_REFRESH_MINUTES = DEFAULT_BATTLE_SETTINGS.chipAutoRefreshMinutes;

// 一般盤勢固定計算 67 個族群；「股期標的」只是標的集合，不列入族群強弱。
const HANSTOCK_GROUP_TOTAL = 67;
const FOCUS_SNAPSHOT_KEY = "hanstock-battle-focus-snapshot-v3";
const LIVE_RANKING_SNAPSHOT_KEY = "hanstock-battle-live-ranking-snapshot-v3";
const BROKER_BRANCH_DAILY_SNAPSHOT_KEY = "hanstock-broker-branch-daily-v1";
type FloatingPanelResizeDirection = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const FLOATING_PANEL_RESIZE_DIRECTIONS: FloatingPanelResizeDirection[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

function clampFloatingPanelPosition(x: number, y: number, width: number, height: number): FloatingPanelPosition {
  const margin = 8;
  const maxX = Math.max(margin, window.innerWidth - width - margin);
  const maxY = Math.max(margin, window.innerHeight - height - margin);
  return {
    x: Math.min(Math.max(margin, x), maxX),
    y: Math.min(Math.max(margin, y), maxY),
  };
}

function clampSignalCenterPosition(x: number, y: number, width: number, height: number): FloatingPanelPosition {
  const margin = 8;
  // 大型訊號視窗允許往四個方向移出部分畫面，但始終保留可抓回來的標題區。
  const visibleWidth = Math.min(width, Math.max(220, Math.min(width * .3, window.innerWidth - margin * 2)));
  const headerHeight = Math.min(94, height);
  const visibleHeaderHeight = Math.min(48, headerHeight);
  return {
    x: Math.min(Math.max(margin - width + visibleWidth, x), window.innerWidth - visibleWidth - margin),
    y: Math.min(Math.max(margin - headerHeight + visibleHeaderHeight, y), window.innerHeight - visibleHeaderHeight - margin),
  };
}

function floatingPanelSizeLimits() {
  const viewportWidth = window.visualViewport?.width ?? window.innerWidth;
  const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
  const maximumWidth = Math.max(280, viewportWidth - 16);
  const maximumHeight = Math.max(220, viewportHeight - 16);
  return {
    minimumWidth: Math.min(760, Math.max(280, Math.round(maximumWidth * .62))),
    minimumHeight: Math.min(360, Math.max(220, Math.round(maximumHeight * .38))),
    maximumWidth,
    maximumHeight,
  };
}

function shouldHoldPreviousTradingSnapshot() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = value("weekday");
  const minutes = Number(value("hour")) * 60 + Number(value("minute"));
  // 08:30 起盤前四撮已有正式試撮價，不能再被昨日快照擋住。
  return weekday === "Sat" || weekday === "Sun" || minutes < 8 * 60 + 30;
}

function isPreopenTrialClientWindow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).formatToParts(new Date());
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = value("weekday");
  const minutes = Number(value("hour")) * 60 + Number(value("minute"));
  return weekday !== "Sat" && weekday !== "Sun" && minutes >= 8 * 60 + 30 && minutes < 9 * 60;
}

function hasStoredSnapshot(key: string) {
  try {
    return Boolean(window.localStorage.getItem(key));
  } catch {
    return false;
  }
}

const chipWeightMeta: Array<{ key: ChipWeightKey; label: string; description: string }> = [
  { key: "main", label: "主力", description: "券商分點集中度" },
  { key: "foreign", label: "外資", description: "外資每日買賣超" },
  { key: "trust", label: "投信", description: "投信每日買賣超" },
  { key: "etf", label: "ETF持股", description: "主動式 ETF 持股淨增減" },
  { key: "dealer", label: "自營自買", description: "自行買賣部位" },
  { key: "hedge", label: "自營避險", description: "避險部位" },
];

const groupStockData: Record<string, GroupStock[]> = {
  "記憶體": [
    { ticker: "2344", name: "華邦電", price: "169.50", change: "+7.91%" },
    { ticker: "2408", name: "南亞科", price: "186.00", change: "+6.45%" },
    { ticker: "2337", name: "旺宏", price: "42.10", change: "+5.78%" },
    { ticker: "3006", name: "晶豪科", price: "98.60", change: "+4.92%" },
    { ticker: "8299", name: "群聯", price: "721.00", change: "+4.34%" },
    { ticker: "3260", name: "威剛", price: "127.50", change: "+3.66%" },
    { ticker: "8271", name: "宇瞻", price: "71.20", change: "+2.89%" },
    { ticker: "5351", name: "鈺創", price: "54.70", change: "+2.43%" },
    { ticker: "4967", name: "十銓", price: "132.00", change: "+2.17%" },
    { ticker: "2451", name: "創見", price: "109.50", change: "+1.86%" },
  ],
  "光通訊": [
    { ticker: "3081", name: "聯亞", price: "392.50", change: "+5.21%" },
    { ticker: "3363", name: "上詮", price: "311.00", change: "+4.63%" },
    { ticker: "4979", name: "華星光", price: "248.50", change: "+4.18%" },
    { ticker: "3163", name: "波若威", price: "226.00", change: "+3.67%" },
    { ticker: "4908", name: "前鼎", price: "111.00", change: "+2.97%" },
    { ticker: "3450", name: "聯鈞", price: "287.50", change: "+2.55%" },
    { ticker: "3234", name: "光環", price: "57.90", change: "+1.94%" },
  ],
  "PCB 設備": [
    { ticker: "2368", name: "金像電", price: "548.00", change: "+5.06%" },
    { ticker: "2383", name: "台光電", price: "1,265.00", change: "+4.72%" },
    { ticker: "3037", name: "欣興", price: "188.50", change: "+4.31%" },
    { ticker: "6664", name: "群翊", price: "329.00", change: "+3.85%" },
    { ticker: "8021", name: "尖點", price: "89.60", change: "+3.22%" },
    { ticker: "2467", name: "志聖", price: "207.50", change: "+2.98%" },
  ],
  "航運": [
    { ticker: "2603", name: "長榮", price: "189.50", change: "-4.42%" },
    { ticker: "2609", name: "陽明", price: "76.20", change: "-3.96%" },
    { ticker: "2615", name: "萬海", price: "92.30", change: "-3.42%" },
    { ticker: "2618", name: "長榮航", price: "36.45", change: "-2.78%" },
    { ticker: "2610", name: "華航", price: "22.80", change: "-2.15%" },
    { ticker: "2637", name: "慧洋-KY", price: "68.40", change: "-1.82%" },
  ],
  "營建": [
    { ticker: "2542", name: "興富發", price: "44.10", change: "-3.71%" },
    { ticker: "5534", name: "長虹", price: "96.30", change: "-3.28%" },
    { ticker: "5522", name: "遠雄", price: "79.20", change: "-2.96%" },
    { ticker: "2515", name: "中工", price: "13.25", change: "-2.36%" },
    { ticker: "2548", name: "華固", price: "118.50", change: "-1.92%" },
  ],
  "生技": [
    { ticker: "4743", name: "合一", price: "132.50", change: "-3.08%" },
    { ticker: "6547", name: "高端疫苗", price: "41.65", change: "-2.76%" },
    { ticker: "6446", name: "藥華藥", price: "646.00", change: "-2.41%" },
    { ticker: "1795", name: "美時", price: "281.50", change: "-1.88%" },
    { ticker: "6472", name: "保瑞", price: "728.00", change: "-1.52%" },
  ],
};

const strongGroups = [
  { rank: 1, name: "記憶體", change: "+4.82%", score: 94, lead: "2344 華邦電", leadChange: "+7.91%" },
  { rank: 2, name: "光通訊", change: "+3.67%", score: 91, lead: "3081 聯亞", leadChange: "+6.34%" },
  { rank: 3, name: "PCB 設備", change: "+3.12%", score: 88, lead: "6669 緯穎", leadChange: "+5.06%" },
  { rank: 4, name: "被動元件", change: "+2.74%", score: 84, lead: "2327 國巨", leadChange: "+4.18%" },
  { rank: 5, name: "軍工", change: "+2.21%", score: 79, lead: "2634 漢翔", leadChange: "+3.42%" },
  { rank: 6, name: "散熱", change: "+2.08%", score: 77, lead: "3324 雙鴻", leadChange: "+3.26%" },
  { rank: 7, name: "AI 伺服器", change: "+1.94%", score: 75, lead: "6669 緯穎", leadChange: "+4.88%" },
  { rank: 8, name: "先進封裝", change: "+1.82%", score: 73, lead: "3711 日月光投控", leadChange: "+2.96%" },
  { rank: 9, name: "重電", change: "+1.71%", score: 71, lead: "1519 華城", leadChange: "+2.84%" },
  { rank: 10, name: "半導體設備", change: "+1.63%", score: 69, lead: "3583 辛耘", leadChange: "+2.71%" },
  { rank: 11, name: "網通", change: "+1.55%", score: 67, lead: "3596 智易", leadChange: "+2.48%" },
  { rank: 12, name: "低軌衛星", change: "+1.46%", score: 65, lead: "2314 台揚", leadChange: "+2.36%" },
  { rank: 13, name: "機器人", change: "+1.38%", score: 63, lead: "2049 上銀", leadChange: "+2.21%" },
  { rank: 14, name: "矽光子", change: "+1.31%", score: 61, lead: "3163 波若威", leadChange: "+2.14%" },
  { rank: 15, name: "IC 設計", change: "+1.24%", score: 59, lead: "2454 聯發科", leadChange: "+1.92%" },
  { rank: 16, name: "電源供應器", change: "+1.16%", score: 57, lead: "2308 台達電", leadChange: "+1.81%" },
  { rank: 17, name: "連接器", change: "+1.08%", score: 55, lead: "3533 嘉澤", leadChange: "+1.73%" },
  { rank: 18, name: "工業電腦", change: "+0.96%", score: 53, lead: "2395 研華", leadChange: "+1.62%" },
  { rank: 19, name: "雲端服務", change: "+0.88%", score: 51, lead: "3029 零壹", leadChange: "+1.51%" },
  { rank: 20, name: "安控", change: "+0.79%", score: 49, lead: "3454 晶睿", leadChange: "+1.43%" },
];

const weakGroups = [
  { rank: 1, name: "航運", change: "-3.24%", score: 18, lead: "2603 長榮", leadChange: "-4.42%" },
  { rank: 2, name: "營建", change: "-2.83%", score: 23, lead: "2542 興富發", leadChange: "-3.71%" },
  { rank: 3, name: "生技", change: "-2.17%", score: 29, lead: "4743 合一", leadChange: "-3.08%" },
  { rank: 4, name: "鋼鐵", change: "-1.86%", score: 34, lead: "2002 中鋼", leadChange: "-2.54%" },
  { rank: 5, name: "塑化", change: "-1.52%", score: 39, lead: "1301 台塑", leadChange: "-2.12%" },
  { rank: 6, name: "觀光", change: "-1.43%", score: 41, lead: "2707 晶華", leadChange: "-2.01%" },
  { rank: 7, name: "造紙", change: "-1.36%", score: 43, lead: "1907 永豐餘", leadChange: "-1.92%" },
  { rank: 8, name: "紡織", change: "-1.29%", score: 45, lead: "1402 遠東新", leadChange: "-1.84%" },
  { rank: 9, name: "食品", change: "-1.21%", score: 47, lead: "1216 統一", leadChange: "-1.76%" },
  { rank: 10, name: "水泥", change: "-1.14%", score: 49, lead: "1101 台泥", leadChange: "-1.68%" },
  { rank: 11, name: "百貨", change: "-1.06%", score: 51, lead: "5903 全家", leadChange: "-1.57%" },
  { rank: 12, name: "汽車", change: "-0.98%", score: 53, lead: "2207 和泰車", leadChange: "-1.46%" },
  { rank: 13, name: "金融", change: "-0.91%", score: 55, lead: "2882 國泰金", leadChange: "-1.38%" },
  { rank: 14, name: "玻璃", change: "-0.84%", score: 57, lead: "1802 台玻", leadChange: "-1.29%" },
  { rank: 15, name: "橡膠", change: "-0.77%", score: 59, lead: "2105 正新", leadChange: "-1.21%" },
  { rank: 16, name: "電信", change: "-0.69%", score: 61, lead: "2412 中華電", leadChange: "-1.13%" },
  { rank: 17, name: "居家生活", change: "-0.61%", score: 63, lead: "9911 櫻花", leadChange: "-1.04%" },
  { rank: 18, name: "鞋材", change: "-0.54%", score: 65, lead: "9904 寶成", leadChange: "-0.96%" },
  { rank: 19, name: "電器電纜", change: "-0.47%", score: 67, lead: "1605 華新", leadChange: "-0.88%" },
  { rank: 20, name: "貿易百貨", change: "-0.39%", score: 69, lead: "2903 遠百", leadChange: "-0.79%" },
];

const stockRankTpexCodes = new Set(["3081", "3163", "3324", "5903"]);

function rankStockTicker(label: string) {
  return label.match(/^(\d{4,6})/)?.[1] ?? "";
}

function rankStockName(label: string) {
  return label.replace(/^\d{4,6}\s*/, "");
}

function stockRankExchange(ticker: string) {
  return stockRankTpexCodes.has(ticker) ? "tpex" : "twse";
}

function rankingPositionTone(position: ReturnType<typeof valuationRiverPosition> | null) {
  if (position === "特價" || position === "便宜") return "is-cheap";
  if (position === "昂貴") return "is-expensive";
  return position === "偏貴" ? "is-rich" : "is-pending";
}

const strongFocusGroups = [
  {
    name: "記憶體",
    change: "+4.82%",
    stocks: [
      { symbol: "2344 華邦電", change: "+7.91%" },
      { symbol: "2408 南亞科", change: "+6.45%" },
      { symbol: "2337 旺宏", change: "+5.78%" },
    ],
  },
  {
    name: "光通訊",
    change: "+3.67%",
    stocks: [
      { symbol: "3081 聯亞", change: "+5.21%" },
      { symbol: "3363 上詮", change: "+4.63%" },
      { symbol: "4979 華星光", change: "+4.18%" },
    ],
  },
  {
    name: "PCB 設備",
    change: "+3.12%",
    stocks: [
      { symbol: "2368 金像電", change: "+5.06%" },
      { symbol: "2383 台光電", change: "+4.72%" },
      { symbol: "3037 欣興", change: "+4.31%" },
    ],
  },
  {
    name: "被動元件",
    change: "+2.74%",
    stocks: [
      { symbol: "2327 國巨", change: "+4.18%" },
      { symbol: "2492 華新科", change: "+3.86%" },
      { symbol: "2375 凱美", change: "+3.42%" },
    ],
  },
  {
    name: "軍工",
    change: "+2.21%",
    stocks: [
      { symbol: "2634 漢翔", change: "+3.42%" },
      { symbol: "4541 晟田", change: "+3.16%" },
      { symbol: "8033 雷虎", change: "+2.88%" },
    ],
  },
  {
    name: "散熱",
    change: "+2.08%",
    stocks: [
      { symbol: "3324 雙鴻", change: "+3.26%" },
      { symbol: "3017 奇鋐", change: "+2.94%" },
      { symbol: "6230 尼得科超眾", change: "+2.61%" },
    ],
  },
];

const weakFocusGroups = [
  {
    name: "航運",
    change: "-3.24%",
    stocks: [
      { symbol: "2603 長榮", change: "-4.42%" },
      { symbol: "2609 陽明", change: "-3.96%" },
      { symbol: "2615 萬海", change: "-3.42%" },
    ],
  },
  {
    name: "營建",
    change: "-2.83%",
    stocks: [
      { symbol: "2542 興富發", change: "-3.71%" },
      { symbol: "5534 長虹", change: "-3.28%" },
      { symbol: "5522 遠雄", change: "-2.96%" },
    ],
  },
  {
    name: "生技",
    change: "-2.17%",
    stocks: [
      { symbol: "4743 合一", change: "-3.08%" },
      { symbol: "6547 高端疫苗", change: "-2.76%" },
      { symbol: "6446 藥華藥", change: "-2.41%" },
    ],
  },
  {
    name: "鋼鐵",
    change: "-1.86%",
    stocks: [
      { symbol: "2002 中鋼", change: "-2.54%" },
      { symbol: "2014 中鴻", change: "-2.21%" },
      { symbol: "2027 大成鋼", change: "-1.98%" },
    ],
  },
  {
    name: "塑化",
    change: "-1.52%",
    stocks: [
      { symbol: "1301 台塑", change: "-2.12%" },
      { symbol: "1303 南亞", change: "-1.94%" },
      { symbol: "1326 台化", change: "-1.77%" },
    ],
  },
  {
    name: "觀光",
    change: "-1.43%",
    stocks: [
      { symbol: "2707 晶華", change: "-2.01%" },
      { symbol: "2727 王品", change: "-1.82%" },
      { symbol: "2731 雄獅", change: "-1.66%" },
    ],
  },
];

function MiniChart({ down = false }: { down?: boolean }) {
  const path = down
    ? "M0 12 L10 9 L18 17 L28 14 L38 24 L48 20 L58 29 L68 27 L78 36 L88 31 L100 42"
    : "M0 42 L10 38 L18 40 L28 30 L38 34 L48 21 L58 25 L68 15 L78 20 L88 7 L100 11";
  return (
    <svg className="mini-chart" viewBox="0 0 100 48" aria-hidden="true">
      <path d={path} fill="none" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function formatTaipeiDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
  }).format(date);
}

function formatTriangleSampleTime(value: string | null | undefined) {
  if (!value) return "—";
  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnly) return `${dateOnly[1]}/${dateOnly[2]}/${dateOnly[3]}（交易日）`;
  return formatTaipeiDateTime(value);
}

function normalizedTaipeiDate(value: string | null | undefined) {
  if (!value) return null;
  const matched = value.match(/^(\d{4})[-/]?(\d{2})[-/]?(\d{2})/u);
  if (matched) return `${matched[1]}-${matched[2]}-${matched[3]}`;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function dateDistanceInDays(newer: string, older: string) {
  const newerTime = Date.parse(`${newer}T00:00:00Z`);
  const olderTime = Date.parse(`${older}T00:00:00Z`);
  if (!Number.isFinite(newerTime) || !Number.isFinite(olderTime)) return Number.POSITIVE_INFINITY;
  return Math.round((newerTime - olderTime) / 86_400_000);
}

function recentPreviousTradingDate(dataDate: string, today: string) {
  const distance = dateDistanceInDays(today, dataDate);
  return distance >= 0 && distance <= 4;
}

function isChipRankingCurrent(payload: MarketRankingPayload | null, status: ChipDataStatus) {
  // 盤中讀取最近一次永久保存的完整快照並不是過期資料；只要市場覆蓋完整、
  // 日期是最近交易日，就應明確顯示為「盤中正確沿用」，不能因為本次外部
  // 請求暫時失敗而誤標成待更新。
  if (!payload || status === "error" || !payload.coverage.completeMarkets) return false;
  const dataDate = normalizedTaipeiDate(payload.dataDate);
  if (!dataDate) return false;
  const now = taipeiSessionState();
  if (dataDate === now.date) return true;
  const weekend = now.weekday === "Sat" || now.weekday === "Sun";
  if (weekend || now.minutes < 15 * 60) return recentPreviousTradingDate(dataDate, now.date);
  return false;
}

function isLiveRankingCurrent(sourceDate: string | null, liveData: boolean) {
  const dataDate = normalizedTaipeiDate(sourceDate);
  if (!dataDate) return false;
  const now = taipeiSessionState();
  const weekend = now.weekday === "Sat" || now.weekday === "Sun";
  const duringMarket = !weekend && now.minutes >= 9 * 60 && now.minutes <= 13 * 60 + 35;
  if (duringMarket) return liveData && dataDate === now.date;
  if (dataDate === now.date) return true;
  if (weekend || now.minutes < 9 * 60) return recentPreviousTradingDate(dataDate, now.date);
  return false;
}

function FreshnessBadge({ current, updatedAt, dataDate }: { current: boolean; updatedAt: string | null; dataDate: string | null }) {
  const normalizedDate = normalizedTaipeiDate(dataDate)?.replaceAll("-", "/") ?? "—";
  return (
    <span className={`live-badge data-freshness ${current ? "is-current" : "is-pending"}`} aria-live="polite">
      <i /><b>{current ? "已更新" : "待更新"}</b>
      <small>資料日 {normalizedDate}｜{current ? "最新更新" : "最新檢查"} {formatTaipeiDateTime(updatedAt)}</small>
    </span>
  );
}

function chipJudgement(score: number) {
  if (score >= 60) return { label: "籌碼增加中", className: "increasing" };
  if (score >= 20) return { label: "籌碼整理中", className: "consolidating" };
  if (score > -20) return { label: "籌碼觀察", className: "watching" };
  if (score > -60) return { label: "籌碼偏弱", className: "weakening" };
  return { label: "籌碼減少中", className: "decreasing" };
}

function tradingWeekEndDate(date: string) {
  const normalized = date.replaceAll("/", "-").slice(0, 10);
  const parsed = new Date(`${normalized}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  const daysToFriday = (5 - parsed.getUTCDay() + 7) % 7;
  parsed.setUTCDate(parsed.getUTCDate() + daysToFriday);
  return parsed.toISOString().slice(0, 10);
}

function formatSigned(value: number, digits = 2, suffix = "") {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(digits)}${suffix}`;
}

function formatTwd(value: number) {
  const absolute = Math.abs(value);
  const sign = value < 0 ? "-" : value > 0 ? "+" : "";
  if (absolute >= 100_000_000) return `${sign}${(absolute / 100_000_000).toFixed(2)} 億`;
  if (absolute >= 10_000) return `${sign}${(absolute / 10_000).toFixed(1)} 萬`;
  return `${sign}${Math.round(absolute).toLocaleString("zh-TW")}`;
}

function intradayLargeForceStage(timestamp: number | null | undefined) {
  const date = new Date((timestamp ?? Date.now()) + 8 * 60 * 60_000);
  const minute = date.getUTCHours() * 60 + date.getUTCMinutes();
  if (minute < 9 * 60 + 15) return { code: "A", label: "監測階段", time: "09:00–09:15" };
  if (minute < 9 * 60 + 30) return { code: "B", label: "確認階段", time: "09:15–09:30" };
  if (minute < 10 * 60) return { code: "C", label: "確認階段", time: "09:30–10:00" };
  return { code: "盤中", label: "持續追蹤", time: "10:00–13:30" };
}

function formatSignedLots(value: number) {
  const sign = value < 0 ? "-" : value > 0 ? "+" : "";
  return `${sign}${Math.abs(value).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 張`;
}

function FiveDayTrendPreview({
  code,
  name,
  value,
  series,
}: {
  code: string;
  name: string;
  value: number;
  series: FiveDayTrendPoint[];
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12 });

  const showPreview = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const popupWidth = Math.min(330, window.innerWidth - 24);
    const popupHeight = 226;
    const left = Math.max(12, Math.min(rect.left + rect.width / 2 - popupWidth / 2, window.innerWidth - popupWidth - 12));
    const top = rect.bottom + popupHeight + 12 <= window.innerHeight
      ? rect.bottom + 8
      : Math.max(12, rect.top - popupHeight - 8);
    setPosition({ left, top });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const closePreview = () => setOpen(false);
    window.addEventListener("resize", closePreview);
    window.addEventListener("scroll", closePreview, true);
    return () => {
      window.removeEventListener("resize", closePreview);
      window.removeEventListener("scroll", closePreview, true);
    };
  }, [open]);

  const chart = useMemo(() => {
    const width = 292;
    const height = 104;
    const paddingX = 12;
    const paddingY = 13;
    const values = series.map((point) => point.score);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = Math.max(1, max - min);
    const points = series.map((point, index) => ({
      ...point,
      x: paddingX + (index * (width - paddingX * 2)) / Math.max(1, series.length - 1),
      y: paddingY + ((max - point.score) / span) * (height - paddingY * 2),
    }));
    const zeroY = paddingY + ((max - 0) / span) * (height - paddingY * 2);
    return { width, height, points, zeroY };
  }, [series]);

  const tone = value >= 0 ? "positive" : "negative";
  return (
    <span className="five-day-trend-trigger" onMouseEnter={showPreview} onMouseLeave={() => setOpen(false)}>
      <button
        ref={triggerRef}
        type="button"
        className={tone}
        aria-label={`${code} ${name} 最近五日籌碼趨勢，${formatSigned(value, 1)}`}
        aria-expanded={open}
        onFocus={showPreview}
        onBlur={() => setOpen(false)}
        onClick={() => open ? setOpen(false) : showPreview()}
      >
        {formatSigned(value, 1)}
        <span aria-hidden="true">⌁</span>
      </button>
      {open && createPortal(
        <aside className="five-day-trend-popover" style={{ left: position.left, top: position.top }} role="tooltip">
          <div className="five-day-trend-heading">
            <div><strong>{code} {name}</strong><small>最近 5 個交易日</small></div>
            <b className={tone}>{formatSigned(value, 1)}</b>
          </div>
          <svg viewBox={`0 0 ${chart.width} ${chart.height}`} role="img" aria-label={`${code} 最近五日籌碼分數曲線`}>
            <defs>
              <linearGradient id={`battle-trend-fill-${code}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={value >= 0 ? "#65d39f" : "#ff7474"} stopOpacity="0.28" />
                <stop offset="100%" stopColor={value >= 0 ? "#65d39f" : "#ff7474"} stopOpacity="0" />
              </linearGradient>
            </defs>
            <line className="trend-zero-line" x1="0" x2={chart.width} y1={chart.zeroY} y2={chart.zeroY} />
            {chart.points.length > 1 && (
              <polygon
                className="trend-area"
                points={`${chart.points.map((point) => `${point.x},${point.y}`).join(" ")} ${chart.points.at(-1)!.x},${chart.height} ${chart.points[0].x},${chart.height}`}
                fill={`url(#battle-trend-fill-${code})`}
              />
            )}
            <polyline className={value >= 0 ? "trend-curve positive" : "trend-curve negative"} points={chart.points.map((point) => `${point.x},${point.y}`).join(" ")} />
            {chart.points.map((point) => (
              <g key={point.date}>
                <circle className={point.score >= 0 ? "trend-dot positive" : "trend-dot negative"} cx={point.x} cy={point.y} r="3.7" />
                <text className="trend-value-label" x={point.x} y={Math.max(10, point.y - 8)} textAnchor="middle">{formatSigned(point.score, 1)}</text>
              </g>
            ))}
          </svg>
          <div className="five-day-trend-dates">
            {series.map((point) => <span key={point.date}>{point.date.slice(5)}</span>)}
          </div>
          <p>曲線為每日加權籌碼分數；虛線為 0 軸。</p>
        </aside>,
        document.body,
      )}
    </span>
  );
}

function ChipMomentumHistogram({ code, name, series }: { code: string; name: string; series: FiveDayTrendPoint[] }) {
  const width = 760;
  const height = 250;
  const left = 54;
  const right = 18;
  const baseline = 116;
  const halfHeight = 78;
  const maxMagnitude = Math.max(1, ...series.map((point) => Math.abs(point.score)));
  const columnWidth = (width - left - right) / Math.max(1, series.length);
  const barWidth = Math.min(58, columnWidth * 0.58);

  return (
    <section className="chip-momentum-card" aria-label={`${code} ${name}籌碼加權動能柱狀體`}>
      <header><div><span className="eyebrow">WEIGHTED CHIP MOMENTUM</span><h3>籌碼加權動能</h3></div><p><i className="positive" />紅柱為籌碼增強　<i className="negative" />綠柱為籌碼轉弱</p></header>
      <div className="chip-momentum-chart-scroll" role="region" aria-label={`${code}最近交易日籌碼加權動能`} tabIndex={0}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${code} ${name}最近${series.length}個交易日籌碼加權動能柱狀圖`}>
          <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline - halfHeight} y2={baseline - halfHeight} />
          <line className="chip-momentum-zero" x1={left} x2={width - right} y1={baseline} y2={baseline} />
          <line className="chip-momentum-grid" x1={left} x2={width - right} y1={baseline + halfHeight} y2={baseline + halfHeight} />
          <text className="chip-momentum-axis-label" x={left - 8} y={baseline - halfHeight + 4} textAnchor="end">+{maxMagnitude.toFixed(1)}</text>
          <text className="chip-momentum-axis-label" x={left - 8} y={baseline + 4} textAnchor="end">0</text>
          <text className="chip-momentum-axis-label" x={left - 8} y={baseline + halfHeight + 4} textAnchor="end">-{maxMagnitude.toFixed(1)}</text>
          {series.map((point, index) => {
            const barHeight = Math.max(2, Math.abs(point.score) / maxMagnitude * halfHeight);
            const x = left + index * columnWidth + (columnWidth - barWidth) / 2;
            const y = point.score >= 0 ? baseline - barHeight : baseline;
            return (
              <g key={`${code}-momentum-${point.date}`}>
                <rect className={point.score >= 0 ? "chip-momentum-positive" : "chip-momentum-negative"} x={x} y={y} width={barWidth} height={barHeight} rx="5" />
                <text className={point.score >= 0 ? "chip-momentum-value positive" : "chip-momentum-value negative"} x={x + barWidth / 2} y={point.score >= 0 ? Math.max(18, y - 7) : Math.min(height - 30, y + barHeight + 16)} textAnchor="middle">{formatSigned(point.score, 1)}</text>
                <text className="chip-momentum-date" x={x + barWidth / 2} y={height - 14} textAnchor="middle">{point.date.slice(5)}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <footer>柱體依目前設定的籌碼權重即時重算；0 軸以上為偏多，0 軸以下為偏空。</footer>
    </section>
  );
}

function openKlineByTicker(ticker: string, stockName: string, signalTs?: number) {
  const url = new URL("/kline", window.location.origin);
  url.searchParams.set("ticker", ticker);
  url.searchParams.set("interval", "5m");
  if (stockName) url.searchParams.set("name", stockName);
  if (signalTs && Number.isFinite(signalTs)) url.searchParams.set("signalTs", String(Math.trunc(signalTs)));
  const sourceUrl = new URL(window.location.href);
  url.searchParams.set("returnTo", `${sourceUrl.pathname}${sourceUrl.search}${sourceUrl.hash}`);

  const mobileLike = window.matchMedia?.("(pointer: coarse)").matches || window.innerWidth <= 820;
  if (mobileLike) {
    window.location.assign(url.toString());
    return;
  }

  const child = window.open(
    url.toString(),
    "_blank",
    "popup=yes,width=1180,height=780,resizable=yes,scrollbars=no",
  );
  if (!child) window.location.assign(url.toString());
}

function openOriginalKline(ticker: string, name?: string) {
  openKlineByTicker(ticker, name ?? "");
}

function StrengthGauge({ score }: { score: number }) {
  const level = Math.min(5, Math.max(0, Math.ceil(Math.abs(score) / 20)));
  return (
    <div className="rank-strength-gauge">
      {[1, 2, 3, 4, 5].map((bar) => (
        <i key={bar} className={bar <= level ? "active" : ""} />
      ))}
    </div>
  );
}

function TriangleScreenerPanel() {
  const [payload, setPayload] = useState<TrianglePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [activeStatus, setActiveStatus] = useState<"全部" | TriangleStatus>("全部");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch("/api/triangles", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const data = await response.json() as TrianglePayload;
        if (!response.ok || !data.ok) throw new Error(data.message || "三角收斂名單暫時無法取得");
        setPayload(data);
        setMessage("");
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setMessage(error instanceof Error ? error.message : "三角收斂名單暫時無法取得");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const rows = payload?.rows ?? [];
  const visibleRows = activeStatus === "全部" ? rows : rows.filter((row) => row.status === activeStatus);
  const statuses: Array<"全部" | TriangleStatus> = ["全部", "放量突破", "突破待量", "接近突破", "形成中"];
  const statusCount = (status: "全部" | TriangleStatus) => status === "全部" ? rows.length : rows.filter((row) => row.status === status).length;

  return (
    <section className="triangle-console" aria-label="盤後日線三角收斂選股">
      <header className="triangle-head">
        <div><span className="eyebrow">DAILY TRIANGLE CONVERGENCE</span><h2>盤後日線三角收斂</h2><p>依 2,337 檔官方日 K 掃描；放量突破優先，其次為突破待量、接近突破與形成中。</p><a className="triangle-live-link" href="/triangles-intraday">開啟盤中即時名單 ›</a></div>
        <div className="triangle-summary">
          <span>符合名單</span>
          <strong>{(payload?.summary?.matched_count ?? rows.length) || "—"} 檔</strong>
          <small>資料不足 {payload?.summary?.unavailable_count ?? "—"} 檔</small>
          <time>資料樣本時間 {formatTriangleSampleTime(payload?.sampleAt)}</time>
          <time>最新更新時間 {payload?.updatedAt || payload?.generatedAt ? formatTaipeiDateTime(payload.updatedAt ?? payload.generatedAt) : "—"}</time>
        </div>
      </header>
      <div className="triangle-filters" role="tablist" aria-label="三角收斂狀態篩選">
        {statuses.map((status) => <button type="button" role="tab" aria-selected={activeStatus === status} className={activeStatus === status ? "active" : ""} onClick={() => setActiveStatus(status)} key={status}>{status}<b>{statusCount(status)}</b></button>)}
      </div>
      <div className="triangle-table-scroll" role="region" aria-label="三角收斂股票名單" tabIndex={0}>
        <div className="triangle-table">
          <div className="triangle-row triangle-row-head"><span>狀態</span><span>代號／名稱</span><span>分數</span><span>收盤價</span><span>距上緣</span><span>20 日量比</span><span>操作</span></div>
          {loading && <div className="triangle-empty">正在讀取最新盤後掃描結果…</div>}
          {!loading && message && <div className="triangle-empty is-error">{message}</div>}
          {!loading && !message && visibleRows.map((row) => (
            <button type="button" className={`triangle-row status-${row.status}`} key={row.stock_code} onClick={() => openKlineByTicker(row.stock_code, row.stock_name)} aria-label={`開啟 ${row.stock_code} ${row.stock_name} 五分鐘 K 線`}>
              <span><i />{row.status}</span><span><b>{row.stock_code}</b><strong>{row.stock_name}</strong></span><strong>{row.score.toFixed(1)}</strong><span>{row.close.toLocaleString("zh-TW")}</span><span className={row.distance_to_upper_pct <= 0 ? "is-breakout" : ""}>{row.distance_to_upper_pct > 0 ? "+" : ""}{row.distance_to_upper_pct.toFixed(2)}%</span><span className={row.volume_ratio_20d >= 1.5 ? "is-volume" : ""}>{row.volume_ratio_20d.toFixed(2)}×</span><em>查看 5 分 K ›</em>
            </button>
          ))}
          {!loading && !message && visibleRows.length === 0 && <div className="triangle-empty">這個分類目前沒有股票</div>}
        </div>
      </div>
      <footer>名單為盤後技術線型篩選結果，不代表投資建議；點任一個股可直接查看五分鐘 K 線。</footer>
    </section>
  );
}

function earlySellTime(barTs: number) {
  const displayed = new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(barTs));
  return displayed;
}

function signalCenterTime(signal: DaytradeEarlySellSignal) {
  const cutoff = Date.parse(`${signal.tradeDate}T14:30:00+08:00`);
  const intradayTimestamp = Number.isFinite(cutoff) ? Math.min(signal.barTs, cutoff) : signal.barTs;
  return earlySellTime(intradayTimestamp);
}

function intradaySignalText(value: string) {
  return value.replaceAll("早盤", "盤中");
}

function intradaySignalTone(kind: DaytradeEarlySellSignal["kind"]) {
  if (kind === "daytradeEarlyBuy50" || kind === "intradayExtraLargeBuy" || kind === "intradayLargeForceBuy" || kind === "instantLargeBuy" || kind === "fourGateBullish" || kind === "mainForceTurnBullish" || kind === "mainForceStrongBullish" || kind === "fiveMinuteOnePlusTwoLong" || kind === "riverBull") return " is-buy";
  if (kind === "daytradeEarlySell50" || kind === "intradayExtraLargeSell" || kind === "intradayLargeForceSell" || kind === "instantLargeSell" || kind === "fourGateBearish" || kind === "mainForceTurnBearish" || kind === "mainForceStrongBearish" || kind === "fiveMinuteTwelveShort" || kind === "riverBear") return " is-sell";
  if (kind === "triangleVolumeBreakout") return " is-triangle-volume";
  if (kind === "triangleBreakoutPendingVolume") return " is-triangle-pending";
  return " is-triangle-near";
}

function intradaySignalStrongBadge(kind: DaytradeEarlySellSignal["kind"]) {
  if (kind === "mainForceStrongBullish") return " main-force-strong-badge is-bullish";
  if (kind === "mainForceStrongBearish") return " main-force-strong-badge is-bearish";
  if (kind === "intradayExtraLargeSell") return " intraday-extra-large-sell-badge";
  if (kind === "intradayExtraLargeBuy") return " intraday-extra-large-buy-badge";
  if (kind === "instantLargeBuy") return " intraday-extra-large-buy-badge";
  if (kind === "instantLargeSell") return " intraday-extra-large-sell-badge";
  if (kind === "intradayLargeForceBuy") return " intraday-large-force-badge is-bullish";
  if (kind === "intradayLargeForceSell") return " intraday-large-force-badge is-bearish";
  return "";
}

function intradaySignalGroupRank(
  signal: DaytradeEarlySellSignal,
  meta: IntradaySignalStockMeta | undefined,
  rankings: MainForceGroupRankings | undefined,
) {
  const stored = extractMainForceGroupRank(signal.note);
  if (stored) return stored;
  // Live fallback is only valid for today's rows. Historical rows must keep the
  // ranking captured in their persisted note instead of borrowing today's rank.
  if (signal.tradeDate !== taipeiTradeDate()) return null;
  const groups = meta?.groups?.length ? meta.groups : meta?.group ? [meta.group] : [];
  return findMainForceGroupRank(signal, groups, rankings)
    ?? (signal.kind === "riverBear" ? findWeakestGroupRank(groups, rankings) : null);
}

function stripMainForceAnnotations(note: string) {
  return stripMainForceChipRank(stripMainForceGroupRank(note));
}

function displayIntradaySignalNote(signal: DaytradeEarlySellSignal) {
  const note = intradaySignalText(stripMainForceAnnotations(signal.note));
  if (isLargeForceSignal(signal)) {
    const value = note.match(/盤中大戶力\s+[+-]?\d+(?:\.\d+)?%/u);
    if (value?.index !== undefined) {
      const direction = signal.kind === "intradayLargeForceBuy" ? "is-bullish" : "is-bearish";
      return <>{note.slice(0, value.index)}<span className={`intraday-large-force-value ${direction}`}>{value[0]}</span>{note.slice(value.index + value[0].length)}</>;
    }
  }
  return isFourGateSignal(signal) ? formatFourGateSignalNote(note) : note;
}

function ChipIntradayForceBadge({ value, tradeDate }: {
  value?: {forcePct: number | null; tradeDate: string};
  tradeDate: string;
}) {
  const pct = value?.tradeDate === tradeDate && typeof value.forcePct === "number" && Number.isFinite(value.forcePct) ? value.forcePct : null;
  const tone = pct === null || pct === 0 ? "neutral" : pct > 0 ? "positive" : "negative";
  return <i className={`chip-intraday-force ${tone}`} title={tradeDate ? `${tradeDate} 盤中大戶力` : "等待交易日資料"}>
    <span>盤中大戶力</span><b>{pct === null ? "待補" : formatSigned(pct, 1, "%")}</b>
  </i>;
}

function IntradayLargeForceBadge({ forcePct }: { forcePct: number | null }) {
  const direction = forcePct === null || forcePct === 0 ? "is-pending" : forcePct > 0 ? "is-bullish" : "is-bearish";
  return <i className={`intraday-signal-large-force ${direction}`}>
    盤中大戶力 {forcePct === null ? "待補" : formatSigned(forcePct, 1, "%")}
  </i>;
}

function SignalIntradayLargeForceBadge({
  signal,
  value,
}: {
  signal: DaytradeEarlySellSignal;
  value?: IntradayLargeForceValueRow;
}) {
  if (signal.note.includes("14:30 盤後定價成交")) {
    const forcePct = afterHoursForceFromNote(signal.note);
    if (forcePct !== null) {
      const direction = forcePct === 0 ? "is-pending" : forcePct > 0 ? "is-bullish" : "is-bearish";
      return <i className={`intraday-signal-large-force ${direction}`} title="盤後未成交買賣量失衡比例：未成交量 ÷（成交量＋未成交量）；買方為正、賣方為負，非大戶身分或盤中主力淨額。">盤後大戶力 {formatSigned(forcePct, 1, "%")}</i>;
    }
    return <i className="intraday-signal-large-force is-not-applicable">盤後定價成交・不適用盤中大戶力</i>;
  }
  if (isExtraLargeSellSignal(signal) || isExtraLargeBuySignal(signal)) {
    const { forcePct, netAmount } = extraLargeTriggerForce(signal);
    const direction = netAmount === null || netAmount === 0 ? "is-pending" : netAmount > 0 ? "is-bullish" : "is-bearish";
    const text = netAmount === null || netAmount === 0 ? "待補"
      : forcePct === null ? `${netAmount > 0 ? "正" : "負"}（比例待補）`
      : forcePct === 0 ? `${netAmount > 0 ? "+" : "-"}<0.1%` : formatSigned(forcePct, 1, "%");
    return <i className={`intraday-signal-large-force ${direction}`} title="依訊號觸發當分鐘的累計大單淨額確認方向">觸發時大戶力 {text}</i>;
  }
  const noteValue = isLargeForceSignal(signal)
    ? Number(signal.note.match(/盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u)?.[1])
    : isInstantLargeSignal(signal)
      ? Number(signal.note.match(/觸發當時盤中大戶力\s+([+-]?\d+(?:\.\d+)?)%/u)?.[1])
    : Number.NaN;
  // 盤中大戶力與瞬間大單都顯示各自的觸發值；瞬間大單不得借用稍後
  // 的最新值，否則同列會把不同時間點的方向混在一起。
  const forcePct = Number.isFinite(noteValue)
    ? noteValue
    : !isInstantLargeSignal(signal) && typeof value?.forcePct === "number" && Number.isFinite(value.forcePct) ? value.forcePct : null;
  return <IntradayLargeForceBadge forcePct={forcePct} />;
}

function intradaySignalChipRankText(
  signal: DaytradeEarlySellSignal,
  chipRank: NonNullable<ReturnType<typeof extractMainForceChipRank>>,
) {
  const title = (signal.kind === "intradayExtraLargeSell" || signal.kind === "intradayExtraLargeBuy") && chipRank.direction === "減少"
    ? "前日籌碼衰退"
    : `前日綜合${chipRank.direction}`;
  return `${title}第 ${chipRank.rank} 名・${formatSigned(chipRank.score, 1)} 分`;
}

function intradaySignalGroupRankClass(groupRank: MainForceGroupRank) {
  const direction = groupRank.direction === "漲幅" ? "is-bullish" : "is-bearish";
  const emphasis = groupRank.direction === "漲幅" && groupRank.rank <= 5
    ? "is-top-five"
    : groupRank.direction === "跌幅" && groupRank.rank <= 10
      ? "is-bottom-ten"
      : "";
  return `${direction}${emphasis ? ` ${emphasis}` : ""}`;
}

function intradaySignalSource(signal: DaytradeEarlySellSignal) {
  if (signal.strategyKind === "blackDragon") return { label: "創高的黑龍・盤中回補", className: "is-river" };
  if (isInstantLargeSignal(signal)) return { label: "族群前 20・逐筆", className: "is-full-market" };
  if (isLargeForceSignal(signal)) return { label: "原有訊號候選・盤中大戶力", className: "is-full-market" };
  if (signal.kind === "intradayExtraLargeSell" || signal.kind === "intradayExtraLargeBuy") return { label: "前日大單淨額", className: "is-previous-day" };
  if (signal.kind === "fiveMinuteTwelveShort" || signal.kind === "fiveMinuteOnePlusTwoLong") return { label: "五分K全市場掃描", className: "is-full-market" };
  const source = signal.sourceUniverse
    ?? (signal.kind.startsWith("mainForce") ? "fullMarketMainForce" : signal.kind.startsWith("triangle") ? "fullMarketTriangle" : "previousDayDaytrade");
  if (source === "fullMarket") return { label: "全市場掃描", className: "is-full-market" };
  if (source === "both") return { label: "全市場＋隔日沖加強", className: "is-both" };
  if (source === "fullMarketTriangle") return { label: "全市場掃描・三角收斂", className: "is-full-market" };
  if (source === "fullMarketMainForce") return { label: "全市場掃描・主力累計", className: "is-full-market" };
  if (source === "fullMarketLargeForce") return { label: "原有訊號候選・盤中大戶力", className: "is-full-market" };
  return { label: "隔日沖加強", className: "is-previous-day" };
}

function DaytradeEarlySellNotifier({
  marketTime,
  groupRankings,
}: {
  marketTime: string;
  groupRankings?: MainForceGroupRankings;
}) {
  const [popupPosition, setPopupPosition] = useState<FloatingPanelPosition | null>(null);
  const [popupSize, setPopupSize] = useState<FloatingPanelSize | null>(null);
  const [popupDragging, setPopupDragging] = useState(false);
  const [popupExternalWindow, setPopupExternalWindow] = useState<Window | null>(null);
  const popupExternalWindowRef = useRef<Window | null>(null);
  useEffect(() => () => {
    popupExternalWindowRef.current?.close();
  }, []);
  useEffect(() => {
    if (!popupExternalWindow) return;
    const timer = createVisibilityGatedInterval(() => {
      if (popupExternalWindow.closed) {
        popupExternalWindowRef.current = null;
        setPopupExternalWindow(null);
      }
    }, 500);
    return () => timer.cancel();
  }, [popupExternalWindow]);
  const [popupResizing, setPopupResizing] = useState(false);
  const [popupScrollMetrics, setPopupScrollMetrics] = useState({ thumbTop: 0, thumbHeight: 72, valueNow: 0, visible: false });
  const [centerOpen, setCenterOpen] = useState(false);
  const [signalWindowMode, setSignalWindowMode] = useState(false);
  const [signalPinnedFrameMode, setSignalPinnedFrameMode] = useState(false);
  const [signalPinnedWindow, setSignalPinnedWindow] = useState<Window | null>(null);
  const [centerMode, setCenterMode] = useState<IntradaySignalCenterMode>("today");
  const [centerPosition, setCenterPosition] = useState<FloatingPanelPosition | null>(null);
  const [centerPinned, setCenterPinned] = useState(true);
  const [centerDragging, setCenterDragging] = useState(false);
  const centerPreferenceReady = useRef(false);
  const centerRef = useRef<HTMLElement | null>(null);
  const signalPinnedWindowRef = useRef<Window | null>(null);
  const popupRef = useRef<HTMLElement | null>(null);
  const popupListRef = useRef<HTMLDivElement | null>(null);
  const popupScrollbarTrackRef = useRef<HTMLDivElement | null>(null);
  const popupScrollDragRef = useRef<{ pointerId: number; startY: number; startScrollTop: number } | null>(null);
  const popupAutoHideTimer = useRef<number | null>(null);
  const popupDragRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
    width: number;
    height: number;
    position: FloatingPanelPosition;
  } | null>(null);
  const popupResizeRef = useRef<{
    pointerId: number;
    direction: FloatingPanelResizeDirection;
    startX: number;
    startY: number;
    left: number;
    top: number;
    width: number;
    height: number;
    position: FloatingPanelPosition;
    size: FloatingPanelSize;
  } | null>(null);
  const centerDragRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
    width: number;
    height: number;
    position: FloatingPanelPosition;
  } | null>(null);

  const {
    queue,
    popupPinned,
    setPopupPinned,
    signalAlertsEnabled,
    extraLargeCheck,
    todaySignals,
    blackDragonSignals,
    blackDragonReady,
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
  } = useEarlySellSignals({
    groupRankings, centerOpen, centerMode,
    setPopupPosition, setPopupSize, formatTwd, formatWatchlistPrice,
  });

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const detached = query.get("signalWindow") === "1";
    if (!detached) return;
    setSignalPinnedFrameMode(query.get("signalPinned") === "1");
    const requestedMode = query.get("signalMode") as IntradaySignalCenterMode | null;
    if (requestedMode && INTRADAY_SIGNAL_CENTER_MODES.includes(requestedMode)) setCenterMode(requestedMode);
    setSignalWindowMode(true);
    setCenterOpen(true);
    setCenterPinned(true);
    setCenterPosition(null);
    document.title = "HanStock 盤中訊號中心｜獨立螢幕";
  }, []);

  useEffect(() => {
    if (!signalWindowMode) return;
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
    };
  }, [signalWindowMode]);

  useEffect(() => () => {
    const pinnedWindow = signalPinnedWindowRef.current;
    signalPinnedWindowRef.current = null;
    if (pinnedWindow && !pinnedWindow.closed) pinnedWindow.close();
  }, []);

  useEffect(() => {
    try {
      setCenterPinned(window.localStorage.getItem(EARLY_SELL_CENTER_PINNED_KEY) !== "false");
      const saved = JSON.parse(window.localStorage.getItem(EARLY_SELL_CENTER_POSITION_KEY) ?? "null") as Partial<FloatingPanelPosition> | null;
      if (Number.isFinite(saved?.x) && Number.isFinite(saved?.y)) {
        setCenterPosition({ x: Number(saved?.x), y: Number(saved?.y) });
      }
    } catch {
      setCenterPinned(true);
    } finally {
      centerPreferenceReady.current = true;
    }
  }, []);

  useEffect(() => {
    if (!centerPreferenceReady.current) return;
    try {
      window.localStorage.setItem(EARLY_SELL_CENTER_PINNED_KEY, String(centerPinned));
    } catch {
      // 瀏覽器停用儲存時，釘選仍在目前頁面有效。
    }
  }, [centerPinned]);

  useEffect(() => {
    if (popupExternalWindow || !popupPosition || queue.length === 0) return;
    let frame = 0;
    const fitPopupToViewport = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const panel = popupRef.current;
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        const next = clampSignalCenterPosition(rect.left, rect.top, rect.width, rect.height);
        setPopupPosition((current) => current
          && Math.abs(current.x - next.x) < 0.5
          && Math.abs(current.y - next.y) < 0.5
          ? current
          : next);
      });
    };
    fitPopupToViewport();
    window.addEventListener("resize", fitPopupToViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", fitPopupToViewport);
    };
  }, [popupPosition, queue.length, popupExternalWindow]);

  useEffect(() => {
    if (!centerOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !centerPinned) setCenterOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    const updateBodyLock = () => {
      document.body.style.overflow = window.innerWidth <= 900 && !centerPinned ? "hidden" : previousOverflow;
    };
    updateBodyLock();
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", updateBodyLock);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", updateBodyLock);
    };
  }, [centerOpen, centerPinned]);

  useEffect(() => {
    if (!centerOpen || signalWindowMode) return;
    let frame = 0;
    const fitCenterToViewport = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const panel = centerRef.current;
        if (!panel || window.innerWidth <= 700) return;
        const rect = panel.getBoundingClientRect();
        setCenterPosition((current) => {
          const next = clampSignalCenterPosition(
            current?.x ?? (window.innerWidth - rect.width) / 2,
            current?.y ?? (window.innerHeight - rect.height) / 2,
            rect.width,
            rect.height,
          );
          return current && Math.abs(current.x - next.x) < 0.5 && Math.abs(current.y - next.y) < 0.5 ? current : next;
        });
      });
    };
    fitCenterToViewport();
    window.addEventListener("resize", fitCenterToViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", fitCenterToViewport);
    };
  }, [centerOpen, signalWindowMode]);
  const cancelPopupAutoHide = () => {
    if (popupAutoHideTimer.current === null) return;
    window.clearTimeout(popupAutoHideTimer.current);
    popupAutoHideTimer.current = null;
  };
  const closePopup = () => {
    popupExternalWindowRef.current?.close();
    popupExternalWindowRef.current = null;
    setPopupExternalWindow(null);
    cancelPopupAutoHide();
    dismissPopupSignals();
  };
  const scrollPopupList = (direction: -1 | 1) => {
    const list = popupListRef.current;
    if (!list) return;
    list.scrollBy({ top: direction * Math.max(220, Math.round(list.clientHeight * .82)), behavior: "smooth" });
  };
  const refreshPopupScrollMetrics = () => {
    const list = popupListRef.current;
    const track = popupScrollbarTrackRef.current;
    if (!list || !track) return;
    const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
    const trackHeight = Math.max(0, track.clientHeight);
    const thumbHeight = maxScroll > 0 ? Math.max(52, Math.min(trackHeight, trackHeight * list.clientHeight / list.scrollHeight)) : trackHeight;
    const thumbTravel = Math.max(0, trackHeight - thumbHeight);
    const thumbTop = maxScroll > 0 ? list.scrollTop / maxScroll * thumbTravel : 0;
    setPopupScrollMetrics({ thumbTop, thumbHeight, valueNow: maxScroll > 0 ? Math.round(list.scrollTop / maxScroll * 100) : 0, visible: maxScroll > 0 });
  };
  const startPopupThumbDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const list = popupListRef.current;
    if (!list) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    popupScrollDragRef.current = { pointerId: event.pointerId, startY: event.clientY, startScrollTop: list.scrollTop };
  };
  const movePopupThumb = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = popupScrollDragRef.current;
    const list = popupListRef.current;
    const track = popupScrollbarTrackRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !list || !track) return;
    event.preventDefault();
    const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
    const thumbTravel = Math.max(1, track.clientHeight - popupScrollMetrics.thumbHeight);
    list.scrollTop = Math.max(0, Math.min(maxScroll, drag.startScrollTop + (event.clientY - drag.startY) * maxScroll / thumbTravel));
  };
  const finishPopupThumbDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (popupScrollDragRef.current?.pointerId !== event.pointerId) return;
    popupScrollDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const jumpPopupScrollbar = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const list = popupListRef.current;
    const track = popupScrollbarTrackRef.current;
    if (!list || !track) return;
    const rect = track.getBoundingClientRect();
    const thumbTravel = Math.max(1, rect.height - popupScrollMetrics.thumbHeight);
    const nextTop = Math.max(0, Math.min(thumbTravel, event.clientY - rect.top - popupScrollMetrics.thumbHeight / 2));
    list.scrollTo({ top: nextTop / thumbTravel * Math.max(0, list.scrollHeight - list.clientHeight), behavior: "smooth" });
  };
  const handlePopupThumbKey = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const list = popupListRef.current;
    if (!list) return;
    if (event.key === "Home") { event.preventDefault(); list.scrollTo({ top: 0, behavior: "smooth" }); }
    else if (event.key === "End") { event.preventDefault(); list.scrollTo({ top: list.scrollHeight, behavior: "smooth" }); }
    else if (["ArrowUp", "PageUp"].includes(event.key)) { event.preventDefault(); scrollPopupList(-1); }
    else if (["ArrowDown", "PageDown"].includes(event.key)) { event.preventDefault(); scrollPopupList(1); }
  };
  useEffect(() => {
    if (!popupSignals.length) return;
    const frame = window.requestAnimationFrame(refreshPopupScrollMetrics);
    const list = popupListRef.current;
    const track = popupScrollbarTrackRef.current;
    const observer = list && typeof ResizeObserver !== "undefined" ? new ResizeObserver(refreshPopupScrollMetrics) : null;
    if (list && observer) observer.observe(list);
    if (track && observer) observer.observe(track);
    return () => { window.cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [popupSignals.length, popupSize?.height, popupSize?.width]);
  const schedulePopupAutoHide = () => {
    if (popupPinned) return;
    const touchDevice = document.documentElement.dataset.hanstockDevice;
    if (touchDevice === "ipad" || touchDevice === "iphone" || window.matchMedia("(hover: none), (pointer: coarse)").matches) return;
    cancelPopupAutoHide();
    popupAutoHideTimer.current = window.setTimeout(closePopup, 500);
  };
  useEffect(() => {
    if (popupPinned || popupSignals.length === 0) {
      cancelPopupAutoHide();
      return;
    }
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const panel = popupRef.current;
      if (panel && event.target instanceof Node && !panel.contains(event.target)) closePopup();
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
      cancelPopupAutoHide();
    };
  }, [popupPinned, popupSignals.length]);
  const closePinnedSignalCenter = () => {
    const pinnedWindow = signalPinnedWindowRef.current;
    signalPinnedWindowRef.current = null;
    setSignalPinnedWindow(null);
    if (pinnedWindow && !pinnedWindow.closed) pinnedWindow.close();
  };
  const openPinnedSignalCenter = async () => {
    const currentWindow = signalPinnedWindowRef.current;
    if (currentWindow && !currentWindow.closed) {
      currentWindow.focus();
      return;
    }
    const pictureInPicture = (window as Window & { documentPictureInPicture?: DocumentPictureInPictureApi }).documentPictureInPicture;
    if (!pictureInPicture) {
      window.alert("這個瀏覽器無法開啟螢幕最上層釘選視窗，請使用最新版 Chrome 或 Edge 電腦版。");
      return;
    }
    try {
      const pinnedWindow = await pictureInPicture.requestWindow({ width: 1500, height: 900 });
      const pinnedStyle = pinnedWindow.document.createElement("style");
      pinnedStyle.textContent = "html,body{margin:0;width:100%;height:100%;background:#07090b;overflow:hidden}";
      pinnedWindow.document.head.appendChild(pinnedStyle);
      pinnedWindow.document.title = "HanStock 盤中訊號中心｜最上層釘選";
      signalPinnedWindowRef.current = pinnedWindow;
      setSignalPinnedWindow(pinnedWindow);
      pinnedWindow.addEventListener("pagehide", () => {
        signalPinnedWindowRef.current = null;
        setSignalPinnedWindow(null);
      }, { once: true });
    } catch {
      closePinnedSignalCenter();
    }
  };
  const togglePinnedSignalCenter = async () => {
    if (signalPinnedWindowRef.current && !signalPinnedWindowRef.current.closed) {
      closePinnedSignalCenter();
      return;
    }
    await openPinnedSignalCenter();
  };
  const openSignalCenterWindow = (requestedMode: IntradaySignalCenterMode = centerMode, movePopup = false) => {
    const url = new URL(window.location.href);
    url.searchParams.set("signalWindow", "1");
    url.searchParams.set("signalMode", requestedMode);
    url.hash = "";
    const child = window.open(
      url.toString(),
      "hanstock-intraday-signal-center",
      `popup=yes,width=${Math.max(360, Math.min(1500, window.screen.availWidth - 64))},height=${Math.max(320, Math.min(900, window.screen.availHeight - 64))},resizable=yes,scrollbars=yes`,
    );
    if (!child) {
      window.alert("瀏覽器阻擋了獨立視窗，請允許這個網站開啟彈出式視窗後再試一次。");
      return;
    }
    child.focus();
    if (movePopup) closePopup();
    setCenterOpen(false);
  };
  const returnPopupToPage = () => {
    popupExternalWindowRef.current?.close();
    popupExternalWindowRef.current = null;
    setPopupExternalWindow(null);
  };
  const openPopupOnExternalScreen = () => {
    if (popupExternalWindowRef.current && !popupExternalWindowRef.current.closed) {
      popupExternalWindowRef.current.focus();
      return;
    }
    const child = window.open('', 'hanstock-live-signal-cards', `popup=yes,width=${Math.max(360, Math.min(1200, window.screen.availWidth - 64))},height=${Math.max(320, Math.min(900, window.screen.availHeight - 64))},resizable=yes,scrollbars=yes`);
    if (!child) {
      window.alert('請允許本站開啟彈出式視窗，再按「移到另一螢幕」。也可在 Chrome／Edge 開啟本站使用。');
      return;
    }
    try {
      child.document.title = 'HanStock 盤中全部即時訊號｜外接螢幕';
      child.document.documentElement.lang = 'zh-Hant';
      document.querySelectorAll('link[rel="stylesheet"], style').forEach(element => {
        const copy = element.cloneNode(true) as HTMLElement;
        if (element instanceof HTMLLinkElement) (copy as HTMLLinkElement).href = element.href;
        child.document.head.appendChild(copy);
      });
      const layout = child.document.createElement('style');
      layout.textContent = 'html,body{margin:0;background:#10151b;color:#fff;overflow:hidden} .early-sell-toast{position:fixed!important;inset:0!important;width:100vw!important;max-width:none!important;height:100dvh!important;max-height:none!important;border-radius:0!important} .early-sell-resize-handle{display:none!important} .early-sell-toast header{cursor:default;touch-action:auto} .external-popup-help{color:#ffe184;font-size:13px;line-height:1.5;padding:5px 0}';
      child.document.head.appendChild(layout);
      popupExternalWindowRef.current = child;
      setPopupPinned(true);
      cancelPopupAutoHide();
      setPopupExternalWindow(child);
      child.focus();
    } catch {
      child.close();
      window.alert('目前瀏覽器無法開啟獨立訊號卡片，請改用 Chrome／Edge 開啟本站後再試。');
    }
  };
  const closeSignalCenter = () => {
    if (signalWindowMode) window.close();
    else setCenterOpen(false);
  };
  const startPopupDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (popupExternalWindow || (event.pointerType !== "mouse" && window.innerWidth <= 900) || (event.pointerType === "mouse" && event.button !== 0)) return;
    if ((event.target as HTMLElement).closest("button, input, select, textarea, a")) return;
    const panel = popupRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const position = clampSignalCenterPosition(rect.left, rect.top, rect.width, rect.height);
    popupDragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
      position,
    };
    setPopupPosition(position);
    setPopupPinned(true);
    cancelPopupAutoHide();
    setPopupDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const movePopupDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = popupDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.position = clampSignalCenterPosition(
      event.clientX - drag.offsetX,
      event.clientY - drag.offsetY,
      drag.width,
      drag.height,
    );
    setPopupPosition(drag.position);
  };
  const finishPopupDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = popupDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    popupDragRef.current = null;
    setPopupDragging(false);
    setPopupPosition(drag.position);
    try {
      window.localStorage.setItem(EARLY_SELL_TOAST_POSITION_KEY, JSON.stringify(drag.position));
    } catch {
      // 瀏覽器停用儲存時，拖曳位置仍在本次開啟期間有效。
    }
  };
  const startPopupResize = (event: ReactPointerEvent<HTMLButtonElement>, direction: FloatingPanelResizeDirection) => {
    if (popupExternalWindow) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const panel = popupRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const position = clampFloatingPanelPosition(rect.left, rect.top, rect.width, rect.height);
    const size = { width: rect.width, height: rect.height };
    popupResizeRef.current = {
      pointerId: event.pointerId,
      direction,
      startX: event.clientX,
      startY: event.clientY,
      left: position.x,
      top: position.y,
      width: rect.width,
      height: rect.height,
      position,
      size,
    };
    setPopupPosition(position);
    setPopupSize(size);
    setPopupResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  };
  const movePopupResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const resize = popupResizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - resize.startX;
    const deltaY = event.clientY - resize.startY;
    const { minimumWidth, minimumHeight, maximumWidth, maximumHeight } = floatingPanelSizeLimits();
    let width = resize.width;
    let height = resize.height;
    let left = resize.left;
    let top = resize.top;
    if (resize.direction.includes("e")) width = Math.min(maximumWidth, Math.max(minimumWidth, resize.width + deltaX));
    if (resize.direction.includes("s")) height = Math.min(maximumHeight, Math.max(minimumHeight, resize.height + deltaY));
    if (resize.direction.includes("w")) {
      width = Math.min(maximumWidth, Math.max(minimumWidth, resize.width - deltaX));
      left = resize.left + resize.width - width;
    }
    if (resize.direction.includes("n")) {
      height = Math.min(maximumHeight, Math.max(minimumHeight, resize.height - deltaY));
      top = resize.top + resize.height - height;
    }
    resize.position = clampFloatingPanelPosition(left, top, width, height);
    resize.size = { width, height };
    setPopupPosition(resize.position);
    setPopupSize(resize.size);
  };
  const finishPopupResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const resize = popupResizeRef.current;
    if (!resize || resize.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    popupResizeRef.current = null;
    setPopupResizing(false);
    setPopupPosition(resize.position);
    setPopupSize(resize.size);
    try {
      window.localStorage.setItem(EARLY_SELL_TOAST_POSITION_KEY, JSON.stringify(resize.position));
      window.localStorage.setItem(EARLY_SELL_TOAST_SIZE_KEY, JSON.stringify(resize.size));
    } catch {
      // 瀏覽器停用儲存時，視窗大小仍在本次開啟期間有效。
    }
  };
  const startCenterDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (signalWindowMode || window.innerWidth <= 700 || (event.pointerType === "mouse" && event.button !== 0)) return;
    if ((event.target as HTMLElement).closest("button, input, select, textarea, a")) return;
    const panel = centerRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const position = clampSignalCenterPosition(rect.left, rect.top, rect.width, rect.height);
    centerDragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
      position,
    };
    setCenterPosition(position);
    setCenterPinned(true);
    setCenterDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  const moveCenterDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = centerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.position = clampSignalCenterPosition(
      event.clientX - drag.offsetX,
      event.clientY - drag.offsetY,
      drag.width,
      drag.height,
    );
    setCenterPosition(drag.position);
  };
  const finishCenterDrag = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = centerDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    centerDragRef.current = null;
    setCenterDragging(false);
    setCenterPosition(drag.position);
    try {
      window.localStorage.setItem(EARLY_SELL_CENTER_POSITION_KEY, JSON.stringify(drag.position));
    } catch {
      // 瀏覽器停用儲存時，拖曳仍在本次開啟期間有效。
    }
  };
  const resetCenterPosition = () => {
    const rect = centerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const position = clampSignalCenterPosition((window.innerWidth - rect.width) / 2, (window.innerHeight - rect.height) / 2, rect.width, rect.height);
    setCenterPosition(position);
    try { window.localStorage.setItem(EARLY_SELL_CENTER_POSITION_KEY, JSON.stringify(position)); } catch { /* Keep the current position when storage is unavailable. */ }
  };
  const largeForceStageGroups = [
    { code: "盤中", label: "持續追蹤", time: "10:00–13:30" },
    { code: "C", label: "確認階段", time: "09:30–10:00" },
    { code: "B", label: "確認階段", time: "09:15–09:30" },
    { code: "A", label: "監測階段", time: "09:00–09:15" },
  ].map((stage) => ({
    ...stage,
    signals: largeForceVisibleSignals
      .filter((signal) => intradayLargeForceStage(signal.barTs).code === stage.code)
      .sort((left, right) => right.barTs - left.barTs || left.ticker.localeCompare(right.ticker)),
  }));
  const largeForceStockCount = new Set(largeForceAjSignals.map((signal) => signal.ticker)).size;
  const activeDate = centerMode === "today" ? (combinedTodaySignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "blackDragon" ? (blackDragonSignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "fiveMinuteTwelveShort" ? (combinedTodaySignals.find(isFiveMinuteTwelveShortSignal)?.tradeDate ?? taipeiTradeDate()) : centerMode === "fiveMinuteOnePlusTwoLong" ? (combinedTodaySignals.find(isFiveMinuteOnePlusTwoLongSignal)?.tradeDate ?? taipeiTradeDate()) : centerMode === "instantLarge" ? (instantLargeSignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "mainForce" ? (mainForceSignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "fourGate" ? (fourGateSignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "extraLargeSell" ? (extraLargeSellSignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "extraLargeBuy" ? (extraLargeBuySignals[0]?.tradeDate ?? taipeiTradeDate()) : centerMode === "largeForce" ? (largeForceSignals[0]?.tradeDate ?? todaySignals[0]?.tradeDate ?? selectedDate) : selectedDate;
  const fourGateSignalTotal = strictFourGateSignals.length;
  const mainForceSignalTotal = mainForceSignals.length;
  // 頁籤徽章必須與點入後的實際訊號清單共用同一個計數來源；候選股票
  // 數僅供空清單診斷，不能在已有永久訊號時把 112 則顯示成 0。
  const instantLargeSignalTotal = instantLargeSignals.filter(isInstantLargeSignal).length;
  const instantLargeCandidateTotal: number | null = Number.isFinite(instantLargeCollector?.candidateCount)
    ? Math.max(0, Number(instantLargeCollector?.candidateCount))
    : instantLargeSignals.length > 0 ? instantLargeSignals.length : null;
  const instantLargeCandidateTicks = Math.max(0, Number(instantLargeCollector?.candidateTickCount) || 0);
  const instantLargeEligibleTicks = Math.max(0, Number(instantLargeCollector?.eligibleTickCount) || 0);
  const instantLargeBurstCount = Math.max(0, Number(instantLargeCollector?.burstThresholdCount) || 0);
  const instantLargePersistedCount = Math.max(0, Number(instantLargeCollector?.persistedSignalCount) || 0);
  const instantLargePendingCount = Math.max(0, Number(instantLargeCollector?.pendingSignalCount) || 0);
  const instantLargePersistenceErrors = Math.max(0, Number(instantLargeCollector?.persistenceErrorCount) || 0);
  const instantLargeDiagnosticsReady = Number.isFinite(instantLargeCollector?.candidateTickCount);
  const instantLargeEmptyReason = !instantLargeDiagnosticsReady
    ? "偵測診斷正在同步，畫面每 10 秒自動更新。"
    : instantLargeCandidateTicks === 0
      ? "候選名單已建立，但後台尚未收到這些股票的逐筆成交。"
      : instantLargeEligibleTicks === 0
        ? "已收到候選股逐筆成交，但尚無單筆達 20 張或 100 萬元。"
        : instantLargeBurstCount === 0
          ? "已有大單逐筆，但尚未在同一秒累計達 100 張或 3,000 萬元。"
          : instantLargePendingCount > 0
            ? "已有事件達門檻，訊號已保留並正在補寫永久紀錄。"
            : "已有事件達門檻，訊號畫面正在同步。";
  // 特大買／賣單以「符合條件的個股檔數」計，不以同一檔重算後留下的訊號筆數計。
  const extraLargeSellSignalTotal = new Set(extraLargeSellSignals.map((signal) => signal.ticker)).size;
  const extraLargeBuySignalTotal = new Set(extraLargeBuySignals.map((signal) => signal.ticker)).size;
  const largeForceSignalTotal = largeForceAjSignals.length;
  const fiveMinuteTwelveShortSignalTotal = combinedTodaySignals.filter(isFiveMinuteTwelveShortSignal).length;
  const fiveMinuteOnePlusTwoLongSignalTotal = combinedTodaySignals.filter(isFiveMinuteOnePlusTwoLongSignal).length;
  const todaySignalTotal = combinedTodaySignals.length;
  const blackDragonSignalTotal = blackDragonSignals.length;
  const coreCount = (value: number) => signalSnapshotReady ? value : "…";
  const todayCount = signalSnapshotReady ? todaySignalTotal : "…";
  const allSignalTotal = new Set(combinedTodaySignals.map(intradaySignalKey)).size;
  const popupSignalModes = new Set(popupSignals.map(intradaySignalCenterModeFor));
  // 混合提醒一定回到「今日即時」，不能因清單裡剛好包含一筆瞬間
  // 大單，就把整個視窗誤標成單一類型，並把「查看全部」帶到錯的頁籤。
  const popupCenterMode: IntradaySignalCenterMode = popupSignalModes.size === 1
    ? [...popupSignalModes][0] ?? "today"
    : "today";
  const popupIsFourGate = popupCenterMode === "fourGate";
  const popupIsMainForce = popupCenterMode === "mainForce";
  const popupIsExtraLargeSell = popupCenterMode === "extraLargeSell";
  const popupIsExtraLargeBuy = popupCenterMode === "extraLargeBuy";
  const popupIsLargeForce = popupCenterMode === "largeForce";
  const popupIsInstantLarge = popupCenterMode === "instantLarge";
  const popupIsFiveMinuteTwelveShort = popupCenterMode === "fiveMinuteTwelveShort";
  const popupIsFiveMinuteOnePlusTwoLong = popupCenterMode === "fiveMinuteOnePlusTwoLong";
  const toneForChange = (changePct: number | null | undefined) => changePct === null || changePct === undefined || changePct === 0 ? "neutral" : changePct > 0 ? "positive" : "negative";
  const changeLabel = (changePct: number | null | undefined) => changePct === null || changePct === undefined ? "漲跌 —" : `${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%`;
  const popupPanel = !signalWindowMode && signalAlertsEnabled && popupSignals.length > 0 ? <aside
        ref={popupRef}
        className={`early-sell-toast${popupPinned ? " is-pinned" : ""}${popupPosition ? " is-positioned" : ""}${popupDragging ? " is-dragging" : ""}${popupResizing ? " is-resizing" : ""}`}
        style={{
          ...(popupPosition ? { left: popupPosition.x, top: popupPosition.y, right: "auto" } : {}),
          ...(popupSize ? { width: popupSize.width, height: popupSize.height } : {}),
        }}
        role="alert"
        aria-live="assertive"
        aria-label={`最新盤中即時訊號（${popupPinned ? "已釘選" : "未釘選"}）`}
        onPointerEnter={cancelPopupAutoHide}
        onPointerLeave={schedulePopupAutoHide}
      >
        {FLOATING_PANEL_RESIZE_DIRECTIONS.map((direction) => <button
          className={`early-sell-resize-handle direction-${direction}`}
          type="button"
          aria-label={`調整訊號視窗${direction}方向大小`}
          key={direction}
          onPointerDown={(event) => startPopupResize(event, direction)}
          onPointerMove={movePopupResize}
          onPointerUp={finishPopupResize}
          onPointerCancel={finishPopupResize}
        />)}
        <header
          title="按住標題區可拖曳訊號窗"
          onPointerDown={startPopupDrag}
          onPointerMove={movePopupDrag}
          onPointerUp={finishPopupDrag}
          onPointerCancel={finishPopupDrag}
        >
          <div>{popupExternalWindow && <p className="external-popup-help">拖曳視窗最上方標題列到外接螢幕；Win＋Shift＋←／→ 也可切換。請保持主頁開啟，訊號會持續同步。</p>}<span>{popupIsFiveMinuteTwelveShort ? `12空（五分K）即時訊號｜${activeDate.replaceAll("-", "/")}` : popupIsFiveMinuteOnePlusTwoLong ? `1+2多（五分K）即時訊號｜${activeDate.replaceAll("-", "/")}` : popupIsInstantLarge ? `族群瞬間大單｜${activeDate.replaceAll("-", "/")}` : popupIsExtraLargeSell ? `盤中特大賣單｜${activeDate.replaceAll("-", "/")}` : popupIsExtraLargeBuy ? `盤中特大買單｜${activeDate.replaceAll("-", "/")}` : popupIsLargeForce ? `🐋 盤中大戶力｜${activeDate.replaceAll("-", "/")}` : popupIsFourGate ? `四項精選即時訊號｜${activeDate.replaceAll("-", "/")}` : popupIsMainForce ? `主力累計即時訊號｜${activeDate.replaceAll("-", "/")}` : popupSignals.some((item) => item.demo) ? "歷史警示示範｜2026/08/14" : "盤中全部即時訊號｜09:00–13:30"}</span><strong>{popupIsFiveMinuteTwelveShort ? `1高、破惡均下彎、2不過1高｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsFiveMinuteOnePlusTwoLong ? `同時站上昨日高與 905高｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsInstantLarge ? `漲跌幅前 20 族群逐筆成交即時偵測｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsExtraLargeSell ? `盤中大單賣出累計已達前日大單淨額｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsExtraLargeBuy ? `盤中大單買進累計已達前日大單淨賣超｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsLargeForce ? `全市場盤中大戶力達 ±12%｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsFourGate ? `今日正式即時計算｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupIsMainForce ? `主力累計 A～D 同步濾網｜${popupSignals.length} 則完整顯示｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}` : popupSignals.some((item) => item.demo) ? "歷史示範，不列入今日正式警示紀錄" : `全部 ${popupSignalModes.size} 類訊號合併顯示｜共 ${popupSignals.length} 則｜${popupPinned ? "已釘選固定" : "移出或點外面自動收起"}`}</strong></div>
          <div className="early-sell-toast-actions">
            <button className="early-sell-popout" type="button" onClick={popupExternalWindow ? returnPopupToPage : openPopupOnExternalScreen} title="保留同一份即時訊號卡片，移到獨立視窗">{popupExternalWindow ? "返回主畫面" : "↗ 移到另一螢幕"}</button>
            <button className="early-sell-pin-toggle" type="button" disabled={Boolean(popupExternalWindow)} aria-pressed={popupPinned} aria-label={popupPinned ? "目前已釘選，點擊解除釘選" : "目前未釘選，點擊固定顯示"} title={popupPinned ? "目前固定顯示；點一下解除釘選" : "目前會自動收起；點一下固定顯示"} onClick={() => setPopupPinned((current) => !current)}>{popupPinned ? "📌 已釘選" : "📍 未釘選"}</button>
            <button className="early-sell-toast-close" type="button" onClick={closePopup} aria-label="收起全部盤中即時訊號">×</button>
          </div>
        </header>
        <LiveSyncStatus polledAt={signalFeed?.polledAt} sourceAt={signalCollection.sourceAt} checkedAt={signalCollection.checkedAt} error={signalSyncError || signalCollection.error} latestSignalAt={popupSignals[0]?.barTs} />
        <div className="early-sell-toast-scroll-shell">
          <button className="early-sell-toast-scroll-button is-up" type="button" aria-label="向上捲動盤中訊號" title="向上捲動" onClick={(event) => { event.stopPropagation(); scrollPopupList(-1); }}>▲</button>
        <div id="intraday-signal-popup-list" ref={popupListRef} className="early-sell-toast-list" role="list" onScroll={refreshPopupScrollMetrics}>
          {popupSignals.map((item) => {
            const meta = stockMeta[item.ticker];
            const technical = signalTechnicalMeta[item.ticker];
            const position = technical?.riverBase ? valuationRiverPosition(item.price, technical.riverBase) : null;
            const displayName = meta?.name && meta.name !== item.ticker ? meta.name : item.name;
            const tone = toneForChange(meta?.changePct);
            const directionClass = intradaySignalTone(item.kind);
            const source = intradaySignalSource(item);
            const groupRank = intradaySignalGroupRank(item, meta, effectiveGroupRankings);
            const chipRank = extractMainForceChipRank(item.note);
            return <button className={`early-sell-toast-row${directionClass}${isFourGateSignal(item) ? " is-four-gate" : ""}`} type="button" role="listitem" key={`${item.tradeDate}:${item.ticker}:${item.kind}:${item.barTs}`} onClick={() => { if (!popupPinned) closePopup(); openKlineByTicker(item.ticker, displayName, item.barTs); }}>
              <time><small>訊號成立</small>{signalCenterTime(item)}<small>{item.tradeDate.slice(5).replace("-", "/")}</small></time>
              <span className="early-sell-toast-stock"><span className="early-sell-toast-stock-identity"><b>{item.ticker}</b><strong>{displayName}</strong></span><span className="early-signal-technical-badges"><i className={`is-position ${position ? `is-${position}` : "is-pending"}`}>{position ?? "位置待算"}</i><i className="is-ma-score">均線 {technical?.maScore ?? "—"}/15</i></span><small className={`early-signal-stock-meta ${tone}`}><span>{meta?.group ?? extractMainForceGroupRank(item.note)?.group ?? "族群讀取中"}</span><i>{changeLabel(meta?.changePct)}</i><SignalIntradayLargeForceBadge signal={item} value={signalLargeForceValues[`${item.tradeDate}:${item.ticker}`]} /></small><StockTradingBadges ticker={item.ticker} compact /><em className={`early-signal-source ${source.className}`}>{source.label}</em></span>
              <span className="early-sell-toast-detail"><span className="early-signal-title-line"><b className={intradaySignalStrongBadge(item.kind)}>{intradaySignalText(item.label)}</b>{groupRank && <em className={`main-force-group-rank ${intradaySignalGroupRankClass(groupRank)}`}>{groupRank.group}・{groupRank.direction}第 {groupRank.rank} 名</em>}{chipRank && <em className={`main-force-chip-rank ${chipRank.direction === "增加" ? "is-increasing" : "is-decreasing"}`}>{intradaySignalChipRankText(item, chipRank)}</em>}</span><small className={isFourGateSignal(item) ? "four-gate-signal-note" : undefined}>{displayIntradaySignalNote(item)}</small></span>
              <span className="early-sell-toast-price"><small>成交價</small><strong>{item.price.toLocaleString("zh-TW")}</strong></span>
            </button>;
          })}
        </div>
          <div ref={popupScrollbarTrackRef} className={`early-sell-toast-scroll-track${popupScrollMetrics.visible ? " is-visible" : ""}`} onPointerDown={jumpPopupScrollbar} aria-hidden={!popupScrollMetrics.visible}>
            <button className="early-sell-toast-scroll-thumb" type="button" role="scrollbar" aria-label="拖曳盤中訊號捲軸" aria-controls="intraday-signal-popup-list" aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100} aria-valuenow={popupScrollMetrics.valueNow} disabled={!popupScrollMetrics.visible} tabIndex={popupScrollMetrics.visible ? 0 : -1} style={{ height: popupScrollMetrics.thumbHeight, transform: `translateY(${popupScrollMetrics.thumbTop}px)` }} onPointerDown={startPopupThumbDrag} onPointerMove={movePopupThumb} onPointerUp={finishPopupThumbDrag} onPointerCancel={finishPopupThumbDrag} onKeyDown={handlePopupThumbKey} />
          </div>
          <button className="early-sell-toast-scroll-button is-down" type="button" aria-label="向下捲動盤中訊號" title="向下捲動" onClick={(event) => { event.stopPropagation(); scrollPopupList(1); }}>▼</button>
        </div>
        <footer>
          <button type="button" onClick={() => { if (popupExternalWindow) returnPopupToPage(); if (!popupPinned) closePopup(); setCenterMode(popupCenterMode); setCenterOpen(true); window.focus(); }}>查看全部／歷史查詢</button>
          <button type="button" onClick={() => changeSignalAlerts(false)}>⏸ 暫停本次提醒</button>
        </footer>
        <em>已顯示全部 {popupSignals.length} 則；舊日期請按「查看全部／歷史查詢」</em>
      </aside> : null;
  const signalPinnedPortal = signalPinnedWindow && !signalPinnedWindow.closed ? createPortal(
    <main style={{ width: "100vw", height: "100vh", display: "grid", gridTemplateRows: "48px minmax(0, 1fr)", background: "#07090b", color: "#fff" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "0 14px", background: "#151a20", borderBottom: "1px solid #39414b", fontFamily: "system-ui, sans-serif" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}><strong>📌 HanStock 盤中訊號中心</strong><span style={{ color: "#f4d76b", fontSize: 13 }}>螢幕最上層顯示中｜可移到另一個螢幕</span></div>
        <button type="button" onClick={closePinnedSignalCenter} style={{ border: "1px solid #8b7442", borderRadius: 8, padding: "7px 14px", background: "#332a18", color: "#ffe58a", fontWeight: 800, cursor: "pointer" }}>解除置頂</button>
      </header>
      <iframe
        src={`/?signalWindow=1&signalPinned=1&signalMode=${encodeURIComponent(centerMode)}`}
        title="HanStock 盤中訊號中心最上層釘選"
        style={{ width: "100%", height: "100%", border: 0, background: "#07090b" }}
      />
    </main>,
    signalPinnedWindow.document.body,
  ) : null;

  return (
    <div className={`topbar-status${centerOpen ? " has-signal-center-open" : ""}${signalWindowMode ? " is-signal-window" : ""}`}>
      {signalPinnedPortal}
      <div className="early-signal-controls">
        <button className="early-signal-trigger" type="button" onClick={() => setCenterOpen(true)} aria-haspopup="dialog">
          <span>🔔 盤中訊號</span><b>{allSignalTotal}</b>
        </button>
        <button className={`early-signal-master-toggle${signalAlertsEnabled ? " is-on" : " is-off"}`} type="button" role="switch" aria-checked={signalAlertsEnabled} onClick={() => changeSignalAlerts(!signalAlertsEnabled)} title={signalAlertsEnabled ? "暫停本次開啟期間的盤中訊號自動跳出" : "恢復盤中訊號自動跳出"}>
          <i aria-hidden="true" /><span>{signalAlertsEnabled ? "提醒開啟" : "提醒暫停"}</span>
        </button>
      </div>
      <div className="market-state"><i />盤中 <b>{marketTime}</b></div>

      {popupExternalWindow && !popupExternalWindow.closed ? createPortal(popupPanel ?? <div style={{ padding: 24 }}>目前沒有顯示中的訊號。<button type="button" onClick={returnPopupToPage}>返回主畫面</button></div>, popupExternalWindow.document.body) : popupPanel}

      {centerOpen && <div className={`early-signal-backdrop${centerPinned ? " is-pinned" : ""}${signalWindowMode ? " is-detached" : ""}`} role="presentation" onMouseDown={(event) => {
        if (!centerPinned && event.target === event.currentTarget) setCenterOpen(false);
      }}>
        <section
          ref={centerRef}
          className={`early-signal-center${centerPosition && !signalWindowMode ? " is-positioned" : ""}${centerDragging ? " is-dragging" : ""}${centerPinned ? " is-pinned" : ""}${signalWindowMode ? " is-detached" : ""}`}
          style={centerPosition && !signalWindowMode ? { left: centerPosition.x, top: centerPosition.y } : undefined}
          role="dialog"
          aria-modal={!centerPinned}
          aria-labelledby="early-signal-center-title"
        >
          <header
            className="early-signal-center-head"
            title="按住上方標題區可往上、下、左、右拖曳視窗"
            onPointerDown={startCenterDrag}
            onPointerMove={moveCenterDrag}
            onPointerUp={finishCenterDrag}
            onPointerCancel={finishCenterDrag}
            onLostPointerCapture={finishCenterDrag}
          >
            <div><span>INTRADAY SIGNALS · 09:00–13:30 · 1 MIN</span><h2 id="early-signal-center-title">盤中訊號中心</h2><small className="signal-center-drag-hint">{signalWindowMode ? "拖曳視窗最上方標題列到外接螢幕；Windows 可按 Win＋Shift＋←／→。若開成分頁，請先將分頁拖出成獨立視窗。" : "按住標題可在頁面內移動；移到外接螢幕請按右側「移到另一螢幕」。"}</small><p>主力累計 A～D 同步濾網：零軸翻向連續確認、淨額率、VWAP 同步與距離、量能門檻同時通過才顯示；訊號依交易日永久保留。</p></div>
            <div className="early-signal-center-actions">
              <div className="early-signal-header-date">{centerMode === "history" ? <label><span>交易日期</span><input type="date" value={selectedDate} max={taipeiTradeDate()} onChange={(event) => { if (event.target.value) { selectHistoryDate(event.target.value); } }} list="early-signal-dates" /></label> : <div><span>交易日期</span><strong>{activeDate.replaceAll("-", "/")}</strong></div>}</div>
              <Link className="early-signal-center-help" href="/education?category=intraday" onPointerDown={(event) => event.stopPropagation()} title="查看盤中訊號名詞、公式與排除條件">? 訊號教學</Link>
              {!signalWindowMode && popupSignals.length > 0 && <button className="early-signal-center-alerts" type="button" onClick={() => setCenterOpen(false)} title="收起訊號中心並返回最新提醒">🔔 最新提醒 {popupSignals.length}</button>}
              {!signalWindowMode && <button className="early-signal-center-popout" type="button" onClick={() => openSignalCenterWindow(centerMode)} title="開成獨立視窗後可拖到另一個螢幕">↗ 移到另一螢幕</button>}
              {!signalWindowMode && <button className="early-signal-center-reset" type="button" onClick={resetCenterPosition} title="將訊號中心移回畫面中央">回到中央</button>}
              {!signalWindowMode && <button className="early-signal-center-pin" type="button" aria-pressed={centerPinned} onClick={() => setCenterPinned((current) => !current)} title={centerPinned ? "解除釘選後可點外面關閉" : "釘選後操作其他功能也不會消失"}>{centerPinned ? "📌 已釘選" : "📍 釘選視窗"}</button>}
              {signalWindowMode && !signalPinnedFrameMode && <button className="early-signal-center-pin" type="button" aria-pressed={Boolean(signalPinnedWindow && !signalPinnedWindow.closed)} onClick={() => void togglePinnedSignalCenter()} title="開啟可移到其他螢幕、且不會被其他視窗蓋住的最上層畫面">{signalPinnedWindow && !signalPinnedWindow.closed ? "📌 取消最上層" : "📌 置頂最上層"}</button>}
              {signalPinnedFrameMode && <button className="early-signal-center-pin" type="button" disabled aria-label="盤中訊號中心已置頂最上層">📌 最上層顯示中</button>}
              {!signalPinnedFrameMode && <button className="early-signal-center-close" type="button" onClick={closeSignalCenter} aria-label={signalWindowMode ? "關閉獨立盤中訊號視窗" : "關閉盤中訊號中心"}>×</button>}
            </div>
          </header>
          <datalist id="early-signal-dates">{availableDates.map((date) => <option value={date} key={date} />)}</datalist>
          <div className="early-signal-tabs" role="tablist" aria-label="盤中訊號檢視方式">
            <button className={centerMode === "today" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "today"} onClick={() => setCenterMode("today")}>今日即時 <b>{todayCount}</b></button>
            <button className={centerMode === "blackDragon" ? "active daily-strategies" : "daily-strategies"} type="button" role="tab" aria-selected={centerMode === "blackDragon"} onClick={() => setCenterMode("blackDragon")}><span>🐉 創高的黑龍 <b>{blackDragonReady ? blackDragonSignalTotal : "…"}</b></span><small>11:00 前暫不顯示訊號</small></button>
            <button className={centerMode === "fiveMinuteTwelveShort" ? "active five-minute-twelve-short" : "five-minute-twelve-short"} type="button" role="tab" aria-selected={centerMode === "fiveMinuteTwelveShort"} onClick={() => setCenterMode("fiveMinuteTwelveShort")}>📉 12空（五分K） <b>{coreCount(fiveMinuteTwelveShortSignalTotal)}</b></button>
            <button className={centerMode === "fiveMinuteOnePlusTwoLong" ? "active five-minute-one-plus-two-long" : "five-minute-one-plus-two-long"} type="button" role="tab" aria-selected={centerMode === "fiveMinuteOnePlusTwoLong"} onClick={() => setCenterMode("fiveMinuteOnePlusTwoLong")}>📈 1+2多（五分K） <b>{coreCount(fiveMinuteOnePlusTwoLongSignalTotal)}</b></button>
            <button className={centerMode === "instantLarge" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "instantLarge"} onClick={() => setCenterMode("instantLarge")}>⚡ 族群瞬間大單 <b>{coreCount(instantLargeSignalTotal)}</b></button>
            <button className={centerMode === "mainForce" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "mainForce"} onClick={() => setCenterMode("mainForce")}>主力累計 <b>{coreCount(mainForceSignalTotal)}</b></button>
            <button className={centerMode === "fourGate" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "fourGate"} onClick={() => setCenterMode("fourGate")}>四項精選 <b>{coreCount(fourGateSignalTotal)}</b></button>
            <button className={centerMode === "extraLargeSell" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "extraLargeSell"} onClick={() => setCenterMode("extraLargeSell")}>🟢 盤中特大賣單 <b>{coreCount(extraLargeSellSignalTotal)}</b></button>
            <button className={centerMode === "extraLargeBuy" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "extraLargeBuy"} onClick={() => setCenterMode("extraLargeBuy")}>🔴 盤中特大買單 <b>{coreCount(extraLargeBuySignalTotal)}</b></button>
            <button className={centerMode === "largeForce" ? "active large-force" : "large-force"} type="button" role="tab" aria-selected={centerMode === "largeForce"} onClick={() => setCenterMode("largeForce")}>🐋 盤中大戶力 <b>{signalSnapshotReady && (largeForceSignals.length === 0 || largeForceAjStatus === "ready") ? largeForceSignalTotal : largeForceAjStatus === "error" ? "重試中" : "…"}</b></button>
            <button className={centerMode === "history" ? "active" : ""} type="button" role="tab" aria-selected={centerMode === "history"} onClick={() => setCenterMode("history")}>歷史查詢</button>
          </div>
          {<div className="early-signal-summary"><span><i />{centerMode === "today" ? "今日全部盤中訊號 · 同步包含 12空（五分K）、1+2多（五分K）、盤中大戶力 · 原始紀錄永久保留" : centerMode === "blackDragon" ? "創高的黑龍 · 11:00 前暫不顯示訊號 · 11:00 起每 5 分 K 掃描 · 同根最高價須突破前五日高點，當時價須低於今日 09:00 開盤 · 不沿用較早創高 · 以下保留盤中成立紀錄，盤後日 K 名單請至選股程式" : centerMode === "fiveMinuteTwelveShort" ? "12空（五分K） · 先破 905低 · 形成 1高 · 跌破 MA20 且均線下彎 · 2高不過 1高後轉弱 · 今日由 09:00 起完整回補" : centerMode === "fiveMinuteOnePlusTwoLong" ? "1+2多（五分K） · 五分K同時站上昨日高與 905高才成立 · 只過其中一高不算 · 今日由 09:00 起完整回補" : centerMode === "instantLarge" ? "漲幅前 20 族群偵測外盤買進、跌幅前 20 族群偵測內盤賣出 · 敲進須觸發當分鐘大戶力為正，倒出須為負 · 一般達 100 張或 3,000 萬元；特大達 300 張或 5,000 萬元 · 同方向 5 分鐘冷卻 · 逐筆即時" : centerMode === "mainForce" ? "A～D 同步發動濾網 · 09:05 起正式顯示 · 每方向僅一次 · 永久保留" : centerMode === "fourGate" ? "前日預估賣壓金額 > 1 億元 · 今日即時計算 · 隔日沖加強 · 四項同時通過才顯示" : centerMode === "extraLargeSell" ? "前日大單淨買超 > 5,000 萬元、隔日沖淨額資金占比 > 10%，且盤中大單賣出累計 ≥ 前日大單淨買超、觸發時大戶力為負 · 同步標示族群漲跌前 10 與前日籌碼減少前 100 · 永久保留" : centerMode === "extraLargeBuy" ? "前日大單淨賣超 > 5,000 萬元、隔日沖淨額資金占比 > 10%，且盤中大單買進累計 ≥ 前日大單淨賣超、觸發時大戶力為正 · 同步標示族群漲跌前 10 與前日籌碼減少前 100 · 永久保留" : centerMode === "largeForce" ? `盤中大戶力多空達標條件：多方須大戶力 ≥ +10%（正式訊號仍採 +12%）、成交額 ≥ 3 億、同一族群近 3 交易日曾在後 20、今日進入前 20；空方為大戶力 ≤ -10%（正式訊號仍採 -12%）、成交額 ≥ 3 億、同一族群近 3 交易日曾進前 20、今日跌出前 20${largeForceAjPreviousDates.length === 3 ? `｜回看 ${largeForceAjPreviousDates.join("、")}` : ""}` : historyLoading ? "正在更新歷史紀錄 · 原清單保持顯示" : "永久歷史紀錄 · 每筆標示掃描來源"}{centerMode === "today" && signalFeed?.polledAt ? <small className={`early-signal-feed-status${signalFeed.degraded ? " is-degraded" : ""}`}>{signalFeed.degraded ? `逐筆來源延遲｜1 分 K 備援${signalFeed.fallbackAt ? `已接手至 ${earlySellTime(signalFeed.fallbackAt)}` : "接手中"}` : "已存快照即時顯示"}｜畫面更新 {earlySellTime(signalFeed.polledAt)}｜最新符合訊號 {combinedTodaySignals[0] ? earlySellTime(combinedTodaySignals[0].barTs) : "尚無"}</small> : null}</span><strong>{centerMode === "largeForce" ? `${largeForceVisibleSignals.length} 則／${largeForceStockCount} 檔` : `${selectedCenterReady ? visibleSignals.length : "…"} 則訊號`}</strong></div>}
          {(centerMode === "extraLargeSell" || centerMode === "extraLargeBuy") && <p className="early-signal-data-status" role="status">{extraLargeCheck === "complete" ? "資料核對完成；僅顯示達到金額、方向及族群條件的訊號。" : "目前數字是已驗證並保存的訊號，不代表市場沒有大單。正在核對前日基準與盤中資料；資料未完整時會繼續補查。"}</p>}
          <div className="early-signal-list" role="list" key={centerMode}>
            {centerMode === "largeForce" ? !selectedCenterReady ? <div className="early-signal-empty"><strong>正在套用盤中大戶力多空條件…</strong><span>正在比對訊號成交額、近三個交易日族群強弱與今日反轉排名。</span></div> : largeForceVisibleSignals.length ? largeForceStageGroups.map((stage) => <section className="large-force-stage-group" key={stage.code} aria-label={`階段 ${stage.code} ${stage.time}`}>
              <div className="large-force-monitor-stage" role="status">
                <b>階段 {stage.code}</b><strong>{stage.label}</strong><span>{stage.time}</span><em>{stage.signals.length} 則</em>
              </div>
              {stage.signals.length ? stage.signals.map((item) => {
                const meta = stockMeta[item.ticker];
                const displayName = meta?.name && meta.name !== item.ticker ? meta.name : item.name;
                const displayChangePct = meta?.changePct ?? item.changePct;
                const tone = toneForChange(displayChangePct);
                const directionClass = intradaySignalTone(item.kind);
                const ajTransition = largeForceAjTransitions[item.ticker];
                const ajGroup = item.kind === "intradayLargeForceBuy" ? ajTransition?.bullishQualifyingGroup : ajTransition?.qualifyingGroup;
                const ajTransitionText = item.kind === "intradayLargeForceBuy"
                  ? `${ajGroup}・近 3 日最弱第 ${ajTransition?.worstRecentRank} → 今日第 ${ajTransition?.bullishCurrentRank}`
                  : `${ajGroup}・近 3 日最佳第 ${ajTransition?.bestRecentRank} → 今日第 ${ajTransition?.currentRank}`;
                return <button className={`early-signal-row large-force-monitor-row${directionClass}`} type="button" role="listitem" key={`${item.tradeDate}:${item.ticker}:${item.kind}:${item.barTs}`} onClick={() => openKlineByTicker(item.ticker, displayName, item.barTs)}>
                  <time>{signalCenterTime(item)}<small>{item.tradeDate.replaceAll("-", "/")}</small></time>
                  <span><span className="early-signal-stock-identity"><b>{item.ticker}</b><strong>{displayName}</strong></span><small className={`early-signal-stock-meta ${tone}`}><span>{meta?.group ?? extractMainForceGroupRank(item.note)?.group ?? "族群讀取中"}</span><i>{changeLabel(displayChangePct)}</i><SignalIntradayLargeForceBadge signal={item} value={signalLargeForceValues[`${item.tradeDate}:${item.ticker}`]} /></small><StockTradingBadges ticker={item.ticker} compact /><em className="early-signal-source is-full-market">{item.kind === "intradayLargeForceBuy" ? "多方大戶力達標" : "空方大戶力達標"}</em></span>
                  <span className="early-signal-detail"><span className="early-signal-title-line"><b className={intradaySignalStrongBadge(item.kind)}>{intradaySignalText(item.label)}</b>{ajGroup && <em className="aj-large-force-transition">{ajTransitionText}</em>}</span><small>{displayIntradaySignalNote(item)}</small></span>
                  <span><small>訊號成交價</small><strong>{item.price.toLocaleString("zh-TW")}</strong></span>
                  <i>開啟 5 分 K ›</i>
                </button>;
              }) : <div className="large-force-stage-empty">本階段沒有通過大戶力、成交額與族群強弱反轉四項交集的股票</div>}
            </section>) : <div className="early-signal-empty"><strong>{largeForceAjStatus === "error" ? "族群強弱回測資料暫時無法讀取" : "目前沒有符合多方／空方大戶力達標條件的股票"}</strong><span>{largeForceAjStatus === "error" ? "為避免把未篩選多空訊號誤列進來，暫時不顯示；資料恢復後會自動更新。" : "多方須為近三日後 20 族群今日進入前 20；空方須為近三日前 20 族群今日跌出前 20；兩邊同時要求大戶力與成交額達標。"}</span></div> : !selectedCenterReady ? <div className="early-signal-empty"><strong>正在讀取已存訊號…</strong><span>先顯示今日快照，背景持續偵測新大單。</span></div> : visibleSignals.length ? visibleSignals.map((item) => {
              const meta = stockMeta[item.ticker];
              const displayName = meta?.name && meta.name !== item.ticker ? meta.name : item.name;
              const displayChangePct = meta?.changePct ?? item.changePct;
              const tone = toneForChange(displayChangePct);
              const directionClass = intradaySignalTone(item.kind);
              const source = intradaySignalSource(item);
              const groupRank = intradaySignalGroupRank(item, meta, effectiveGroupRankings);
              const chipRank = extractMainForceChipRank(item.note);
              return <button className={`early-signal-row${directionClass}${item.kind === "intradayExtraLargeSell" ? " is-extra-large-sell" : item.kind === "intradayExtraLargeBuy" ? " is-extra-large-buy" : ""}${isFourGateSignal(item) ? " is-four-gate" : ""}`} type="button" role="listitem" key={`${item.tradeDate}:${item.ticker}:${item.kind}:${item.barTs}`} onClick={() => openKlineByTicker(item.ticker, displayName, item.barTs)}>
                <time>{signalCenterTime(item)}<small>{item.tradeDate.replaceAll("-", "/")}</small></time>
                <span><span className="early-signal-stock-identity"><b>{item.ticker}</b><strong>{displayName}</strong></span><small className={`early-signal-stock-meta ${tone}`}><span>{meta?.group ?? extractMainForceGroupRank(item.note)?.group ?? "族群讀取中"}</span><i>{changeLabel(displayChangePct)}</i><SignalIntradayLargeForceBadge signal={item} value={signalLargeForceValues[`${item.tradeDate}:${item.ticker}`]} /></small><StockTradingBadges ticker={item.ticker} compact /><em className={`early-signal-source ${source.className}`}>{source.label}</em>{isFourGateSignal(item) ? <em className="early-signal-replay-tag">四項通過</em> : item.demo && <em className="early-signal-replay-tag">歷史示範</em>}</span>
                <span className="early-signal-detail"><span className="early-signal-title-line"><b className={intradaySignalStrongBadge(item.kind)}>{intradaySignalText(item.label)}</b>{groupRank && <em className={`main-force-group-rank ${intradaySignalGroupRankClass(groupRank)}`}>{groupRank.group}・{groupRank.direction}第 {groupRank.rank} 名</em>}{chipRank && <em className={`main-force-chip-rank ${chipRank.direction === "增加" ? "is-increasing" : "is-decreasing"}`}>{intradaySignalChipRankText(item, chipRank)}</em>}</span><small className={isFourGateSignal(item) ? "four-gate-signal-note" : item.kind === "intradayExtraLargeSell" || item.kind === "intradayExtraLargeBuy" ? "intraday-extra-large-sell-note" : undefined}>{displayIntradaySignalNote(item)}</small></span>
                <span><small>訊號成交價</small><strong>{item.price.toLocaleString("zh-TW")}</strong></span>
                <i>開啟 5 分 K ›</i>
              </button>;
            }) : <div className={`early-signal-empty${centerMode === "instantLarge" ? " is-instant-large" : ""}`}><strong>目前沒有符合條件的訊號</strong>{centerMode === "instantLarge" ? <><span>{instantLargeEmptyReason}</span><div className="instant-large-diagnostics" aria-label="族群瞬間大單即時診斷"><span>監控股票<b>{instantLargeCandidateTotal ?? 0}</b>檔</span><span>候選逐筆<b>{instantLargeCandidateTicks}</b>筆</span><span>單筆達標<b>{instantLargeEligibleTicks}</b>筆</span><span>同秒達標<b>{instantLargeBurstCount}</b>次</span><span>已存訊號<b>{instantLargePersistedCount}</b>則</span><span className={instantLargePendingCount > 0 ? "is-warning" : ""}>待補存<b>{instantLargePendingCount}</b>則</span>{instantLargePersistenceErrors > 0 ? <span className="is-warning">寫入錯誤<b>{instantLargePersistenceErrors}</b>次</span> : null}</div></> : <span>{historyMessage || (centerMode === "today" ? "全市場五分K正在同步掃描；12空須完成 1高、破惡均下彎、2不過1高，1+2多須同時站上昨日高與 905高。" : centerMode === "fiveMinuteTwelveShort" ? "正在從今天 09:00 起回掃完整五分K；符合 1高、破惡均下彎、2不過1高後會補入。" : centerMode === "fiveMinuteOnePlusTwoLong" ? "正在從今天 09:00 起回掃完整五分K；同時站上昨日高與 905高後會補入。" : centerMode === "fourGate" ? "只保留前日預估賣壓金額超過 1 億元，且四項同時通過的個股。" : centerMode === "extraLargeSell" ? "只保留前日大單淨買超超過 5,000 萬元、隔日沖淨額資金占比超過 10%，且盤中賣出累計達標、觸發時大戶力為負的個股。" : centerMode === "extraLargeBuy" ? "只保留前日大單淨賣超超過 5,000 萬元、隔日沖淨額資金占比超過 10%，且盤中買進累計達標、觸發時大戶力為正的個股。" : "可切換日期再查看。")}</span>}</div>}
          </div>
        </section>
      </div>}
    </div>
  );
}

function fallbackGroupStocks(groupName: string): GroupStock[] {
  const group = [...strongGroups, ...weakGroups].find((item) => item.name === groupName);
  if (!group) return [];
  const match = group.lead.match(/^(\d{4,6})\s*(.*)$/);
  if (!match) return [];
  return [{ ticker: match[1], name: match[2], price: "—", change: group.leadChange }];
}

function formatWatchlistPrice(value: number | null | undefined, fallback: string) {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  return value.toLocaleString("zh-TW", {
    minimumFractionDigits: value < 1_000 ? 2 : 0,
    maximumFractionDigits: 2,
  });
}

function formatWatchlistChange(value: number | null | undefined, fallback: string) {
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback;
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function WatchlistPanel() {
  const [watchlists, setWatchlists] = useState<WatchlistFolder[]>(DEFAULT_WATCHLISTS);
  const [activeId, setActiveId] = useState(DEFAULT_WATCHLISTS[0].id);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<WatchlistStock[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [nameDraft, setNameDraft] = useState(DEFAULT_WATCHLISTS[0].name);
  const [message, setMessage] = useState("");
  const [quoteByTicker, setQuoteByTicker] = useState<Record<string, { price: number | null; changePct: number | null; change: number | null }>>({});
  const [quotesUpdatedAt, setQuotesUpdatedAt] = useState<number | null>(null);
  const [afterHours, setAfterHours] = useState<{tradeDate: string; marketTradeDate: string; stocks: WatchlistStock[]} | null>(null);
  const [afterHoursError, setAfterHoursError] = useState("");
  const watchlistGridRef = useRef<HTMLDivElement>(null);
  const [changeSort, setChangeSort] = useState<"asc" | "desc" | null>(null);
  // The after-hours folder intentionally keeps Friday's 14:30 list until the
  // next close, but its live quote/force columns must follow today's session.
  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        const response = await fetch("/api/watchlists/after-hours", {cache: "no-store", signal: controller.signal});
        const payload = await response.json();
        if (!response.ok || !payload.ok || !Array.isArray(payload.stocks)) throw new Error("after-hours-unavailable");
        if (!controller.signal.aborted) { setAfterHours(payload); setAfterHoursError(typeof payload.message === "string" ? payload.message : ""); }
      } catch {
        if (!controller.signal.aborted) setAfterHoursError("盤後名單更新中斷，稍後自動重試");
      } finally { loading = false; }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => { if (document.visibilityState === "visible") void load(); }, 15_000);
    window.addEventListener("focus", load);
    return () => {controller.abort(); timer.cancel(); window.removeEventListener("focus", load);};
  }, []);
  const [syncStatus, setSyncStatus] = useState<WatchlistSyncStatus>(() => {
    if (typeof window !== "undefined") {
      const current = (window as Window & { __HANSTOCK_WATCHLIST_SYNC_STATUS__?: WatchlistSyncStatus })
        .__HANSTOCK_WATCHLIST_SYNC_STATUS__;
      if (current) return current;
    }
    return {
      state: "connecting",
      label: "正在連接雲端",
      detail: "任一裝置都能建立第一次同步，之後所有裝置共用同一份名單。",
    };
  });

  useEffect(() => {
    const updateStatus = (event: Event) => {
      const next = (event as CustomEvent<WatchlistSyncStatus>).detail;
      if (next) setSyncStatus(next);
    };
    window.addEventListener("hanstock-watchlists-sync-status", updateStatus);
    return () => window.removeEventListener("hanstock-watchlists-sync-status", updateStatus);
  }, []);

  useEffect(() => {
    const restoreWatchlists = () => {
      try {
        const saved = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
        if (!saved) return;
        setWatchlists(normalizeWatchlists(JSON.parse(saved)));
      } catch {
        // Corrupted device-local data falls back to the six default watchlists.
      }
    };
    const syncFromStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY) restoreWatchlists();
    };
    restoreWatchlists();
    window.addEventListener("pageshow", restoreWatchlists);
    window.addEventListener("storage", syncFromStorage);
    window.addEventListener("hanstock-watchlists-changed", restoreWatchlists);
    return () => {
      window.removeEventListener("pageshow", restoreWatchlists);
      window.removeEventListener("storage", syncFromStorage);
      window.removeEventListener("hanstock-watchlists-changed", restoreWatchlists);
    };
  }, []);

  const displayedWatchlists = useMemo(() => watchlists.map(folder => folder.id === AFTER_HOURS_WATCHLIST_ID
    ? {...folder, stocks: visibleAfterHoursStocks(folder, afterHours?.stocks ?? folder.stocks)} : folder), [watchlists, afterHours]);
  const activeList = displayedWatchlists.find((folder) => folder.id === activeId) ?? displayedWatchlists[0];
  const afterHoursAutomaticList = activeId === AFTER_HOURS_WATCHLIST_ID;
  const automaticList = afterHoursAutomaticList;
  const activeTickerKey = useMemo(() => activeList.stocks.map((stock) => stock.ticker).join(","), [activeList.stocks]);
  const details = useWatchlistDetails(activeTickerKey);
  const sortedStocks = useMemo(() => {
    if (!changeSort) return activeList.stocks;
    const value = (stock: WatchlistStock) => {
      const text = formatWatchlistChange(quoteByTicker[stock.ticker]?.changePct, stock.change);
      const number = Number.parseFloat(text.replace(/,/g, ""));
      return Number.isFinite(number) ? number : null;
    };
    return [...activeList.stocks].sort((a, b) => {
      const av = value(a), bv = value(b);
      if (av === null) return bv === null ? 0 : 1;
      if (bv === null) return -1;
      return changeSort === "desc" ? bv - av : av - bv;
    });
  }, [activeList.stocks, quoteByTicker, changeSort]);


  useEffect(() => {
    if (!activeTickerKey) return;
    const controller = new AbortController();
    let loading = false;
    const tickers = activeTickerKey.split(",").filter(Boolean);
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        const batches = Array.from({ length: Math.ceil(tickers.length / 50) }, (_, index) => tickers.slice(index * 50, index * 50 + 50));
        const settled = await Promise.allSettled(batches.map(async (batch) => {
          const items = batch.map((ticker) => `${stockRankExchange(ticker)}:${ticker}`).join(",");
          const response = await fetch(`/api/quotes?items=${encodeURIComponent(items)}&refresh=${Date.now()}&preferOfficialLive=1`, {
            cache: "no-store",
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8_000)]),
            headers: { Accept: "application/json" },
          });
          if (!response.ok) throw new Error(`watchlist-quotes-${response.status}`);
          return response.json() as Promise<{ quotes?: Array<{ code?: string; price?: number | null; changePct?: number | null; change?: number | null }> }>;
        }));
        if (controller.signal.aborted) return;
        const quotes = settled.flatMap((result) => result.status === "fulfilled" && Array.isArray(result.value.quotes) ? result.value.quotes : []);
        if (quotes.length === 0) return;
        setQuoteByTicker((current) => ({
          ...current,
          ...Object.fromEntries(quotes.flatMap((quote) => {
            const ticker = String(quote.code ?? "").trim().toUpperCase();
            if (!ticker) return [];
            return [[ticker, {
              price: typeof quote.price === "number" && Number.isFinite(quote.price) ? quote.price : null,
              changePct: typeof quote.changePct === "number" && Number.isFinite(quote.changePct) ? quote.changePct : null,
              change: typeof quote.change === "number" && Number.isFinite(quote.change) ? quote.change : null,
            }]];
          })),
        }));
        setQuotesUpdatedAt(Date.now());
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          // Keep the most recent successful quote while the next three-second poll reconnects.
        }
      } finally {
        loading = false;
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 3_000);
    window.addEventListener("focus", onVisible);
    return () => {
      controller.abort();
      timer.cancel();
      window.removeEventListener("focus", onVisible);
    };
  }, [activeTickerKey]);

  useEffect(() => {
    setNameDraft(activeList.name);
    setQuery("");
    setSearchResults([]);
    setMessage("");
  }, [activeId, activeList.name]);

  useEffect(() => {
    const keyword = query.trim();
    if (!keyword) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setIsSearching(true);
      fetch(`/api/stock-search?q=${encodeURIComponent(keyword)}`, {
        cache: "no-store",
        signal: controller.signal,
        headers: { Accept: "application/json" },
      })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("search-failed")))
        .then((payload: { stocks?: WatchlistStock[] }) => setSearchResults(Array.isArray(payload.stocks) ? payload.stocks : []))
        .catch((error: Error) => {
          if (error.name !== "AbortError") setSearchResults([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setIsSearching(false);
        });
    }, 180);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const commitWatchlists = (
    updater: (current: WatchlistFolder[]) => WatchlistFolder[],
    operations: WatchlistOperation[],
  ) => {
    const next = updater(watchlists);
    setWatchlists(next);
    try {
      window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next));
      window.dispatchEvent(new CustomEvent("hanstock-watchlists-local-changed", { detail: { operations } }));
    } catch {
      // The UI still works for the current session when storage is unavailable.
    }
  };

  const addStock = (stock: WatchlistStock) => {
    if (automaticList) return;
    if (activeList.stocks.some((item) => item.ticker === stock.ticker)) {
      setMessage(`${stock.ticker} ${stock.name} 已經在「${activeList.name}」`);
      return;
    }
    if (activeList.stocks.length >= 300) {
      setMessage(`「${activeList.name}」已達 300 檔，請先移除股票或改用其他名單`);
      return;
    }
    commitWatchlists((current) => current.map((folder) => folder.id === activeId
      ? { ...folder, stocks: [...folder.stocks, stock] }
      : folder), [{ type: "addStock", folderId: activeId, stock }]);
    setQuery("");
    setSearchResults([]);
    setMessage(`已加入 ${stock.ticker} ${stock.name}`);
  };

  const removeStock = (stock: WatchlistStock) => {
    const operation: WatchlistOperation = {type: "removeStock", folderId: activeId, ticker: stock.ticker,
      ...(automaticList ? {tradeDate: stock.signalTradeDate} : {})};
    if (automaticList && !stock.signalTradeDate) { setMessage("交易日期讀取中，請稍後重試"); return; }
    commitWatchlists(current => applyWatchlistOperations(current, [operation]), [operation]);
    setMessage(`已從「${activeList.name}」移除 ${stock.ticker} ${stock.name}`);
  };

  const moveStock = (stock: WatchlistStock, targetFolderId: string) => {
    const target = watchlists.find(folder => folder.id === targetFolderId);
    if (!target || target.id === activeId || target.id === AFTER_HOURS_WATCHLIST_ID) return false;
    if (target.stocks.length >= 300 && !target.stocks.some(item => item.ticker === stock.ticker)) {
      setMessage(`「${target.name}」已達 300 檔，股票仍保留在「${activeList.name}」`);
      return false;
    }
    if (afterHoursAutomaticList && !stock.signalTradeDate) { setMessage("交易日期讀取中，請稍後重試"); return false; }
    const operation: WatchlistOperation = {type: "moveStock", folderId: activeId, targetFolderId, ticker: stock.ticker,
      ...(afterHoursAutomaticList ? {stock, tradeDate: stock.signalTradeDate} : {})};
    commitWatchlists(current => applyWatchlistOperations(current, [operation]), [operation]);
    setMessage(`已將 ${stock.ticker} ${stock.name} 從「${activeList.name}」移至「${target.name}」`);
    return true;
  };

  const saveListName = () => {
    if (automaticList) return;
    const nextName = nameDraft.trim().slice(0, 16);
    if (!nextName) {
      setMessage("自選股名稱不能空白");
      return;
    }
    commitWatchlists(
      (current) => current.map((folder) => folder.id === activeId ? { ...folder, name: nextName } : folder),
      [{ type: "renameFolder", folderId: activeId, name: nextName }],
    );
    setMessage(`名稱已改為「${nextName}」`);
  };

  const submitSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const keyword = query.trim().toLowerCase();
    const exact = searchResults.find((stock) => stock.ticker.toLowerCase() === keyword || stock.name.toLowerCase() === keyword);
    const candidate = exact ?? searchResults[0];
    if (candidate) addStock(candidate);
    else setMessage(isSearching ? "正在搜尋，請稍候" : "找不到這個股票代號或名稱");
  };

  return (
    <section className="watchlist-console" aria-label="七組自選股管理">
      <header className="watchlist-head">
        <div>
          <span className="eyebrow">MY WATCHLISTS</span>
          <h2>我的自選股</h2>
          <p>六組自選清單可自由加入、移動與管理股票；第7組每日自動收錄14:30盤後成交股票。</p>
        </div>
        <div className="watchlist-head-status">
          <strong>{activeList.stocks.length} 檔</strong>
          <span className={`watchlist-sync-badge ${syncStatus.state}`}>{syncStatus.label}</span>
          <small>{syncStatus.detail}</small>
          {syncStatus.updatedAt && <time dateTime={new Date(syncStatus.updatedAt).toISOString()}>
            最近同步 {new Date(syncStatus.updatedAt).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </time>}
          <time className="watchlist-quote-updated" dateTime={quotesUpdatedAt ? new Date(quotesUpdatedAt).toISOString() : undefined}>
            {quotesUpdatedAt ? `行情更新 ${new Date(quotesUpdatedAt).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : activeList.stocks.length > 0 ? "行情連線中…" : ""}
          </time>
          {syncStatus.state === "auth-required" && (
            <a href="/signin-with-chatgpt?return_to=%2F%3Fsection%3Dwatchlist">用 ChatGPT 帳號登入同步</a>
          )}
        </div>
      </header>

      <div className="watchlist-tabs" role="tablist" aria-label="七組自選股">
        {displayedWatchlists.map((folder, index) => (
          <button
            key={folder.id}
            className={folder.id === activeId ? "active" : ""}
            onClick={() => setActiveId(folder.id)}
            role="tab"
            aria-selected={folder.id === activeId}
          >
            <span>{index + 1}</span><strong>{folder.name}</strong><small>{folder.stocks.length} 檔</small>
          </button>
        ))}
      </div>

      {automaticList ? <div className="watchlist-auto-status" role="status"><strong>{`每日自動匯入 · ${afterHours?.tradeDate ?? "正在讀取交易日"}`}</strong><span>14:30 成交訊號逐檔去重；保留至下一交易日 14:30 更新。</span>{afterHoursError && <span>{afterHoursError}</span>}</div> : <div className="watchlist-tools">
        <form className="watchlist-search" onSubmit={submitSearch}>
          <label htmlFor="watchlist-stock-search">新增股票</label>
          <div><input id="watchlist-stock-search" value={query} onChange={(event) => { setQuery(event.target.value); setMessage(""); }} placeholder="輸入代號或名稱，例如 2330、台積電" autoComplete="off" /><button type="submit">加入</button></div>
          {query.trim() && (
            <div className="watchlist-search-results" aria-label="股票搜尋結果">
              {isSearching ? <p>搜尋中…</p> : searchResults.length > 0 ? searchResults.map((stock) => (
                <button type="button" key={stock.ticker} onClick={() => addStock(stock)}>
                  <span><b>{stock.ticker}</b><strong>{stock.name}</strong></span><small>{stock.group}</small><i>＋</i>
                </button>
              )) : <p>找不到符合的股票</p>}
            </div>
          )}
        </form>

        <form className="watchlist-rename" onSubmit={(event) => { event.preventDefault(); saveListName(); }}>
          <label htmlFor="watchlist-name">更改目前名單名稱</label>
          <div><input id="watchlist-name" value={nameDraft} onChange={(event) => setNameDraft(event.target.value)} maxLength={16} /><button type="submit">儲存名稱</button></div>
        </form>
      </div>}

      {message && <div className="watchlist-message" role="status">{message}</div>}

      <div className="watchlist-compact-scroll" ref={watchlistGridRef} role="region" aria-label="自選股即時報價清單" tabIndex={0}>
        <table className="watchlist-compact-table">
          <colgroup>
            <col className="watchlist-col-number" /><col className="watchlist-col-code" /><col className="watchlist-col-name" />
            <col className="watchlist-col-group" /><col className="watchlist-col-change-pct" />
            <col className="watchlist-col-change" /><col className="watchlist-col-price" /><col className="watchlist-col-actions" />
          </colgroup>
          <thead><tr><th scope="col">序</th><th scope="col">代號</th><th scope="col">名稱</th><th scope="col">族群</th><th scope="col" aria-sort={changeSort === "desc" ? "descending" : changeSort === "asc" ? "ascending" : "none"}><button type="button" className="watchlist-sort-button" onClick={() => setChangeSort(current => current === "desc" ? "asc" : "desc")} aria-label={`漲跌幅：點擊${changeSort === "desc" ? "升冪" : "降冪"}排序`}>漲跌幅 {changeSort === "desc" ? "▼" : changeSort === "asc" ? "▲" : "↕"}</button></th><th scope="col">漲跌</th><th scope="col">成交價</th><th scope="col">管理</th></tr></thead>
          <tbody>
          {sortedStocks.map((stock, index) => {
            const quote = quoteByTicker[stock.ticker];
            const change = formatWatchlistChange(quote?.changePct, stock.change);
            const price = formatWatchlistPrice(quote?.price, stock.price);
            const quoteTone = watchlistQuoteTone(quote?.change, change);
            const changeAmount = quote?.change == null ? "—" : `${quote.change > 0 ? "+" : ""}${quote.change.toFixed(2)}`;
            const meta = details.metadata[stock.ticker];
            const stockName = meta?.name || stock.name;
            const group = meta?.group && meta.group !== "未分類" ? meta.group : stock.group;
            return <tr key={stock.ticker} data-watchlist-ticker={stock.ticker} data-watchlist-index={index}>
              <td className="watchlist-number">{String(index + 1).padStart(2, "0")}</td>
              <td className="watchlist-code">{stock.ticker}</td>
              <th scope="row">{stockName}</th>
              <td>{group || "待補"}</td>
              <td className={quoteTone}><b>{change}</b></td>
              <td className={quoteTone}>{changeAmount}</td>
              <td className={quoteTone}><b>{price}</b></td>
              <td><WatchlistStockEditor key={`${activeId}-${stock.ticker}`} stock={stock} folderId={activeId}
                folders={displayedWatchlists} onMove={moveStock} onRemove={removeStock} /></td>
            </tr>;
          })}
          </tbody>
        </table>
        {activeList.stocks.length === 0 && <div className="watchlist-empty"><strong>{afterHoursAutomaticList ? afterHoursError ? "盤後成交名單確認中" : "尚無符合的 14:30 盤後成交訊號" : "這組自選股目前是空的"}</strong><span>{afterHoursAutomaticList ? afterHoursError || "系統會自動更新，無需手動加入。" : "請在上方輸入股票代號或名稱加入"}</span></div>}
      </div>
    </section>
  );
}

function DaytradeBrokerPanel({ thresholds }: { thresholds: BattleRuntimeSettings["daytradeThresholds"] }) {
  const [payload, setPayload] = useState<DaytradeBrokerPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const requestRef = useRef<AbortController | null>(null);
  const [category, setCategory] = useState<DaytradeBrokerRow["category"]>("漲停鎖定");
  const [viewMode, setViewMode] = useState<DaytradeViewMode>("selected");

  const load = async (force = false) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true);
    try {
      const response = await fetch(`/api/daytrade-brokers${force ? "?force=1" : ""}`, { cache: "no-store", signal: controller.signal });
      setPayload(await response.json() as DaytradeBrokerPayload);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setPayload({ ok: false, message: "疑似隔日沖大單資料暫時無法取得" });
    } finally {
      if (requestRef.current === controller) setLoading(false);
    }
  };

  useEffect(() => {
    void load(false);
    const timer = createVisibilityGatedInterval(() => void load(false), 60_000);
    return () => { timer.cancel(); requestRef.current?.abort(); };
  }, []);

  const allRows = payload?.rows ?? [];
  const categoryRows = allRows.filter((row) => row.category === category);
  const selectedRows = categoryRows.filter((row) => isSelectedDaytradeRow(row, thresholds));
  const rows = viewMode === "selected" ? selectedRows : categoryRows;
  const categoryCounts = allRows.reduce<Record<string, number>>((counts, row) => ({ ...counts, [row.category]: (counts[row.category] ?? 0) + 1 }), {});
  const selectedCategoryCounts = allRows.filter((row) => isSelectedDaytradeRow(row, thresholds)).reduce<Record<string, number>>((counts, row) => ({ ...counts, [row.category]: (counts[row.category] ?? 0) + 1 }), {});
  const progress = payload?.requestedCount ? `${payload.processedCount ?? 0}/${payload.requestedCount}` : "準備中";
  const pendingRows = allRows.filter((row) => !row.mainForceDataAvailable).length;
  const pendingCount = Math.max(payload?.dataMissingCount ?? 0, pendingRows);
  return (
    <section className="daytrade-console" aria-label="疑似隔日沖大單籌碼">
      <header className="daytrade-head">
        <div><span className="eyebrow">SUSPECTED DAY-TRADE FLOW</span><h2>疑似隔日沖大單籌碼</h2><p>依 Shioaji 逐筆成交的大單方向與次一交易日賣出行為推估；不含券商分點身分。</p></div>
        <div><span>資料日 {payload?.dataDate ?? "—"}</span><small>全市場掃描 {progress}｜更新於 {formatTaipeiDateTime(payload?.updatedAt)}</small><button type="button" onClick={() => void load(true)} disabled={loading}>{loading ? "更新中" : "立即更新"}</button></div>
      </header>
      <div className="daytrade-formula">大單淨額資金占比＝大單淨額 ÷ 個股當日成交金額｜疑似隔日沖資金占比＝主動大單買進金額 ÷ 個股當日成交金額｜盤中訊號＝隔一交易日 09:00～13:30（含 13:30）逐分鐘判定，累計大單買進或賣出達前一日預估隔日賣壓 50% 就立即通知；買進紅色、賣出綠色；停券、禁止現股當沖及處置股不列入</div>
      {pendingCount > 0 && <div className="daytrade-data-status" role="status"><strong>{pendingCount} 檔大單資料待回補</strong><span>成交金額與漲停資料已更新；大單買進、淨額、資金占比、尾盤集中與疑似分數尚未取得，畫面以「待回補」標示。系統每 30 分鐘自動重試，也可按「立即更新」強制重掃。</span></div>}
      <div className="daytrade-level-tabs" aria-label="疑似隔日沖三層名單">
        {(["漲停鎖定", "曾達漲停", "強勢大單"] as const).map((item) => <button type="button" key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}><strong>{item}</strong><span>精選 {selectedCategoryCounts[item] ?? 0}／全部 {categoryCounts[item] ?? 0}</span></button>)}
      </div>
      <div className="daytrade-view-controls" aria-label="隔日沖名單顯示範圍">
        <div className="daytrade-view-toggle" role="group" aria-label="精選名單或查看全部">
          <button type="button" className={viewMode === "selected" ? "active" : ""} onClick={() => setViewMode("selected")}>精選名單</button>
          <button type="button" className={viewMode === "all" ? "active" : ""} onClick={() => setViewMode("all")}>查看全部</button>
        </div>
        <p>精選門檻：成交金額 2 億、淨大單 5,000 萬、淨買占比 5%、疑似分數 60；強勢大單另須漲幅 2%、尾盤集中 5%。完整名單仍永久保存。</p>
        <strong>{category}：目前顯示 {rows.length}／全部 {categoryRows.length} 檔</strong>
      </div>
      <div className="daytrade-table-scroll" role="region" aria-label="全市場疑似隔日沖排行" tabIndex={0}>
        <div className="daytrade-table">
          <div className="daytrade-row daytrade-table-head"><span>排行</span><span>代號</span><span>名稱</span><span>漲跌幅</span><span>漲跌</span><span>層級</span><span>市場</span><span>收盤／漲停</span><span>大單買進</span><span>大單淨額</span><span>成交金額</span><span>大單淨額資金占比</span><span>資金占比</span><span>尾盤集中</span><span>疑似分數</span><span>預估隔日賣壓</span><span>隔日回沖率</span><span>近5日趨勢</span></div>
          {rows.map((row, index) => (
            <div className="daytrade-row" key={row.ticker}>
              <span>{index + 1}</span><button onClick={() => openKlineByTicker(row.ticker, row.name)}>{row.ticker}</button><button onClick={() => openKlineByTicker(row.ticker, row.name)}><span>{row.name} ›</span><StockTradingBadges ticker={row.ticker} compact /></button><b className={row.dayChangePct < 0 ? "negative" : row.dayChangePct > 0 ? "positive" : "neutral"}>{formatSigned(row.dayChangePct, 2, "%")}</b><b className={row.dayChange < 0 ? "negative" : row.dayChange > 0 ? "positive" : "neutral"}>{formatSigned(row.dayChange, 2)}</b><strong>{row.category}</strong><span>{row.market}</span><span>{row.closePrice.toFixed(2)}／{row.limitUpPrice.toFixed(2)}</span><strong>{row.mainForceDataAvailable ? formatTwd(row.largeBuyAmount) : "待回補"}</strong><strong>{row.mainForceDataAvailable ? formatTwd(row.netLargeAmount) : "待回補"}</strong><span>{formatTwd(row.turnoverAmount)}</span><span className={`daytrade-net-funding-ratio ${row.netLargeAmount < 0 ? "negative" : row.netLargeAmount > 0 ? "positive" : "neutral"}`}>{row.mainForceDataAvailable ? `${daytradeNetFundingRate(row).toFixed(2)}%` : "待回補"}</span><b>{row.mainForceDataAvailable ? `${row.participationRate.toFixed(2)}%` : "待回補"}</b><span>{row.mainForceDataAvailable ? `${row.lateBuyConcentration.toFixed(1)}%` : "待回補"}</span><strong>{row.mainForceDataAvailable ? `${row.suspicionScore.toFixed(0)}｜${row.suspicionLabel}` : "待回補"}</strong><strong>{row.mainForceDataAvailable ? formatTwd(row.estimatedNextDaySellAmount) : "待回補"}</strong><span>{row.mainForceDataAvailable ? (row.confirmedReversalRate === null ? "待隔日驗證" : `${row.confirmedReversalRate.toFixed(1)}%`) : "待回補"}</span><span>{row.fiveDayRates.length ? row.fiveDayRates.map((rate) => `${rate.toFixed(1)}%`).join(" → ") : "—"}</span>
            </div>
          ))}
          {!loading && rows.length === 0 && <div className="daytrade-empty"><strong>{viewMode === "selected" && categoryRows.length ? `${category}目前沒有符合精選門檻的股票` : `${category}目前尚無已完成資料`}</strong><span>{viewMode === "selected" && categoryRows.length ? "可切換「查看全部」檢視已永久保存的完整候選名單。" : payload?.message ?? `全市場掃描 ${progress}；符合條件時會分批出現並永久保存。`}</span></div>}
          {loading && <div className="daytrade-empty"><strong>正在讀取疑似隔日沖大單資料…</strong></div>}
        </div>
      </div>
      <footer>每天台灣時間 00:05 後永久保存最近有效交易日；休市與空資料不覆蓋。無券商身分資料，不標示特定券商。</footer>
    </section>
  );
}

function BattleHome() {
  const [direction, setDirection] = useState<Direction>("strong");
  const [rankMode, setRankMode] = useState<RankMode>("stocks");
  const [stockChangeSort, setStockChangeSort] = useState<StockChangeSort>("default");
  const [stockGroupSort, setStockGroupSort] = useState<StockGroupSort>("ranking");
  const [bottomMode, setBottomMode] = useState<BottomMode>("ranking");
  const [chipPeriod, setChipPeriod] = useState<ChipPeriod>("previous-day");
  const [chipMarketFilter, setChipMarketFilter] = useState<ChipMarketFilter>("全部");
  const [chipSelectionFilter, setChipSelectionFilter] = useState<ChipSelectionFilter>("all");
  const [chipSort, setChipSort] = useState<ChipSortState>(null);
  const [chipWeights, setChipWeights] = useState<ChipWeights>(DEFAULT_CHIP_WEIGHTS);
  const [chipAutoRefreshMinutes, setChipAutoRefreshMinutes] = useState(CHIP_AUTO_REFRESH_MINUTES);
  const [daytradeThresholds, setDaytradeThresholds] = useState<BattleRuntimeSettings["daytradeThresholds"]>(DEFAULT_BATTLE_SETTINGS.daytradeThresholds);
  const [showChipWeights, setShowChipWeights] = useState(false);
  const [officialChipData, setOfficialChipData] = useState<MarketRankingPayload | null>(null);
  const [weeklyChipData, setWeeklyChipData] = useState<MarketRankingPayload | null>(null);
  const [chipDataStatus, setChipDataStatus] = useState<ChipDataStatus>("idle");
  const [weeklyChipDataStatus, setWeeklyChipDataStatus] = useState<ChipDataStatus>("idle");
  const [chipRefreshTick, setChipRefreshTick] = useState(0);
  const [chipQuotes, setChipQuotes] = useState<Record<string, MarketQuote>>({});
  const [stockRankQuotes, setStockRankQuotes] = useState<StockRankQuoteMap>({});
  const [rankingTechnicalMeta, setRankingTechnicalMeta] = useState<Record<string, IntradaySignalTechnicalMeta>>({});
  const [stockAnalysisQuery, setStockAnalysisQuery] = useState("");
  const [stockResearchTab, setStockResearchTab] = useState<StockResearchTab>("fundamental");
  const [stockAnalysisQuote, setStockAnalysisQuote] = useState<MarketQuote | null>(null);
  const [weeklyMainForceSnapshot, setWeeklyMainForceSnapshot] = useState<{ ticker: string; weekEndDate: string; score: number } | null>(null);
  const [weeklyMainForceTableHistory, setWeeklyMainForceTableHistory] = useState<WeeklyMainForceHistoryRow[]>([]);
  const [weeklyMainForceRows, setWeeklyMainForceRows] = useState<WeeklyMainForceLatestRow[]>([]);
  const [brokerBranchDailyRows, setBrokerBranchDailyRows] = useState<BrokerBranchDailyRow[]>([]);
  const [groupChipMembers, setGroupChipMembers] = useState<GroupChipMemberPayload | null>(null);
  const [liveRankings, setLiveRankings] = useState<Partial<Record<RankMode, Partial<Record<Direction, LiveRankingRow[]>>>>>({});
  const [rankingUpdatedAt, setRankingUpdatedAt] = useState<string | null>(null);
  const [rankingSourceDate, setRankingSourceDate] = useState<string | null>(null);
  const [rankingLiveData, setRankingLiveData] = useState(false);
  const [isDesktop, setIsDesktop] = useState(false);
  const [liveFocusGroups, setLiveFocusGroups] = useState<Partial<Record<Direction, FocusGroup[]>>>({});
  const [liveFocusSummaries, setLiveFocusSummaries] = useState<Partial<Record<Direction, FocusSummary>>>({});
  const [liveFocusPriceTypes, setLiveFocusPriceTypes] = useState<Partial<Record<Direction, string>>>({});
  const [focusSync, setFocusSync] = useState({ polledAt: 0, error: false });
  const [time, setTime] = useState("10:32:18");
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [loadedGroupStocks, setLoadedGroupStocks] = useState<GroupStock[] | null>(null);
  const [loadedGroupAverageChange, setLoadedGroupAverageChange] = useState<string | null>(null);
  const [loadedGroupRetrievedAt, setLoadedGroupRetrievedAt] = useState<string | null>(null);
  const [loadedGroupPriceType, setLoadedGroupPriceType] = useState<string | null>(null);
  const [groupStocksLoading, setGroupStocksLoading] = useState(false);
  const [groupMemberMetaByTicker, setGroupMemberMetaByTicker] = useState<Record<string, GroupMemberMeta>>({});
  const [groupMemberMetaLoading, setGroupMemberMetaLoading] = useState(false);
  const [groupMemberFlowStatus, setGroupMemberFlowStatus] = useState<{ dataDate: string | null; requested: number; completed: number } | null>(null);
  const groupStockCacheRef = useRef(new Map<string, { stocks: GroupStock[]; averageChange: string | null; priceType: string | null; retrievedAt: string | null }>());
  const [fullGroupRankings, setFullGroupRankings] = useState<MainForceGroupRankings | undefined>(undefined);

  const selectBottomMode = (mode: BottomMode) => {
    setBottomMode(mode);
    const url = new URL(window.location.href);
    url.searchParams.set("section", mode);
    window.history.replaceState(null, "", url);
  };

  const openIntradayTracker = () => {
    selectBottomMode("intraday-tracking");
    window.setTimeout(() => {
      const tracker = document.getElementById("intraday-stock-tracking");
      tracker?.scrollIntoView({ behavior: "smooth", block: "start" });
      tracker?.focus({ preventScroll: true });
    }, 80);
  };

  useEffect(() => {
    const section = new URLSearchParams(window.location.search).get("section");
    if (section === "ranking" || section === "watchlist" || section === "chips" || section === "weekly-chips" || section === "daytrade" || section === "etf-holdings" || section === "stock-analysis" || section === "intraday-tracking" || section === "revenue-records") {
      setBottomMode(section);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/runtime-settings", { cache: "no-store", signal: controller.signal })
      .then((response) => response.json())
      .then((payload: { settings?: BattleRuntimeSettings }) => {
        if (!payload.settings) return;
        setChipWeights(payload.settings.chipWeights);
        setChipAutoRefreshMinutes(payload.settings.chipAutoRefreshMinutes);
        if (payload.settings.daytradeThresholds) setDaytradeThresholds(payload.settings.daytradeThresholds);
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          // 保留程式內建建議值，前台仍可正常使用。
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(FOCUS_SNAPSHOT_KEY) ?? "null") as Partial<Record<Direction, FocusSnapshot>> | null;
      if (!saved) return;
      const groups = Object.fromEntries(((["strong", "weak"] as Direction[])
        .filter((side) => Array.isArray(saved[side]?.groups) && saved[side]?.groups?.length === 6)
        .map((side) => [side, saved[side]?.groups] as const)));
      const summaries = Object.fromEntries(((["strong", "weak"] as Direction[])
        .filter((side) => Boolean(saved[side]?.summary))
        .map((side) => [side, saved[side]?.summary] as const)));
      const priceTypes = Object.fromEntries(((["strong", "weak"] as Direction[])
        .filter((side) => Boolean(saved[side]?.priceType))
        .map((side) => [side, saved[side]?.priceType] as const)));
      if (Object.keys(groups).length) setLiveFocusGroups(groups);
      if (Object.keys(summaries).length) setLiveFocusSummaries(summaries);
      if (Object.keys(priceTypes).length) setLiveFocusPriceTypes(priceTypes);
    } catch {
      // Ignore an invalid old browser snapshot and wait for the next valid response.
    }
  }, []);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(LIVE_RANKING_SNAPSHOT_KEY) ?? "null") as LiveRankingPayload | null;
      if (!saved?.rankings) return;
      setLiveRankings(saved.rankings);
      if (saved.groupRankings?.strong?.length || saved.groupRankings?.weak?.length) setFullGroupRankings(saved.groupRankings);
      setStockRankQuotes(Object.fromEntries((saved.quotes ?? []).map((quote) => [quote.key, quote])));
      setRankingUpdatedAt(saved.fetchedAt ?? null);
      setRankingSourceDate(saved.sourceDate ?? null);
      setRankingLiveData(Boolean(saved.liveData));
    } catch {
      // Ignore an invalid old browser snapshot and wait for the next valid response.
    }
  }, []);

  useEffect(() => {
    const isIPhone = /iPhone|iPod/i.test(navigator.userAgent);
    const isIPad = /iPad/i.test(navigator.userAgent)
      || (/Macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
    if (isIPhone) document.documentElement.dataset.hanstockDevice = "iphone";
    else if (isIPad) document.documentElement.dataset.hanstockDevice = "ipad";
    const desktopQuery = window.matchMedia("(min-width: 901px) and (pointer: fine)");
    const updateDesktop = () => setIsDesktop(!isIPhone && !isIPad && desktopQuery.matches);
    updateDesktop();
    desktopQuery.addEventListener("change", updateDesktop);
    return () => {
      desktopQuery.removeEventListener("change", updateDesktop);
      if (document.documentElement.dataset.hanstockDevice === "iphone" || document.documentElement.dataset.hanstockDevice === "ipad") {
        delete document.documentElement.dataset.hanstockDevice;
      }
    };
  }, []);

  useEffect(() => {
    const update = () => setTime(new Intl.DateTimeFormat("zh-TW", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, hourCycle: "h23", timeZone: "Asia/Taipei" }).format(new Date()));
    update();
    const timer = createVisibilityGatedInterval(update, 1000);
    return () => timer.cancel();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      if (shouldHoldPreviousTradingSnapshot() && hasStoredSnapshot(FOCUS_SNAPSHOT_KEY)) return;
      loading = true;
      try {
        // 強、弱共用同一份全市場盤前快照，避免兩次全市場請求互相拖垮。
        const response = await fetch(`/api/focus-ranking?direction=both&refresh=${Date.now()}`, {
          cache: "no-store",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25_000)]),
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("focus-ranking-both-failed");
        const payload = await response.json() as FocusRankingPayload;
        if (controller.signal.aborted) return;
        const entries = (["strong", "weak"] as Direction[]).map((side) => [side, payload.rankings?.[side]] as const);
        if (!payload.ok || entries.some(([, ranked]) => !Array.isArray(ranked?.groups) || ranked.groups.length !== 6 || !ranked.summary)) {
          throw new Error("focus-ranking-both-incomplete");
        }
        setLiveFocusGroups((current) => ({
          ...current,
          ...Object.fromEntries(entries.map(([side, ranked]) => [side, ranked?.groups])),
        }));
        setLiveFocusSummaries((current) => ({
          ...current,
          ...Object.fromEntries(entries.map(([side, ranked]) => [side, ranked?.summary])),
        }));
        setLiveFocusPriceTypes((current) => ({
          ...current,
          strong: payload.priceType ?? current.strong ?? "",
          weak: payload.priceType ?? current.weak ?? "",
        }));
        setFocusSync({ polledAt: Date.now(), error: false });
        try {
          const saved = JSON.parse(window.localStorage.getItem(FOCUS_SNAPSHOT_KEY) ?? "{}") as Partial<Record<Direction, FocusSnapshot>>;
          for (const [side, ranked] of entries) {
            saved[side] = {
              ...saved[side],
              groups: ranked?.groups,
              summary: ranked?.summary,
              updatedAt: payload.updatedAt,
              sourceDate: payload.sourceDate,
              priceType: payload.priceType,
            };
          }
          window.localStorage.setItem(FOCUS_SNAPSHOT_KEY, JSON.stringify(saved));
        } catch {
          // Storage can be unavailable in private browsing; live state still works.
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          if (!controller.signal.aborted) setFocusSync(current => ({ ...current, error: true }));
          console.warn("[focus-ranking] 即時六大族群更新失敗，暫留最近成功資料");
        }
      } finally {
        loading = false;
      }
    };
    void load();
    let intervalMs = isPreopenTrialClientWindow() ? 5_000 : 30_000;
    const poll = () => {
      void load();
      // Re-evaluate on every visible tick, including resume, so an open page
      // switches back to 30 seconds after the preopen trial window ends.
      const nextIntervalMs = isPreopenTrialClientWindow() ? 5_000 : 30_000;
      if (nextIntervalMs !== intervalMs) {
        intervalMs = nextIntervalMs;
        timer.cancel();
        timer = createVisibilityGatedInterval(poll, intervalMs);
      }
    };
    let timer = createVisibilityGatedInterval(poll, intervalMs);
    return () => {
      controller.abort();
      timer.cancel();
    };
  }, []);

  useEffect(() => {
    if (!selectedGroup) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedGroup(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selectedGroup]);

  const selectedGroupMemberTickers = selectedGroup
    ? groupChipMembers?.groups.find((group) => group.name === selectedGroup)?.codes
      ?? loadedGroupStocks?.map((stock) => stock.ticker)
      ?? []
    : [];
  const selectedGroupMemberTickerKey = selectedGroupMemberTickers.join(",");

  useEffect(() => {
    if (!selectedGroup || !selectedGroupMemberTickerKey) {
      setGroupMemberFlowStatus(null);
      return;
    }

    const controller = new AbortController();
    setGroupMemberMetaLoading(true);
    void Promise.allSettled([
      fetch("/api/disposition-risk", { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<GroupMemberMetaPayload> : Promise.reject(new Error("disposition-load-failed"))),
      fetch(`/api/group-member-flow?tickers=${encodeURIComponent(selectedGroupMemberTickerKey)}`, { cache: "no-store", signal: controller.signal }).then((response) => response.ok ? response.json() as Promise<GroupMemberFlowPayload> : Promise.reject(new Error("group-member-flow-load-failed"))),
    ]).then(([dispositionResult, flowResult]) => {
      if (controller.signal.aborted) return;
      const dispositions = dispositionResult.status === "fulfilled" ? dispositionResult.value.dispositions ?? [] : [];
      const flowPayload = flowResult.status === "fulfilled" ? flowResult.value : null;
      const flowRows = flowPayload?.rows ?? [];
      const dispositionByTicker = new Map(dispositions.map((row) => [row.code, row]));
      const flowByTicker = new Map(flowRows.map((row) => [row.ticker, row]));
      const next = Object.fromEntries(selectedGroupMemberTickers.map((ticker) => {
        const disposition = formatGroupDisposition(dispositionByTicker.get(ticker));
        const flow = flowByTicker.get(ticker);
        const funding = formatGroupNetFundingRate(flow?.netLargeAmount ?? undefined, flow?.turnoverAmount ?? undefined, flow?.available);
        const pendingLabel = flow?.status === "turnover_pending" ? "成交額待回補" : "逐筆待回補";
        return [ticker, {
          dispositionLabel: disposition.label,
          dispositionTone: disposition.tone,
          isActiveDisposition: disposition.isActive,
          netFundingLabel: funding.value === null ? pendingLabel : funding.label,
          netFundingValue: funding.value,
          netFundingDataDate: flow?.dataDate ?? null,
          netFundingStatus: flow?.status ?? "force_pending",
        } satisfies GroupMemberMeta];
      }));
      setGroupMemberFlowStatus({
        dataDate: flowPayload?.dataDate ?? null,
        requested: flowPayload?.requestedCount ?? selectedGroupMemberTickers.length,
        completed: flowPayload?.completedCount ?? flowRows.filter((row) => row.available).length,
      });
      setGroupMemberMetaByTicker(next);
    }).finally(() => {
      if (!controller.signal.aborted) setGroupMemberMetaLoading(false);
    });
    return () => controller.abort();
  }, [selectedGroup, selectedGroupMemberTickerKey]);

  useEffect(() => {
    if (!selectedGroup) {
      setLoadedGroupStocks(null);
      setLoadedGroupAverageChange(null);
      setLoadedGroupPriceType(null);
      setLoadedGroupRetrievedAt(null);
      setGroupStocksLoading(false);
      return;
    }

    const controller = new AbortController();
    let loading = false;
    const cached = groupStockCacheRef.current.get(selectedGroup);
    setLoadedGroupStocks(cached?.stocks ?? null);
    setLoadedGroupAverageChange(cached?.averageChange ?? null);
    setLoadedGroupPriceType(cached?.priceType ?? null);
    setLoadedGroupRetrievedAt(cached?.retrievedAt ?? null);
    setGroupStocksLoading(!cached);
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        const response = await fetch(`/api/group?name=${encodeURIComponent(selectedGroup)}`, {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("group-load-failed");
        const payload = await response.json() as GroupStockPayload;
        if (Array.isArray(payload.stocks) && payload.stocks.length > 0) {
          const entry = {
            stocks: payload.stocks,
            retrievedAt: payload.retrievedAt ?? null,
            averageChange: typeof payload.averageChange === "string" ? payload.averageChange : null,
            priceType: typeof payload.priceType === "string" ? payload.priceType : null,
          };
          groupStockCacheRef.current.set(selectedGroup, entry);
          setLoadedGroupStocks(entry.stocks);
          setLoadedGroupAverageChange(entry.averageChange);
          setLoadedGroupPriceType(entry.priceType);
          setLoadedGroupRetrievedAt(entry.retrievedAt);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
      } finally {
        loading = false;
        if (!controller.signal.aborted) setGroupStocksLoading(false);
      }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 15_000);

    return () => {
      controller.abort();
      timer.cancel();
    };
  }, [selectedGroup]);

  useEffect(() => {
    if (bottomMode !== "ranking" && bottomMode !== "chips" && bottomMode !== "stock-analysis") return;
    const controller = new AbortController();
    let hasUsableData = false;

    const load = async () => {
      await Promise.resolve();
      setChipDataStatus("loading");
      try {
        const snapshotResponse = await fetch("/api/market-ranking", {
          cache: "no-store",
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!snapshotResponse.ok) throw new Error("market-ranking-snapshot-failed");
        const snapshot = await snapshotResponse.json() as MarketRankingPayload;
        if (!snapshot.ok || !Array.isArray(snapshot.rows) || snapshot.rows.length === 0) {
          throw new Error("market-ranking-snapshot-empty");
        }
        hasUsableData = true;
        setOfficialChipData((current) => current && current.dataDate > snapshot.dataDate ? current : snapshot);
        setChipDataStatus(snapshot.snapshotFallback ? "fallback" : "ready");

        const refreshResponse = await fetch(`/api/market-ranking?refresh=${Date.now()}-${chipRefreshTick}`, {
          cache: "no-store",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(50_000)]),
          headers: { Accept: "application/json" },
        });
        if (!refreshResponse.ok) throw new Error("market-ranking-refresh-failed");
        let refreshed = await refreshResponse.json() as MarketRankingPayload;
        if (!refreshed.ok || !Array.isArray(refreshed.rows) || refreshed.rows.length === 0) {
          throw new Error("market-ranking-refresh-empty");
        }
        // Cloudflare／Railway 的機房 IP 偶爾會被櫃買中心擋下；若伺服器只拿到
        // 舊快照，就由使用者瀏覽器直接讀官方 OpenAPI，再送回本站完成同日合併。
        if (refreshed.snapshotFallback) {
          try {
            const browserRefreshed = await refreshMarketRankingWithBrowserOfficialSources(controller.signal);
            if (!browserRefreshed.snapshotFallback || browserRefreshed.dataDate > refreshed.dataDate) {
              refreshed = browserRefreshed;
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") throw error;
          }
        }
        hasUsableData = true;
        setOfficialChipData((current) => current && current.dataDate > refreshed.dataDate ? current : refreshed);
        setChipDataStatus(refreshed.snapshotFallback ? "fallback" : "ready");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!hasUsableData) setChipDataStatus("error");
      }
    };

    void load();
    return () => controller.abort();
  }, [bottomMode, chipRefreshTick]);

  useEffect(() => {
    if (bottomMode !== "chips" && bottomMode !== "stock-analysis") return;
    const refresh = () => setChipRefreshTick((value) => value + 1);
    const timer = createVisibilityGatedInterval(refresh, chipAutoRefreshMinutes * 60 * 1000);
    return () => {
      timer.cancel();
    };
  }, [bottomMode, chipAutoRefreshMinutes]);

  useEffect(() => {
    if (bottomMode !== "weekly-chips" && bottomMode !== "stock-analysis") return;
    const controller = new AbortController();
    let hasUsableData = false;
    const load = async () => {
      await Promise.resolve();
      setWeeklyChipDataStatus("loading");
      try {
        const snapshotResponse = await fetch("/api/market-ranking?weekly=1", {
          cache: "no-store",
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        if (!snapshotResponse.ok) throw new Error("weekly-market-ranking-snapshot-failed");
        const snapshot = await snapshotResponse.json() as MarketRankingPayload;
        if (snapshot.ok && Array.isArray(snapshot.rows) && snapshot.rows.some((row) => row.series.length >= 10)) {
          hasUsableData = true;
          setWeeklyChipData(snapshot);
          setWeeklyChipDataStatus(snapshot.snapshotFallback ? "fallback" : "ready");
        }

        const refreshResponse = await fetch(`/api/market-ranking?weekly=1&refresh=${Date.now()}-${chipRefreshTick}`, {
          cache: "no-store",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(80_000)]),
          headers: { Accept: "application/json" },
        });
        if (!refreshResponse.ok) throw new Error("weekly-market-ranking-refresh-failed");
        let refreshed = await refreshResponse.json() as MarketRankingPayload;
        if (!refreshed.ok || !Array.isArray(refreshed.rows) || refreshed.rows.length === 0) {
          throw new Error("weekly-market-ranking-refresh-empty");
        }
        if (refreshed.snapshotFallback || !refreshed.rows.some((row) => row.series.length >= 10)) {
          try {
            const browserRefreshed = await refreshMarketRankingWithBrowserOfficialSources(controller.signal, 20);
            if (!browserRefreshed.snapshotFallback || browserRefreshed.coverage.tradingDays > refreshed.coverage.tradingDays) {
              refreshed = browserRefreshed;
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") throw error;
          }
        }
        if (refreshed.rows.some((row) => row.series.length >= 10)) {
          hasUsableData = true;
          setWeeklyChipData(refreshed);
          setWeeklyChipDataStatus(refreshed.snapshotFallback ? "fallback" : "ready");
        } else if (!hasUsableData) {
          setWeeklyChipDataStatus("fallback");
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!hasUsableData) setWeeklyChipDataStatus("error");
      }
    };
    void load();
    return () => controller.abort();
  }, [bottomMode, chipRefreshTick]);

  useEffect(() => {
    if (bottomMode !== "weekly-chips" && bottomMode !== "stock-analysis") return;
    const refresh = () => setChipRefreshTick((value) => value + 1);
    const timer = createVisibilityGatedInterval(refresh, chipAutoRefreshMinutes * 60 * 1000);
    return () => {
      timer.cancel();
    };
  }, [bottomMode, chipAutoRefreshMinutes]);

  useEffect(() => {
    if (bottomMode !== "chips") return;
    const controller = new AbortController();
    const load = () => fetch("/api/weekly-main-force-history", {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() as Promise<{ rows?: WeeklyMainForceLatestRow[] }> : null)
      .then((payload) => {
        if (Array.isArray(payload?.rows)) setWeeklyMainForceRows(payload.rows);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") console.warn("[weekly-main-force] 每週主力綜合資料暫時無法讀取");
      });
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 30_000);
    return () => {
      controller.abort();
      timer.cancel();
    };
  }, [bottomMode, chipRefreshTick]);

  useEffect(() => {
    if (bottomMode !== "chips") return;
    const controller = new AbortController();
    try {
      const cached = JSON.parse(window.localStorage.getItem(BROKER_BRANCH_DAILY_SNAPSHOT_KEY) ?? "null") as BrokerBranchDailyRow[] | null;
      if (Array.isArray(cached) && cached.length) {
        queueMicrotask(() => setBrokerBranchDailyRows((current) => current.length ? current : cached));
      }
    } catch {
      // 私密瀏覽可能禁用儲存；仍可繼續讀正式 API。
    }
    const load = () => fetch("/api/broker-branch-daily", {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() as Promise<{ rows?: BrokerBranchDailyRow[] }> : null)
      .then((payload) => {
        if (!Array.isArray(payload?.rows) || payload.rows.length === 0) return;
        setBrokerBranchDailyRows(payload.rows);
        try { window.localStorage.setItem(BROKER_BRANCH_DAILY_SNAPSHOT_KEY, JSON.stringify(payload.rows)); } catch { /* 保留記憶體中的最近成功資料。 */ }
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") console.warn("[broker-branch-daily] 每日分點資料暫時無法讀取，保留最近成功資料");
      });
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 30_000);
    return () => {
      controller.abort();
      timer.cancel();
    };
  }, [bottomMode, chipRefreshTick]);

  const rows = useMemo(() => {
    const liveRows = liveRankings[rankMode]?.[direction];
    if (liveRows?.length === 20) return liveRows;
    // 個股榜不可回退到舊的示範／全市場名單；尚未取得正式 67 族群白名單時寧可暫不顯示。
    if (rankMode === "stocks") return [];
    return direction === "strong" ? strongGroups : weakGroups;
  }, [rankMode, direction, liveRankings]);

  const displayedRows = useMemo(() => {
    if (rankMode !== "stocks") return rows;
    if (stockGroupSort === "group") return groupRankingRowsByLead(rows);
    if (stockChangeSort === "default") return rows;
    return [...rows].sort((a, b) => {
      const tickerA = rankStockTicker(a.name);
      const tickerB = rankStockTicker(b.name);
      const changeA = (stockRankQuotes[tickerA] ?? stockRankQuotes[`${stockRankExchange(tickerA)}:${tickerA}`])?.changePct ?? Number.parseFloat(a.change);
      const changeB = (stockRankQuotes[tickerB] ?? stockRankQuotes[`${stockRankExchange(tickerB)}:${tickerB}`])?.changePct ?? Number.parseFloat(b.change);
      const difference = changeA - changeB;
      return stockChangeSort === "asc" ? difference : -difference;
    });
  }, [rows, rankMode, stockChangeSort, stockGroupSort, stockRankQuotes]);

  const toggleStockChangeSort = () => {
    setStockGroupSort("ranking");
    setStockChangeSort((current) => current === "desc" ? "asc" : "desc");
  };

  const toggleStockGroupSort = () => {
    setStockChangeSort("default");
    setStockGroupSort((current) => current === "group" ? "ranking" : "group");
  };

  const rankingTechnicalTickerKey = useMemo(() => [...new Set((liveRankings.stocks?.[direction] ?? [])
    .map((row) => rankStockTicker(row.name))
    .filter(Boolean))].join(","), [direction, liveRankings.stocks]);

  useEffect(() => {
    if (bottomMode !== "ranking" || !rankingTechnicalTickerKey) return;
    const controller = new AbortController();
    const wanted = new Set(rankingTechnicalTickerKey.split(","));
    Promise.all([
      fetch("/api/technical-market?fast=1", { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("ranking-technical-meta-failed"))),
      fetch("/api/ma-score-ranking?lookback=5", { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("ranking-ma-score-failed")))
        .catch(() => ({ rows: [] })),
    ]).then(([technicalPayload, scorePayload]: [{ rows?: Array<{ code?: string; maScore?: number | null; maCompositeScore?: number | null; riverBase?: number | null }> }, { rows?: Array<{ code?: string; score?: number | null }> }]) => {
      if (controller.signal.aborted) return;
      const compositeByCode = new Map((scorePayload.rows ?? []).flatMap((row) => {
        const code = String(row.code ?? "");
        const score = row.score === null || row.score === undefined ? null : Number(row.score);
        return code && score !== null && Number.isFinite(score) ? [[code, score] as const] : [];
      }));
      const next = Object.fromEntries((technicalPayload.rows ?? []).flatMap((row) => {
        const code = String(row.code ?? "");
        if (!wanted.has(code)) return [];
        const rawScore = compositeByCode.get(code) ?? row.maCompositeScore ?? row.maScore;
        const score = rawScore === null || rawScore === undefined ? null : Number(rawScore);
        const base = row.riverBase === null || row.riverBase === undefined ? null : Number(row.riverBase);
        return [[code, {
          maScore: score !== null && Number.isFinite(score) ? score : null,
          riverBase: base !== null && Number.isFinite(base) && base > 0 ? base : null,
        }] as const];
      }));
      setRankingTechnicalMeta((current) => ({ ...current, ...next }));
    }).catch((error: Error) => {
      if (error.name !== "AbortError") console.warn("[stock-ranking] 位置與均線分數暫時無法更新");
    });
    return () => controller.abort();
  }, [bottomMode, rankingTechnicalTickerKey]);

  useEffect(() => {
    if (bottomMode !== "ranking" || rankMode !== "stocks") return;
    const controller = new AbortController();
    const rankRows = liveRankings.stocks?.[direction] ?? [];
    if (rankRows.length === 0) return;
    const items = rankRows.map((row) => {
      const ticker = rankStockTicker(row.name);
      return `${stockRankExchange(ticker)}:${ticker}`;
    }).join(",");
    const load = () => fetch(`/api/quotes?items=${encodeURIComponent(items)}`, {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("stock-ranking-quotes-failed")))
      .then((payload: { quotes?: MarketQuote[] }) => {
        setStockRankQuotes(Object.fromEntries((payload.quotes ?? []).map((quote) => [quote.key, quote])));
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setStockRankQuotes({});
      });
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 5_000);
    return () => {
      controller.abort();
      timer.cancel();
    };
  }, [bottomMode, rankMode, direction, liveRankings.stocks]);

  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const load = async () => {
      if (loading || controller.signal.aborted) return;
      loading = true;
      try {
        const response = await fetch("/api/live-ranking", {
          cache: "default",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("live-ranking-failed");
        const payload = await response.json() as LiveRankingPayload;
        const rankings = payload.rankings;
        if (!payload.ok || !rankings
          || rankings.stocks.strong.length !== 20 || rankings.stocks.weak.length !== 20
          || rankings.groups.strong.length !== 20 || rankings.groups.weak.length !== 20) {
          throw new Error("live-ranking-incomplete");
        }
        setLiveRankings(rankings);
        if (payload.groupRankings?.strong?.length || payload.groupRankings?.weak?.length) setFullGroupRankings(payload.groupRankings);
        setStockRankQuotes(Object.fromEntries((payload.quotes ?? []).map((quote) => [quote.key, quote])));
        setRankingUpdatedAt(payload.fetchedAt ?? new Date().toISOString());
        setRankingSourceDate(payload.sourceDate ?? null);
        setRankingLiveData(Boolean(payload.liveData));
        try {
          window.localStorage.setItem(LIVE_RANKING_SNAPSHOT_KEY, JSON.stringify(payload));
        } catch {
          // Storage can be unavailable in private browsing; live state still works.
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          console.warn("[live-ranking] 即時前二十名更新失敗，暫留最近成功資料");
        }
      } finally {
        loading = false;
      }
    };
    void load();
    const timer = createVisibilityGatedInterval(() => void load(), 30_000);
    return () => {
      controller.abort();
      timer.cancel();
    };
  }, []);

  const chipGroupByTicker = useMemo(() => {
    const groups = new Map<string, string>();
    for (const group of groupChipMembers?.groups ?? []) {
      for (const code of group.codes) {
        if (!groups.has(code)) groups.set(code, group.name);
      }
    }
    return groups;
  }, [groupChipMembers]);

  const weeklyMainForceByTicker = useMemo(() => new Map(
    weeklyMainForceRows.map((row) => [row.ticker, row] as const),
  ), [weeklyMainForceRows]);

  const brokerBranchDailyByTicker = useMemo(() => new Map(
    brokerBranchDailyRows.map((row) => [row.ticker, row] as const),
  ), [brokerBranchDailyRows]);

  const brokerBranchDailyScores = useMemo(() => rankBrokerBranchWeekly(brokerBranchDailyRows), [brokerBranchDailyRows]);

  const officialChipRows = useMemo(() => {
    if (!officialChipData) return [] as OfficialChipRankRow[];
    const rows = officialChipData.rows
      .filter((row) => {
        const market = row.market === "twse" ? "上市" : row.market === "tpex" ? "上櫃" : "ETF";
        return chipMarketFilter === "全部" || market === chipMarketFilter;
      })
      .map((row) => {
        const factors = averageOfficialFlow(row.series, 1);
        const fiveDayFactors = averageOfficialFlow(row.series, 5);
        const score = calculateOfficialChipScore(factors, chipWeights);
        const fiveDayTrend = calculateOfficialChipScore(fiveDayFactors, chipWeights);
        const previousScore = calculateOfficialChipScore(averageOfficialFlow(row.series.slice(1), 1), chipWeights);
        const previousFiveDayTrend = calculateOfficialChipScore(averageOfficialFlow(row.series.slice(1), 5), chipWeights);
        const weeklyMainForce = weeklyMainForceByTicker.get(row.code) ?? null;
        const dailyBranch = brokerBranchDailyByTicker.get(row.code) ?? null;
        return {
          ticker: row.code,
          name: row.name,
          groupName: chipGroupByTicker.get(row.code) ?? (row.market === "etf" ? "ETF" : "未分類"),
          market: row.market === "twse" ? "上市" as const : row.market === "tpex" ? "上櫃" as const : "ETF" as const,
          exchange: row.exchange,
          changePercent: null,
          priceChange: null,
          latestPrice: null,
          fiveDayTrend,
          combinedScore: calculateCombinedChipScore(score, fiveDayTrend),
          dailyBranchNetAmount: dailyBranch?.netAmount ?? null,
          dailyBranchNetLots: typeof dailyBranch?.netLots === "number" && Number.isFinite(dailyBranch.netLots) ? dailyBranch.netLots : null,
          dailyBranchNetLotsEstimated: false,
          dailyBranchScore: brokerBranchDailyScores.get(row.code) ?? null,
          dailyBranchTradeDate: dailyBranch?.tradeDate ?? null,
          dailyBranchConcentration: dailyBranch?.concentration ?? null,
          dailyBranchActiveBranches: dailyBranch?.activeBranches ?? null,
          weeklyMainForceScore: weeklyMainForce?.compositeScore ?? null,
          weeklyMainForceLabel: weeklyMainForce?.label ?? "待本週三項資料",
          weeklyMainForceWeekEndDate: weeklyMainForce?.weekEndDate ?? null,
          previousCombinedScore: calculateCombinedChipScore(previousScore, previousFiveDayTrend),
          todayCombinedRank: 0,
          previousCombinedRank: 0,
          rankChange: 0,
          trendSeries: row.series.slice(0, 5).reverse().map((point) => ({
            date: point.date,
            score: calculateOfficialChipScore(point, chipWeights),
          })),
          factors,
          score,
          rank: 0,
        };
      });
    const movements = calculateChipRankMovements(rows.map((row) => ({
      ticker: row.ticker,
      currentScore: row.combinedScore,
      previousScore: row.previousCombinedScore,
    })));
    return rows.map((row) => {
      const movement = movements.get(row.ticker);
      return {
        ...row,
        todayCombinedRank: movement?.currentRank ?? 0,
        previousCombinedRank: movement?.previousRank ?? 0,
        rankChange: movement?.change ?? 0,
      };
    });
  }, [officialChipData, chipMarketFilter, chipWeights, chipGroupByTicker, weeklyMainForceByTicker, brokerBranchDailyByTicker, brokerBranchDailyScores]);

  const rankingAfterHoursForceByTicker = useMemo(() => new Map(
    officialChipRows.map((row) => [row.ticker, row.score] as const),
  ), [officialChipRows]);

  const selectedOfficialChipRows = useMemo(() => officialChipRows.filter((row) => {
    if (chipSelectionFilter === "bullish") return row.score >= 60 && row.fiveDayTrend >= 20;
    if (chipSelectionFilter === "bearish") return row.score <= -60 && row.fiveDayTrend <= -20;
    return true;
  }), [officialChipRows, chipSelectionFilter]);

  const rankedOfficialChipRows = useMemo(() => {
    const metric = chipSort?.key === "dailyBranchNetAmount"
      ? "dailyBranchNetAmount"
      : chipSort?.key === "dailyBranchScore"
        ? "dailyBranchScore"
      : chipSort?.key === "weeklyMainForceScore"
        ? "weeklyMainForceScore"
      : chipSort?.key === "combinedScore"
        ? "combinedScore"
      : chipSort?.key === "fiveDayTrend" || (!chipSort && chipPeriod === "five-days")
        ? "fiveDayTrend"
        : "score";
    const direction = chipSort?.key === "changePercent" ? "desc" : chipSort?.direction ?? "desc";
    return [...selectedOfficialChipRows]
      .sort((a, b) => {
        const valueA = a[metric];
        const valueB = b[metric];
        if (valueA === null && valueB === null) return a.ticker.localeCompare(b.ticker);
        if (valueA === null) return 1;
        if (valueB === null) return -1;
        return direction === "asc" ? valueA - valueB : valueB - valueA;
      })
      .slice(0, 20)
      .map((row, index) => ({ ...row, rank: index + 1 }));
  }, [selectedOfficialChipRows, chipSort, chipPeriod]);

  useEffect(() => {
    if (bottomMode !== "chips" || selectedOfficialChipRows.length === 0) return;
    const controller = new AbortController();
    const fullMarketQuotes = chipSort?.key === "changePercent";
    const items = rankedOfficialChipRows.map((row) => `${row.exchange}:${row.ticker}`).join(",");
    fetch(fullMarketQuotes ? "/api/quotes?scope=all" : `/api/quotes?items=${encodeURIComponent(items)}`, {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("quotes-failed")))
      .then((payload: { quotes?: MarketQuote[] }) => {
        const nextQuotes = Object.fromEntries((payload.quotes ?? []).map((quote) => [quote.key, quote]));
        setChipQuotes(nextQuotes);
      })
      .catch((error: Error) => {
        if (error.name !== "AbortError") setChipQuotes({});
      });
    return () => controller.abort();
  }, [bottomMode, selectedOfficialChipRows.length, rankedOfficialChipRows, chipSort?.key]);

  const chipRows = useMemo(() => (chipSort?.key === "changePercent" ? selectedOfficialChipRows : rankedOfficialChipRows).map((row) => {
    const quote = chipQuotes[`${row.exchange}:${row.ticker}`];
    const estimatedNetLots = row.dailyBranchNetLots === null
      && row.dailyBranchNetAmount !== null
      && typeof quote?.price === "number"
      && quote.price > 0
      ? row.dailyBranchNetAmount / quote.price / 1000
      : null;
    return {
      ...row,
      changePercent: quote?.changePct ?? null,
      priceChange: quote?.change ?? null,
      latestPrice: quote?.price ?? null,
      dailyBranchNetLots: row.dailyBranchNetLots ?? estimatedNetLots,
      dailyBranchNetLotsEstimated: row.dailyBranchNetLots === null && estimatedNetLots !== null,
    };
  }), [selectedOfficialChipRows, rankedOfficialChipRows, chipQuotes, chipSort?.key]);

  const displayedChipRows = useMemo(() => {
    const sortedRows = !chipSort ? chipRows : [...chipRows].sort((a, b) => {
      const valueA = chipSort.key === "changePercent" ? a.changePercent : chipSort.key === "score" ? a.score : chipSort.key === "combinedScore" ? a.combinedScore : chipSort.key === "dailyBranchNetAmount" ? a.dailyBranchNetAmount : chipSort.key === "dailyBranchScore" ? a.dailyBranchScore : chipSort.key === "weeklyMainForceScore" ? a.weeklyMainForceScore : a.fiveDayTrend;
      const valueB = chipSort.key === "changePercent" ? b.changePercent : chipSort.key === "score" ? b.score : chipSort.key === "combinedScore" ? b.combinedScore : chipSort.key === "dailyBranchNetAmount" ? b.dailyBranchNetAmount : chipSort.key === "dailyBranchScore" ? b.dailyBranchScore : chipSort.key === "weeklyMainForceScore" ? b.weeklyMainForceScore : b.fiveDayTrend;
      const aMissing = valueA === null || !Number.isFinite(valueA);
      const bMissing = valueB === null || !Number.isFinite(valueB);
      if (aMissing && bMissing) return a.ticker.localeCompare(b.ticker);
      if (aMissing) return 1;
      if (bMissing) return -1;
      const difference = valueA - valueB;
      return chipSort.direction === "asc" ? difference : -difference;
    });
    return sortedRows.slice(0, 20).map((row, index) => ({ ...row, rank: index + 1 }));
  }, [chipRows, chipSort]);

  const chipForceDate = normalizedTaipeiDate(officialChipData?.dataDate) ?? "";
  const chipForceTickerKey = bottomMode === "chips" ? displayedChipRows.map(row => row.ticker).sort().join(",") : "";
  const chipForceValues = useIntradayForceValues(chipForceTickerKey, chipForceDate);

  const toggleChipSort = (key: ChipSortKey) => {
    setChipSort((current) => ({
      key,
      direction: current?.key === key && current.direction === "desc" ? "asc" : "desc",
    }));
  };

  const chipSortIndicator = (key: ChipSortKey) => chipSort?.key === key
    ? chipSort.direction === "asc" ? "▲" : "▼"
    : "⇅";

  const selectChipCandidates = (filter: ChipSelectionFilter) => {
    setChipSelectionFilter(filter);
    setChipSort(filter === "bullish"
      ? { key: "combinedScore", direction: "desc" }
      : filter === "bearish"
        ? { key: "combinedScore", direction: "asc" }
        : null);
  };

  const chipWeightTotal = Object.values(chipWeights).reduce((sum, weight) => sum + weight, 0);
  const analyzedStock = useMemo(() => {
    const query = stockAnalysisQuery.trim().toLowerCase();
    if (!query || !officialChipData) return null;
    const row = officialChipData.rows.find((item) => item.code.toLowerCase() === query || item.name.toLowerCase().includes(query));
    if (!row) return null;
    const latest = averageOfficialFlow(row.series, 1);
    const fiveDays = averageOfficialFlow(row.series, 5);
    return {
      ...row,
      latest,
      fiveDays,
      latestScore: calculateOfficialChipScore(latest, chipWeights),
      fiveDayScore: calculateOfficialChipScore(fiveDays, chipWeights),
      trendSeries: row.series.slice(0, 5).reverse().map((point) => ({
        date: point.date,
        score: calculateOfficialChipScore(point, chipWeights),
      })),
      momentumSeries: row.series.slice(0, 6).reverse().map((point) => ({
        date: point.date,
        score: calculateOfficialChipScore(point, chipWeights),
      })),
    };
  }, [stockAnalysisQuery, officialChipData, chipWeights]);

  useEffect(() => {
    if (bottomMode !== "stock-analysis" || !analyzedStock?.code) {
      setWeeklyMainForceTableHistory([]);
      return;
    }
    const controller = new AbortController();
    fetch(`/api/weekly-main-force-history?ticker=${encodeURIComponent(analyzedStock.code)}`, { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ history?: WeeklyMainForceHistoryRow[] }> : null)
      .then((payload) => setWeeklyMainForceTableHistory(Array.isArray(payload?.history) ? payload.history : []))
      .catch(() => { if (!controller.signal.aborted) setWeeklyMainForceTableHistory([]); });
    return () => controller.abort();
  }, [bottomMode, analyzedStock?.code]);

  useEffect(() => {
    if (groupChipMembers) return;
    const controller = new AbortController();
    fetch("/api/group-chip-members", {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("group-chip-members-failed")))
      .then((payload: GroupChipMemberPayload) => {
        if (payload.ok && Array.isArray(payload.groups)) setGroupChipMembers(payload);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [groupChipMembers]);

  const groupChipRankings = useMemo(() => {
    if (!officialChipData || !groupChipMembers) return [] as GroupChipRankRow[];
    const stocksByCode = new Map(officialChipData.rows.map((row) => [row.code, row]));
    return groupChipMembers.groups.flatMap((group): GroupChipRankRow[] => {
      const rows = group.codes.map((code) => stocksByCode.get(code)).filter((row): row is MarketRankingRow => Boolean(row));
      if (rows.length === 0) return [];
      const latestScores = rows.map((row) => calculateOfficialChipScore(averageOfficialFlow(row.series, 1), chipWeights));
      const fiveDayScores = rows.map((row) => calculateOfficialChipScore(averageOfficialFlow(row.series, 5), chipWeights));
      const trendSeries = Array.from({ length: 5 }, (_, reversedIndex) => {
        const index = 4 - reversedIndex;
        const points = rows.map((row) => row.series[index]).filter((point): point is MarketRankingSeriesPoint => Boolean(point));
        return {
          date: points[0]?.date ?? "",
          score: points.length ? points.reduce((sum, point) => sum + calculateOfficialChipScore(point, chipWeights), 0) / points.length : 0,
        };
      }).filter((point) => point.date);
      return [{
        name: group.name,
        latestScore: Math.round(latestScores.reduce((sum, score) => sum + score, 0) / latestScores.length * 10) / 10,
        fiveDayScore: Math.round(fiveDayScores.reduce((sum, score) => sum + score, 0) / fiveDayScores.length * 10) / 10,
        positiveCount: latestScores.filter((score) => score > 0).length,
        negativeCount: latestScores.filter((score) => score < 0).length,
        coveredCount: rows.length,
        memberCount: group.codes.length,
        trendSeries,
      }];
    });
  }, [officialChipData, groupChipMembers, chipWeights]);

  const groupChipStrongRows = useMemo(() => groupChipRankings.filter((row) => row.latestScore > 0).sort((a, b) => b.latestScore - a.latestScore).slice(0, 10), [groupChipRankings]);
  const groupChipWeakRows = useMemo(() => groupChipRankings.filter((row) => row.latestScore < 0).sort((a, b) => a.latestScore - b.latestScore).slice(0, 10), [groupChipRankings]);

  useEffect(() => {
    if (bottomMode !== "stock-analysis" || !analyzedStock) {
      setStockAnalysisQuote(null);
      return;
    }
    const controller = new AbortController();
    const item = `${analyzedStock.exchange}:${analyzedStock.code}`;
    fetch(`/api/quotes?items=${encodeURIComponent(item)}`, {
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("analysis-quote-failed")))
      .then((payload: { quotes?: MarketQuote[] }) => setStockAnalysisQuote(payload.quotes?.[0] ?? null))
      .catch((error: Error) => {
        if (error.name !== "AbortError") setStockAnalysisQuote(null);
      });
    return () => controller.abort();
  }, [bottomMode, analyzedStock?.code, analyzedStock?.exchange]);

  const isWeak = direction === "weak";
  const focusGroups = liveFocusGroups[direction] ?? (isWeak ? weakFocusGroups : strongFocusGroups);
  const preopenTrialRanking = liveFocusPriceTypes[direction] === "盤前試撮";
  const fallbackSummary = (side: Direction): FocusSummary => {
    const groups = side === "weak" ? weakFocusGroups : strongFocusGroups;
    const averageChange = groups.length > 0
      ? groups.reduce((sum, group) => sum + (Number.parseFloat(group.change) || 0), 0) / groups.length
      : 0;
    return {
      groupCount: groups.filter((group) => side === "weak" ? group.change.startsWith("-") : !group.change.startsWith("-")).length,
      averageChange,
      strength: Math.max(5, Math.min(95, Math.round(50 + averageChange * 8))),
    };
  };
  const strongFocusSummary = liveFocusSummaries.strong ?? fallbackSummary("strong");
  const weakFocusSummary = liveFocusSummaries.weak ?? fallbackSummary("weak");
  const focusSummary = isWeak ? weakFocusSummary : strongFocusSummary;
  const leadGroupName = focusGroups[0]?.name ?? (isWeak ? "弱勢族群" : "強勢族群");
  const nextGroupName = focusGroups[1]?.name ?? "次強族群";
  const focusHeadline = isWeak
    ? `${leadGroupName}轉弱，${nextGroupName}賣壓擴大`
    : `${leadGroupName}領漲，${nextGroupName}接棒`;
  const configuredGroupStocks = selectedGroup
    ? groupChipMembers?.groups.find((group) => group.name === selectedGroup)?.members?.map((stock) => ({ ticker: stock.code, name: stock.name, price: "—", priceChange: "—", change: "—" })) ?? []
    : [];
  const focusGroupStocks = selectedGroup
    ? focusGroups.find((group) => group.name === selectedGroup)?.stocks.flatMap((stock) => {
      const match = stock.symbol.match(/^(\d{4,6})\s+(.+)$/);
      return match ? [{ ticker: match[1], name: match[2], price: "—", priceChange: "—", change: stock.change }] : [];
    }) ?? []
    : [];
  const selectedGroupStocks = selectedGroup
    ? loadedGroupStocks
      ?? (configuredGroupStocks.length ? configuredGroupStocks : null)
      ?? groupStockData[selectedGroup]
      ?? (focusGroupStocks.length ? focusGroupStocks : fallbackGroupStocks(selectedGroup))
    : [];
  const selectedGroupChange = selectedGroup
    ? loadedGroupAverageChange
      ?? focusGroups.find((item) => item.name === selectedGroup)?.change
      ?? [...strongGroups, ...weakGroups].find((item) => item.name === selectedGroup)?.change
    : undefined;
  const selectedGroupChangeValue = Number.parseFloat(selectedGroupChange ?? "");
  const selectedGroupTone = !Number.isFinite(selectedGroupChangeValue) || selectedGroupChangeValue === 0
    ? "neutral"
    : selectedGroupChangeValue < 0 ? "negative" : "positive";
  const desktopStockRows = useMemo(() => {
    const sourceRows = liveRankings.stocks?.[direction] ?? [];
    return stockGroupSort === "group" ? groupRankingRowsByLead(sourceRows) : sourceRows;
  }, [liveRankings, direction, stockGroupSort]);
  const desktopGroupRows = liveRankings.groups?.[direction] ?? [];
  const preopenTrialActive = Object.values(stockRankQuotes).some((quote) => quote.session === "preopen-trial");
  const stockPriceColumnLabel = preopenTrialActive ? "盤前試撮價" : "成交價";
  const chipDataIsCurrent = isChipRankingCurrent(officialChipData, chipDataStatus);
  const chipUpdatedAt = officialChipData?.refreshAttemptedAt ?? officialChipData?.fetchedAt ?? null;
  const chipAutomaticUpdate = chipAutomaticUpdateMessage(officialChipData?.dataDate, chipDataIsCurrent);
  const liveRankingIsCurrent = isLiveRankingCurrent(rankingSourceDate, rankingLiveData);

  return (
    <main className={`battle-shell ${isWeak ? "is-weak" : "is-strong"}`}>
      <header className="topbar">
        <div className="brand-block">
          <div className="logo-mark">H</div>
          <div><strong>HanStock</strong><span>盤中戰鬥版</span></div>
        </div>
        <div className="topbar-actions">
          <DaytradeEarlySellNotifier
            marketTime={time}
            groupRankings={fullGroupRankings ?? liveRankings.groups}
          />
          <Link className="topbar-education-link" href="/education" title="查詢訊號、策略與功能說明"><span>?</span> 教學中心</Link>
        </div>
      </header>

      <nav className="direction-tabs" aria-label="多空方向">
        <button className={direction === "strong" ? "active" : ""} onClick={() => setDirection("strong")}>強勢</button>
        <button className={direction === "weak" ? "active" : ""} onClick={() => setDirection("weak")}>弱勢</button>
      </nav>

      <section className="battle-card">
        <div className="battle-left">
          <div className="battle-summary-row">
            <div className="battle-copy">
              <span className="eyebrow">{isWeak ? "WEAK SIDE" : "STRONG SIDE"}</span>
              <h1>{focusHeadline}</h1>
              <p>{isWeak ? "後 8 弱" : "前 8 強"}平均漲跌幅 {formatSigned(focusSummary.averageChange, 2, "%")}</p>
              <div className="strength-meter"><i style={{ width: `${100 - focusSummary.strength}%` }} /><b style={{ left: `${100 - focusSummary.strength}%` }} /></div>
              <div className="meter-labels"><span>偏多</span><strong>盤勢強度 {focusSummary.strength}</strong><span>偏空</span></div>
            </div>
            <div className="group-score-pair" aria-label="強勢與弱勢族群分數比較">
              <div className="group-count group-count-fraction strong-score">
                <div className="group-fraction" aria-label={`${strongFocusSummary.groupCount}／${HANSTOCK_GROUP_TOTAL} 個強勢族群`}>
                  <strong>{strongFocusSummary.groupCount}</strong>
                  <i />
                  <b>{HANSTOCK_GROUP_TOTAL}</b>
                </div>
                <span>強勢族群</span>
              </div>
              <div className="group-count group-count-fraction weak-score">
                <div className="group-fraction" aria-label={`${weakFocusSummary.groupCount}／${HANSTOCK_GROUP_TOTAL} 個弱勢族群`}>
                  <strong>{weakFocusSummary.groupCount}</strong>
                  <i />
                  <b>{HANSTOCK_GROUP_TOTAL}</b>
                </div>
                <span>弱勢族群</span>
              </div>
            </div>
          </div>
          <section className={`focus-panel focus-groups${preopenTrialRanking ? " is-preopen-trial" : ""}`} aria-label={isWeak ? "跌幅前六大族群" : "漲幅前六大族群"}>
            <div className="focus-title">
              <span>{isWeak ? "跌幅" : "漲幅"}前六大族群</span>
              <small>{preopenTrialRanking ? "盤前四撮・依試撮價自動重排" : "依族群平均漲跌幅"}</small>
            </div>
            <div className="focus-group-list">
              {focusGroups.map((group, index) => (
                <button key={group.name} className="focus-group-row" onClick={() => setSelectedGroup(group.name)} aria-label={`查看 ${group.name} 全部個股`}>
                  <i>{index + 1}</i><strong>{group.name}</strong><b>{group.change}</b>
                </button>
              ))}
            </div>
          </section>
        </div>
        <div className="battle-focus">
          <section className={`focus-panel focus-stocks${preopenTrialRanking ? " is-preopen-trial" : ""}`} aria-label={`六大族群各前三${isWeak ? "弱" : "強"}個股`}>
            <div className="focus-title">
              <span>各族群前三{isWeak ? "弱" : "強"}個股</span>
              <small>{preopenTrialRanking ? "盤前四撮・每撮即時更新" : "依個股漲跌幅"}</small>
            </div>
            <LiveSyncStatus compact polledAt={focusSync.polledAt} sourceAt={focusSync.polledAt} error={focusSync.error} />
            <div className="focus-stock-groups">
              {focusGroups.map((group, index) => (
                <div className="focus-stock-group" key={group.name}>
                  <div className="focus-stock-group-heading">
                    <span>第{index + 1}{isWeak ? "弱" : "強"}</span>
                    <strong title={group.name}>{group.name}</strong>
                    <b>{group.change}</b>
                  </div>
                  <div className="focus-stock-list">
                    {group.stocks.map((stock) => {
                      const ticker = rankStockTicker(stock.symbol);
                      const name = rankStockName(stock.symbol);
                      return <button
                        key={stock.symbol}
                        onClick={() => openOriginalKline(ticker, name)}
                        aria-label={`開啟 ${stock.symbol} 完整 K 線`}
                        title="開啟原始版完整 K 線（預設五分 K，可切換週期）"
                      >
                        <span className="focus-stock-primary">
                          <span className="focus-stock-identity"><strong>{ticker}</strong><em>{name}</em></span>
                          <b>{stock.change}</b>
                        </span>
                        <StockTradingBadges ticker={ticker} compact detailed />
                      </button>;
                    })}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>

      <section className="core-actions" aria-label="戰鬥版核心功能">
        <button className={bottomMode === "ranking" && rankMode === "stocks" ? "selected" : ""} onClick={() => { selectBottomMode("ranking"); setRankMode("stocks"); }}>
          <span className="action-icon">個</span><div><strong>前二十大個股強弱排行</strong><small>67 族群前 20 名即時排行</small></div><b>›</b>
        </button>
        <button className={bottomMode === "ranking" && rankMode === "groups" ? "selected" : ""} onClick={() => { selectBottomMode("ranking"); setRankMode("groups"); }}>
          <span className="action-icon">群</span><div><strong>前二十大族群強弱排行</strong><small>67 族群前 20 名即時排名</small></div><b>›</b>
        </button>
        <button className={bottomMode === "triangles" ? "selected" : ""} onClick={() => selectBottomMode("triangles")}>
          <span className="action-icon">△</span><div><strong>日線三角收斂選股</strong><small>盤後官方日 K 全市場掃描</small></div><b>›</b>
        </button>
        <button className={bottomMode === "revenue-records" ? "selected" : ""} onClick={() => selectBottomMode("revenue-records")}>
          <span className="action-icon">營</span><div><strong>每月營收分析</strong><small>成長榜・族群檢視・高低點</small></div><b>›</b>
        </button>
      </section>

      {bottomMode === "ranking" && (
        <div className="mode-switch" role="tablist" aria-label="排名類型">
          <button className={rankMode === "stocks" ? "active" : ""} onClick={() => setRankMode("stocks")}>前 20 個股強弱</button>
          <button className={rankMode === "groups" ? "active" : ""} onClick={() => setRankMode("groups")}>前 20 族群強弱</button>
        </div>
      )}

      {bottomMode === "chips" && (
        <>
          <section className="chip-update-card" aria-label="盤後籌碼資料更新說明">
            <div className="chip-update-copy">
              <span>每日更新說明</span>
              <p>{CHIP_UPDATE_SCHEDULE.summary}</p>
              <small>
                {CHIP_UPDATE_SCHEDULE.etfNote}
                {officialChipData && `｜目前涵蓋 ${officialChipData.coverage.total.toLocaleString("zh-TW")} 檔（上市 ${officialChipData.coverage.twse.toLocaleString("zh-TW")}／上櫃 ${officialChipData.coverage.tpex.toLocaleString("zh-TW")}／ETF ${officialChipData.coverage.etf.toLocaleString("zh-TW")}）`}
              </small>
            </div>
            <div className={`chip-update-time ${chipDataIsCurrent ? "is-ready" : "is-waiting"}`}>
              <span><i />{chipAutomaticUpdate.label}</span>
              <strong>
                {chipDataStatus === "loading" && !officialChipData
                  ? "正在讀取正式盤後資料…"
                  : chipDataStatus === "error"
                    ? "正式資料取得失敗"
                    : officialChipData
                      ? `資料日 ${officialChipData.dataDate}｜${chipDataIsCurrent ? "最新更新" : "最新檢查"} ${formatTaipeiDateTime(chipUpdatedAt)}`
                      : "尚未讀取"}
              </strong>
              {officialChipData && <small>{chipAutomaticUpdate.detail}</small>}
              <small>伺服器盤後每 5 分鐘主動重試；頁面每 {chipAutoRefreshMinutes} 分鐘同步顯示</small>
              <button type="button" onClick={() => setChipRefreshTick((value) => value + 1)} disabled={chipDataStatus === "loading"}>
                {chipDataStatus === "loading" ? "全部更新中" : "全部立即更新"}
              </button>
            </div>
          </section>

          <section className="chip-combined-guide" aria-label="綜合籌碼選股說明">
            <div><span>綜合籌碼選股</span><strong>綜合分數＝今天分數 60%＋近五日平均 40%</strong></div>
            <p>今天看爆發，五日看延續。多方雙強為今天分數至少 60、五日平均至少 20；空方雙弱為今天分數至多 -60、五日平均至多 -20。點擊表格的「綜合分數」可由高到低或由低到高排序。</p>
          </section>

          <section className="chip-weight-card" aria-label="盤後籌碼指標權重設定">
            <button className="chip-weight-summary" onClick={() => setShowChipWeights((open) => !open)} aria-expanded={showChipWeights}>
              <span><b>指標權重</b><small>主力 35% · 外資 25% · 投信 18% · ETF持股 12% · 自營自買 8% · 自營避險 2%</small></span>
              <strong>合計 {chipWeightTotal}%</strong>
              <i>{showChipWeights ? "收合" : "調整"}</i>
            </button>
            {showChipWeights && (
              <div className="chip-weight-settings">
                <p>拖曳調整後會立即重新排行。目前正式計分使用外資、投信、自營自買、自營避險四項，主力與 ETF 持股權重會在來源接妥後自動啟用。</p>
                <div className="chip-weight-list">
                  {chipWeightMeta.map((item) => (
                    <label key={item.key} className={`chip-weight-row weight-${item.key}`}>
                      <span><b>{item.label}</b><small>{item.description}</small></span>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        step="1"
                        value={chipWeights[item.key]}
                        onChange={(event) => setChipWeights((current) => ({ ...current, [item.key]: Number(event.target.value) }))}
                        aria-label={`${item.label}權重`}
                      />
                      <strong>{chipWeights[item.key]}%</strong>
                    </label>
                  ))}
                </div>
                <div className="chip-weight-foot">
                  <button onClick={() => setChipWeights(DEFAULT_CHIP_WEIGHTS)}>恢復建議權重</button>
                  <span>計算時依目前合計比例自動正規化</span>
                </div>
              </div>
            )}
          </section>

          <div className="mode-switch chip-period-switch" role="tablist" aria-label="盤後籌碼排行期間">
            <button className={chipPeriod === "previous-day" ? "active" : ""} onClick={() => setChipPeriod("previous-day")}>今天盤後籌碼增減排行</button>
            <button className={chipPeriod === "five-days" ? "active" : ""} onClick={() => setChipPeriod("five-days")}>近五日平均盤後籌碼增減排行</button>
          </div>
        </>
      )}

      <IntradayTrackingNotifier onOpenTracker={openIntradayTracker} onOpenStock={openKlineByTicker} />
      <MaScreenerSignalNotifier stocks={officialChipData?.rows ?? []} onOpenStock={openKlineByTicker} />
      <MonthlyRevenueRecords active={bottomMode === "revenue-records"} onOpenStock={openKlineByTicker} />
      {bottomMode === "revenue-records" ? null : bottomMode === "intraday-tracking" ? <IntradayStockTrackingPanel onOpenStock={openKlineByTicker} /> : bottomMode === "triangles" ? <TriangleScreenerPanel /> : bottomMode === "daytrade" ? <DaytradeBrokerPanel thresholds={daytradeThresholds} /> : bottomMode === "weekly-chips" ? (
        <WeeklyChipAnalysisPanel
          rows={weeklyChipData?.rows ?? []}
          groups={groupChipMembers?.groups ?? []}
          groupByTicker={chipGroupByTicker}
          weights={chipWeights}
          loading={weeklyChipDataStatus === "loading"}
          updatedAt={weeklyChipData?.fetchedAt ?? null}
          onRefresh={() => setChipRefreshTick((value) => value + 1)}
          onOpenStock={openKlineByTicker}
          onOpenGroup={setSelectedGroup}
        />
      ) : bottomMode === "stock-analysis" ? (
        <section className="stock-analysis-console" aria-label="個股籌碼分析">
          <header className="stock-analysis-head">
            <div>
              <span className="eyebrow">STOCK RESEARCH CENTER</span>
              <h2>個股研究中心</h2>
              <p>基本面估值河流、歷史價位位置均線、機構大戶持股追蹤與盤後籌碼分析，輸入一次股票即可切換查看。</p>
            </div>
            <div className={`stock-analysis-data-state ${chipDataIsCurrent ? "is-ready" : "is-waiting"}`} aria-live="polite">
              <span>{chipAutomaticUpdate.label}</span>
              <strong>資料日 {officialChipData?.dataDate ?? "—"}</strong>
              <small>{officialChipData ? `${chipDataIsCurrent ? "最新更新" : "最新檢查"} ${formatTaipeiDateTime(chipUpdatedAt)}` : "尚未取得資料"}</small>
              <button type="button" onClick={() => setChipRefreshTick((value) => value + 1)} disabled={chipDataStatus === "loading"}>{chipDataStatus === "loading" ? "更新中" : "立即更新"}</button>
            </div>
          </header>
          <label className="stock-analysis-search">
            <span>搜尋上市、上櫃與 ETF</span>
            <input value={stockAnalysisQuery} onChange={(event) => setStockAnalysisQuery(event.target.value)} placeholder="例如 2330、台積電" inputMode="search" />
          </label>
          <nav className="stock-research-tabs" aria-label="個股研究功能" role="tablist">
            <button type="button" role="tab" aria-selected={stockResearchTab === "fundamental"} className={stockResearchTab === "fundamental" ? "active" : ""} onClick={() => setStockResearchTab("fundamental")}><span>本</span><strong>基本面河流圖</strong><small>EPS × 同族群估值</small></button>
            <button type="button" role="tab" aria-selected={stockResearchTab === "river"} className={stockResearchTab === "river" ? "active" : ""} onClick={() => setStockResearchTab("river")}><span>均</span><strong>歷史價位位置均線</strong><small>160 日價位位置</small></button>
            <button type="button" role="tab" aria-selected={stockResearchTab === "holders"} className={stockResearchTab === "holders" ? "active" : ""} onClick={() => setStockResearchTab("holders")}><span>戶</span><strong>機構大戶</strong><small>持股與內部人異動</small></button>
            <button type="button" role="tab" aria-selected={stockResearchTab === "chips"} className={stockResearchTab === "chips" ? "active" : ""} onClick={() => setStockResearchTab("chips")}><span>籌</span><strong>籌碼分析</strong><small>法人與主力分數</small></button>
          </nav>
          {stockResearchTab === "fundamental" && <FundamentalRiverWorkspace stock={analyzedStock ? { code: analyzedStock.code, name: analyzedStock.name, market: analyzedStock.market } : null} livePrice={analyzedStock && (stockAnalysisQuote?.key === analyzedStock.code || stockAnalysisQuote?.key.endsWith(`:${analyzedStock.code}`)) ? stockAnalysisQuote.price : null} onSelect={setStockAnalysisQuery} onOpenKline={openKlineByTicker} />}
          {stockResearchTab === "river" && <ValuationRiverScreener stocks={officialChipData?.rows ?? []} groupByTicker={chipGroupByTicker} onSelect={setStockAnalysisQuery} onOpenKline={openKlineByTicker} />}
          {stockResearchTab === "holders" && <LargeHolderScreener stocks={officialChipData?.rows ?? []} onSelect={setStockAnalysisQuery} />}
          {stockResearchTab === "chips" && <>
          <WeeklyChipStockTrend
            query={stockAnalysisQuery}
            rows={weeklyChipData?.rows ?? []}
            groupByTicker={chipGroupByTicker}
            weights={chipWeights}
            loading={weeklyChipDataStatus === "loading"}
            onOpenStock={openKlineByTicker}
          />
          <section className="stock-analysis-weights" aria-label="個股籌碼分析權重設定">
            <button className="chip-weight-summary" type="button" onClick={() => setShowChipWeights((open) => !open)} aria-expanded={showChipWeights}>
              <span><b>籌碼加權指標設定</b><small>主力、外資、投信、ETF 持股、自營自買、自營避險</small></span>
              <strong>合計 {chipWeightTotal}%</strong>
              <i>{showChipWeights ? "收合 ▲" : "調整 ▼"}</i>
            </button>
            {showChipWeights && (
              <div className="chip-weight-settings">
                <p>與盤後籌碼排行共用同一組權重；目前正式分數先依已接入的外資、投信、自營自買、自營避險四項自動正規化。</p>
                <div className="chip-weight-list">
                  {chipWeightMeta.map((item) => (
                    <label key={`analysis-${item.key}`} className={`chip-weight-row weight-${item.key}`}>
                      <span><b>{item.label}</b><small>{item.description}</small></span>
                      <input type="range" min="0" max="100" step="1" value={chipWeights[item.key]} onChange={(event) => setChipWeights((current) => ({ ...current, [item.key]: Number(event.target.value) }))} aria-label={`個股分析${item.label}權重`} />
                      <strong>{chipWeights[item.key]}%</strong>
                    </label>
                  ))}
                </div>
                <div className="chip-weight-foot"><button type="button" onClick={() => setChipWeights(DEFAULT_CHIP_WEIGHTS)}>恢復建議權重</button><span>修改後個股分數與五日趨勢立即重算</span></div>
              </div>
            )}
          </section>
          </>}
          {stockResearchTab !== "fundamental" && !stockAnalysisQuery.trim() && <div className="stock-analysis-empty">請輸入股票代號或名稱，或從上方篩選清單點選股票查看完整分析</div>}
          {stockResearchTab !== "fundamental" && stockAnalysisQuery.trim() && chipDataStatus === "loading" && !officialChipData && <div className="stock-analysis-empty">正在讀取完整盤後籌碼資料…</div>}
          {stockResearchTab !== "fundamental" && stockAnalysisQuery.trim() && chipDataStatus !== "loading" && !analyzedStock && <div className="stock-analysis-empty">找不到這檔股票，請確認代號或名稱</div>}
          {analyzedStock && stockResearchTab === "chips" && (
            <article className="stock-analysis-card">
              <div className="stock-analysis-title">
                <div><strong>{analyzedStock.code}　{analyzedStock.name}</strong><StockTradingBadges ticker={analyzedStock.code} /><small>最近完整交易日：{analyzedStock.series[0]?.date ?? officialChipData?.dataDate ?? "—"}</small></div>
                <div><span>{analyzedStock.market === "twse" ? "上市" : analyzedStock.market === "tpex" ? "上櫃" : "ETF"}</span><b className={(stockAnalysisQuote?.changePct ?? 0) < 0 ? "negative" : (stockAnalysisQuote?.changePct ?? 0) > 0 ? "positive" : "neutral"}>{stockAnalysisQuote?.price == null ? "現價 —" : `${stockAnalysisQuote.session === "preopen-trial" ? "盤前試撮 " : "現價 "}${stockAnalysisQuote.price.toFixed(2)}　${stockAnalysisQuote.changePct == null ? "—" : formatSigned(stockAnalysisQuote.changePct, 2, "%")}`}</b></div>
              </div>
              <div className="stock-analysis-scores">
                <div><span>最近交易日分數</span><strong className={analyzedStock.latestScore < 0 ? "negative" : "positive"}>{formatSigned(analyzedStock.latestScore, 1)}</strong><small>{chipJudgement(analyzedStock.latestScore).label}</small></div>
                <div><span>五日籌碼分數</span><strong className={analyzedStock.fiveDayScore < 0 ? "negative" : "positive"}>{formatSigned(analyzedStock.fiveDayScore, 1)}</strong><small>{chipJudgement(analyzedStock.fiveDayScore).label}</small></div>
                <div><span>最近五日趨勢圖</span><FiveDayTrendPreview code={analyzedStock.code} name={analyzedStock.name} value={analyzedStock.fiveDayScore} series={analyzedStock.trendSeries} /><small>點擊數字查看每日曲線</small></div>
              </div>
              <ChipMomentumHistogram code={analyzedStock.code} name={analyzedStock.name} series={analyzedStock.momentumSeries} />
              <h3 className="stock-analysis-subtitle">主力綜合籌碼判讀（週）</h3>
              <div className="stock-analysis-factors">
                <WeeklyMainForceCards code={analyzedStock.code} institutionalScore={analyzedStock.fiveDayScore} onCompositeUpdate={setWeeklyMainForceSnapshot} />
              </div>
              <h3 className="stock-analysis-subtitle">籌碼指標明細</h3>
              <div className="stock-analysis-factors">
                <BrokerBranchDetailFactor code={analyzedStock.code} />
                <span><b>外資</b><strong>{formatSigned(analyzedStock.latest.foreign, 1)}</strong><small>五日 {formatSigned(analyzedStock.fiveDays.foreign, 1)}</small></span>
                <span><b>投信</b><strong>{formatSigned(analyzedStock.latest.trust, 1)}</strong><small>五日 {formatSigned(analyzedStock.fiveDays.trust, 1)}</small></span>
                <ActiveEtfDetailFactor code={analyzedStock.code} />
                <span><b>自營自買</b><strong>{formatSigned(analyzedStock.latest.dealer, 1)}</strong><small>五日 {formatSigned(analyzedStock.fiveDays.dealer, 1)}</small></span>
                <span><b>自營避險</b><strong>{formatSigned(analyzedStock.latest.hedge, 1)}</strong><small>五日 {formatSigned(analyzedStock.fiveDays.hedge, 1)}</small></span>
              </div>
              <h3 className="stock-analysis-subtitle">最近交易日明細</h3>
              <div className="stock-analysis-history-scroll" role="region" aria-label={`${analyzedStock.code}最近交易日籌碼明細`} tabIndex={0}>
                <div className="stock-analysis-history">
                  <div className="stock-analysis-history-row is-head"><span>日期</span><span>外資</span><span>投信</span><span>自營自買</span><span>自營避險</span><span>當日加權</span><span>主力週綜合</span><span>短中線綜合</span><span>判斷</span></div>
                  {analyzedStock.series.slice(0, 6).map((point) => {
                    const score = calculateOfficialChipScore(point, chipWeights);
                    const pointDate = point.date.replaceAll("/", "-").slice(0, 10);
                    const pointWeekEndDate = tradingWeekEndDate(point.date);
                    const savedWeekly = weeklyMainForceTableHistory.find((record) => {
                      const savedDate = record.weekEndDate.replaceAll("/", "-").slice(0, 10);
                      return savedDate === pointDate || savedDate === pointWeekEndDate;
                    });
                    const matchingWeek = weeklyMainForceSnapshot?.ticker === analyzedStock.code
                      && (pointWeekEndDate === weeklyMainForceSnapshot.weekEndDate.replaceAll("/", "-") || pointDate === weeklyMainForceSnapshot.weekEndDate.replaceAll("/", "-"));
                    const weeklyScore = savedWeekly?.compositeScore ?? (matchingWeek ? weeklyMainForceSnapshot.score : null);
                    const combinedScore = weeklyScore === null ? null : Number((score * 0.6 + weeklyScore * 0.4).toFixed(1));
                    const judgement = chipJudgement(combinedScore ?? score);
                    return <div className="stock-analysis-history-row" key={`${analyzedStock.code}-${point.date}`}><span>{point.date}</span><strong>{formatSigned(point.foreign, 1)}</strong><strong>{formatSigned(point.trust, 1)}</strong><strong>{formatSigned(point.dealer, 1)}</strong><strong>{formatSigned(point.hedge, 1)}</strong><b className={score < 0 ? "negative" : "positive"}>{formatSigned(score, 1)}</b><b className={weeklyScore === null ? "pending" : weeklyScore < 0 ? "negative" : "positive"}>{weeklyScore === null ? "—" : formatSigned(weeklyScore, 1)}</b><b className={combinedScore === null ? "pending" : combinedScore < 0 ? "negative" : "positive"}>{combinedScore === null ? "—" : formatSigned(combinedScore, 1)}</b><em className={`chip-judgement ${judgement.className}`}>{judgement.label}</em></div>;
                  })}
                </div>
              </div>
              <button className="stock-analysis-kline-button" onClick={() => openKlineByTicker(analyzedStock.code, analyzedStock.name)}>開啟這檔股票的五分鐘 K 線</button>
            </article>
          )}
          {analyzedStock && stockResearchTab === "river" && (
            <ValuationRiverPanel ticker={analyzedStock.code} name={analyzedStock.name} market={analyzedStock.market} currentPrice={stockAnalysisQuote?.price ?? null} flows={analyzedStock.series} onOpenKline={() => openKlineByTicker(analyzedStock.code, analyzedStock.name)} />
          )}
          {analyzedStock && stockResearchTab === "holders" && (
            <InstitutionalHolderPanel ticker={analyzedStock.code} name={analyzedStock.name} market={analyzedStock.market} currentPrice={stockAnalysisQuote?.price ?? null} flows={analyzedStock.series} onOpenKline={() => openKlineByTicker(analyzedStock.code, analyzedStock.name)} />
          )}
        </section>
      ) : bottomMode === "etf-holdings" ? <LiveEtfHoldingsPanel returnTo="/" /> : bottomMode === "watchlist" ? <WatchlistPanel /> : bottomMode === "ranking" && isDesktop ? (
        <section className="desktop-dual-ranking" aria-label="個股與族群前二十大強弱排行">
          <article className="desktop-ranking-panel" id="desktop-stock-ranking">
            <div className="desktop-ranking-head">
              <div><span className="eyebrow">{isWeak ? "WEAK STOCKS" : "STRONG STOCKS"}</span><h2>{isWeak ? "弱勢" : "強勢"}前二十大個股排行</h2></div>
              <FreshnessBadge current={liveRankingIsCurrent} updatedAt={rankingUpdatedAt} dataDate={rankingSourceDate} />
            </div>
            <div className="rank-table desktop-stock-table">
              <div className="rank-header desktop-stock-header"><span>排名</span><span>代號</span><span>名稱</span><span>目前位置</span><span>均線分數</span><span>漲跌幅</span><span>漲跌</span><span>{stockPriceColumnLabel}</span><button type="button" className={`rank-group-sort-button ${stockGroupSort === "group" ? "active" : ""}`} onClick={toggleStockGroupSort} aria-pressed={stockGroupSort === "group"} aria-label={`所屬族群排列，目前${stockGroupSort === "group" ? "同族群集中" : "原強弱排名"}`} title={stockGroupSort === "group" ? "恢復原強弱排名" : "將同族群股票集中排列"}>所屬族群 <b>{stockGroupSort === "group" ? "群" : "⇅"}</b></button></div>
              {desktopStockRows.length === 0 && <div className="stock-ranking-loading">正在讀取最新前二十名排行…</div>}
              {desktopStockRows.map((row) => {
                const ticker = rankStockTicker(row.name);
                const quote = stockRankQuotes[ticker] ?? stockRankQuotes[`${stockRankExchange(ticker)}:${ticker}`];
                const technical = rankingTechnicalMeta[ticker];
                const position = quote?.price !== null && quote?.price !== undefined && technical?.riverBase
                  ? valuationRiverPosition(quote.price, technical.riverBase)
                  : null;
                const changePercent = quote?.changePct ?? Number.parseFloat(row.change);
                const tone = changePercent < 0 ? "negative" : changePercent > 0 ? "positive" : "neutral";
                const afterHoursForce = rankingAfterHoursForceByTicker.get(ticker);
                return (
                  <button className="rank-row desktop-stock-row" key={`desktop-stock-${direction}-${row.name}`} onClick={() => openOriginalKline(ticker, rankStockName(row.name))} title="開啟原始版完整 K 線">
                    <span className="rank-number">{row.rank.toString().padStart(2, "0")}</span>
                    <span className="rank-ticker">{ticker}</span>
                    <span className="rank-name"><strong>{rankStockName(row.name)}</strong><StockTradingBadges ticker={ticker} compact /><small>{row.leadChange}</small><em className={`ranking-after-hours-force ${afterHoursForce === undefined ? "pending" : afterHoursForce < 0 ? "negative" : afterHoursForce > 0 ? "positive" : "neutral"}`}>盤後大戶力 {afterHoursForce === undefined ? "待補" : formatSigned(afterHoursForce, 1)}</em></span>
                    <span className={`ranking-position-badge ${rankingPositionTone(position)}`}>{position ?? "待算"}</span>
                    <span className="ranking-ma-score-badge">均線 {technical?.maScore ?? "—"}/15</span>
                    <span className={`rank-change ${tone}`}>{quote?.changePct == null ? row.change : formatSigned(quote.changePct, 2, "%")}</span>
                    <span className={`rank-price-change ${tone}`}>{quote?.change == null ? "—" : formatSigned(quote.change, 2)}</span>
                    <span className={`rank-latest-price ${tone}`}>{quote?.price == null ? "—" : quote.price.toFixed(2)}</span>
                    <span className="rank-lead">{row.lead}</span>
                  </button>
                );
              })}
            </div>
          </article>

          <article className="desktop-ranking-panel" id="desktop-group-ranking">
            <div className="desktop-ranking-head">
              <div><span className="eyebrow">{isWeak ? "WEAK GROUPS" : "STRONG GROUPS"}</span><h2>{isWeak ? "弱勢" : "強勢"}前二十大族群排行</h2></div>
              <FreshnessBadge current={liveRankingIsCurrent} updatedAt={rankingUpdatedAt} dataDate={rankingSourceDate} />
            </div>
            <div className="rank-table desktop-group-table">
              <div className="rank-header desktop-group-header"><span>排名</span><span>族群</span><span>漲跌幅</span><span>領漲股</span></div>
              {desktopGroupRows.length === 0 && <div className="stock-ranking-loading">正在讀取最新前二十名排行…</div>}
              {desktopGroupRows.map((row) => (
                <button className="rank-row desktop-group-row" key={`desktop-group-${direction}-${row.name}`} onClick={() => setSelectedGroup(row.name)} title={`查看 ${row.name} 即時個股清單`}>
                  <span className="rank-number">{row.rank.toString().padStart(2, "0")}</span>
                  <span className="rank-name"><strong>{row.name}</strong><small>{row.leadChange}</small></span>
                  <span className={`rank-change ${row.change.startsWith("-") ? "negative" : "positive"}`}>{row.change}</span>
                  <span className="rank-lead">{row.lead}</span>
                </button>
              ))}
            </div>
          </article>
          {!isWeak && <div className="desktop-high-dividend-wide"><HighDividendEtfPanel onOpenKline={openKlineByTicker} /></div>}
        </section>
      ) : <section className="ranking-section">
        <div className="section-head">
          <div>
            <span className="eyebrow">{bottomMode === "chips" ? "AFTER-HOURS CHIPS" : isWeak ? "WEAK RANKING" : "STRONG RANKING"}</span>
            <h2>{bottomMode === "chips" ? chipPeriod === "previous-day" ? "全市場今天盤後籌碼增減排行榜" : "全市場近五日平均盤後籌碼增減排行" : rankMode === "stocks" ? `${isWeak ? "弱勢" : "強勢"}前二十大個股排行` : `${isWeak ? "弱勢" : "強勢"}前二十大族群排行`}</h2>
          </div>
          {bottomMode === "chips" ? (
            <div className="chip-heading-tools">
              <div className={`chip-head-update ${chipDataIsCurrent ? "is-ready" : "is-waiting"}`} aria-live="polite">
                <span>{chipAutomaticUpdate.label}</span>
                <strong>資料日 {officialChipData?.dataDate ?? "—"}</strong>
                <small>{chipDataIsCurrent ? "最新更新" : "最新檢查"} {formatTaipeiDateTime(chipUpdatedAt)}</small>
                <small>每 {chipAutoRefreshMinutes} 分鐘自動更新</small>
                <button type="button" onClick={() => setChipRefreshTick((value) => value + 1)} disabled={chipDataStatus === "loading"}>
                  {chipDataStatus === "loading" ? "全部更新中" : "全部立即更新"}
                </button>
              </div>
              <div className="chip-market-filter" role="group" aria-label="市場篩選，可切換全部、上市、上櫃或 ETF">
                <span><i />市場篩選（可切換）</span>
                <div>
                  {(["全部", "上市", "上櫃", "ETF"] as ChipMarketFilter[]).map((market) => (
                    <button
                      key={market}
                      type="button"
                      className={chipMarketFilter === market ? "active" : ""}
                      onClick={() => setChipMarketFilter(market)}
                      aria-pressed={chipMarketFilter === market}
                    >
                      {market}
                    </button>
                  ))}
                </div>
              </div>
              <div className="chip-selection-filter" role="group" aria-label="綜合籌碼選股篩選">
                <span>綜合選股篩選</span>
                <div>
                  <button type="button" className={chipSelectionFilter === "all" ? "active" : ""} onClick={() => selectChipCandidates("all")} aria-pressed={chipSelectionFilter === "all"}>全部排行</button>
                  <button type="button" className={chipSelectionFilter === "bullish" ? "active bullish" : ""} onClick={() => selectChipCandidates("bullish")} aria-pressed={chipSelectionFilter === "bullish"}>多方雙強</button>
                  <button type="button" className={chipSelectionFilter === "bearish" ? "active bearish" : ""} onClick={() => selectChipCandidates("bearish")} aria-pressed={chipSelectionFilter === "bearish"}>空方雙弱</button>
                </div>
              </div>
            </div>
          ) : (
            <FreshnessBadge current={liveRankingIsCurrent} updatedAt={rankingUpdatedAt} dataDate={rankingSourceDate} />
          )}
        </div>

        {bottomMode === "chips" ? (
          <div className="chip-table-scroll" role="region" aria-label="全市場盤後籌碼排行表" tabIndex={0}>
            <div className="chip-rank-table">
              <div className="chip-rank-header">
                <span>排行</span><span>代號</span><span>名稱</span>
                <span>所屬族群</span>
                <span>漲跌幅</span>
                <span>漲跌</span><span>最新成交價</span><span>市場</span>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("score")} aria-label={`最新分數排序，${chipSort?.key === "score" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">最新分數<small>（今天變強或變弱）</small></span><b>{chipSortIndicator("score")}</b>
                </button>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("fiveDayTrend")} aria-label={`5日趨勢排序，${chipSort?.key === "fiveDayTrend" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">五日平均<small>（延續性）</small></span><b>{chipSortIndicator("fiveDayTrend")}</b>
                </button>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("combinedScore")} aria-label={`綜合分數排序，${chipSort?.key === "combinedScore" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">短線綜合<small>（60%／40%）</small></span><b>{chipSortIndicator("combinedScore")}</b>
                </button>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("dailyBranchNetAmount")} aria-label={`分點主力日買賣超排序，${chipSort?.key === "dailyBranchNetAmount" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">分點主力日淨額<small>前五買－前五賣</small></span><b>{chipSortIndicator("dailyBranchNetAmount")}</b>
                </button>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("dailyBranchScore")} aria-label={`分點主力日分數排序，${chipSort?.key === "dailyBranchScore" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">分點主力日分數<small>淨額65%／集中35%</small></span><b>{chipSortIndicator("dailyBranchScore")}</b>
                </button>
                <span className="chip-rank-move-head">分點主力日張數<small>買超＋／賣超－</small></span>
                <button className="chip-sort-button" type="button" onClick={() => toggleChipSort("weeklyMainForceScore")} aria-label={`每週主力綜合排序，${chipSort?.key === "weeklyMainForceScore" ? `目前${chipSort.direction === "asc" ? "升冪" : "降冪"}` : "點擊後由高到低"}`}>
                  <span className="chip-sort-label">每週主力綜合<small>（35%／40%／25%）</small></span><b>{chipSortIndicator("weeklyMainForceScore")}</b>
                </button>
                <span className="chip-rank-move-head">與昨日比較<small>（綜合名次）</small></span>
                <span>判斷</span>
              </div>
              {displayedChipRows.map((row, index) => {
                const judgementScore = chipSort?.key === "dailyBranchNetAmount" || chipSort?.key === "dailyBranchScore"
                  ? row.dailyBranchScore ?? row.combinedScore
                  : chipSort?.key === "weeklyMainForceScore"
                  ? row.weeklyMainForceScore ?? row.combinedScore
                  : chipSort?.key === "combinedScore" || chipSelectionFilter !== "all"
                    ? row.combinedScore
                    : chipPeriod === "five-days" ? row.fiveDayTrend : row.score;
                const judgement = chipJudgement(judgementScore);
                const isDown = row.changePercent !== null && row.changePercent < 0;
                return (
                  <div className="chip-rank-row" key={`${chipPeriod}-${row.ticker}`}>
                    <span className="chip-rank-number">{index + 1}</span>
                    <button onClick={() => openKlineByTicker(row.ticker, row.name)} title="開啟原始版完整 K 線">{row.ticker}</button>
                    <button onClick={() => openKlineByTicker(row.ticker, row.name)} title="開啟原始版完整 K 線"><span>{row.name}<i>›</i></span><StockTradingBadges ticker={row.ticker} compact dense trailingBadge={<ChipIntradayForceBadge value={chipForceValues[row.ticker]} tradeDate={chipForceDate} />} /></button>
                    <span className="chip-group-name" title={row.groupName}>{row.groupName}</span>
                    <strong className={isDown ? "negative" : row.changePercent !== null && row.changePercent > 0 ? "positive" : "neutral"}>{row.changePercent === null ? "—" : formatSigned(row.changePercent, 2, "%")}</strong>
                    <strong className={isDown ? "negative" : row.priceChange !== null && row.priceChange > 0 ? "positive" : "neutral"}>{row.priceChange === null ? "—" : formatSigned(row.priceChange)}</strong>
                    <strong className={isDown ? "negative" : row.changePercent !== null && row.changePercent > 0 ? "positive" : "neutral"}>{row.latestPrice === null ? "—" : row.latestPrice.toFixed(2)}</strong>
                    <span className={`market-pill market-${row.market === "上市" ? "listed" : row.market === "上櫃" ? "otc" : "etf"}`}>{row.market}</span>
                    <strong className={row.score < 0 ? "negative" : "positive"}>{formatSigned(row.score, 1)}</strong>
                    <FiveDayTrendPreview code={row.ticker} name={row.name} value={row.fiveDayTrend} series={row.trendSeries} />
                    <strong className={row.combinedScore < 0 ? "negative" : "positive"}>{formatSigned(row.combinedScore, 1)}</strong>
                    <span className={`chip-daily-branch ${row.dailyBranchNetAmount === null ? "pending" : row.dailyBranchNetAmount < 0 ? "negative" : "positive"}`} title={row.dailyBranchTradeDate ? `${row.dailyBranchTradeDate}｜${row.dailyBranchNetAmount !== null && row.dailyBranchNetAmount >= 0 ? "淨買超" : "淨賣超"} ${row.dailyBranchNetAmount === null ? "—" : Math.abs(row.dailyBranchNetAmount).toLocaleString("zh-TW")} 元｜${row.dailyBranchActiveBranches ?? 0} 個活躍分點` : "等待最新交易日分點資料"}>
                      <strong>{row.dailyBranchNetAmount === null ? "待資料" : formatTwd(row.dailyBranchNetAmount)}</strong>
                      <small>{row.dailyBranchNetAmount === null ? "等待回補" : row.dailyBranchNetAmount >= 0 ? "淨買超" : "淨賣超"}</small>
                    </span>
                    <span className={`chip-daily-branch ${row.dailyBranchScore === null ? "pending" : row.dailyBranchScore < 0 ? "negative" : "positive"}`} title={row.dailyBranchTradeDate ? `${row.dailyBranchTradeDate}｜集中度 ${row.dailyBranchConcentration?.toFixed(2) ?? "—"}%｜${row.dailyBranchActiveBranches ?? 0} 個活躍分點` : "等待最新交易日分點資料"}>
                      <strong>{row.dailyBranchScore === null ? "待資料" : formatSigned(row.dailyBranchScore, 1, " 分")}</strong>
                      <small>{row.dailyBranchTradeDate ?? "等待回補"}</small>
                    </span>
                    <span className={`chip-daily-branch ${row.dailyBranchNetLots === null ? "pending" : row.dailyBranchNetLots < 0 ? "negative" : "positive"}${row.dailyBranchNetLotsEstimated ? " estimated" : ""}`} title={row.dailyBranchNetLots === null ? "等待最新交易日分點張數" : row.dailyBranchNetLotsEstimated ? "目前依分點主力日淨額與成交價估算；盤後 FinMind 完成回補後改顯示實際前五分點買賣超張數" : `${row.dailyBranchTradeDate ?? "最新交易日"}｜實際前五分點買賣超張數`}>
                      <strong>{row.dailyBranchNetLots === null ? "待資料" : `${row.dailyBranchNetLotsEstimated ? "約 " : ""}${formatSignedLots(row.dailyBranchNetLots)}`}</strong>
                      <small>{row.dailyBranchNetLots === null ? "等待回補" : row.dailyBranchNetLotsEstimated ? "依淨額／成交價估算" : "實際買賣超"}</small>
                    </span>
                    <span className={`chip-weekly-main-force ${row.weeklyMainForceScore === null ? "pending" : row.weeklyMainForceScore < 0 ? "negative" : "positive"}`} title={row.weeklyMainForceWeekEndDate ? `${row.weeklyMainForceWeekEndDate}｜${row.weeklyMainForceLabel}` : row.weeklyMainForceLabel}>
                      <strong>{row.weeklyMainForceScore === null ? "待資料" : formatSigned(row.weeklyMainForceScore, 1)}</strong>
                      <small>{row.weeklyMainForceLabel}</small>
                    </span>
                    <span className={`chip-rank-move ${row.rankChange > 0 ? "up" : row.rankChange < 0 ? "down" : "flat"}`} title={`今天綜合第 ${row.todayCombinedRank} 名｜昨天綜合第 ${row.previousCombinedRank} 名`}>
                      {row.rankChange > 0 ? <><i>↑</i><b>{row.rankChange}</b></> : row.rankChange < 0 ? <><i>↓</i><b>{Math.abs(row.rankChange)}</b></> : <b>0</b>}
                    </span>
                    <span className={`chip-judgement ${judgement.className}`}>{judgement.label}</span>
                  </div>
                );
              })}
              {chipRows.length === 0 && (
                <div className="chip-empty">{chipDataStatus === "loading" ? "正在讀取正式盤後籌碼…" : `目前沒有符合「${chipMarketFilter}／${chipSelectionFilter === "bullish" ? "多方雙強" : chipSelectionFilter === "bearish" ? "空方雙弱" : "全部排行"}」的資料`}</div>
              )}
            </div>
          </div>
        ) : (
          <div className={`rank-table ${rankMode === "stocks" ? `stock-rank-table ipad-stock-rank iphone-stock-rank${isDesktop ? " desktop-stock-rank-clean" : ""}` : "group-force-rank-table"}`}>
            {rankMode === "stocks" ? (
              <div className="rank-header stock-rank-header">
                <span>排名</span><span>代號</span><span>名稱</span><span>漲跌幅</span><span>漲跌</span><span>{stockPriceColumnLabel}</span><button type="button" className={`rank-group-sort-button ${stockGroupSort === "group" ? "active" : ""}`} onClick={toggleStockGroupSort} aria-pressed={stockGroupSort === "group"}>所屬族群 <b>{stockGroupSort === "group" ? "群" : "⇅"}</b></button>
                {!isDesktop && <><span>強度</span><span>分數</span></>}
              </div>
            ) : (
              <div className="rank-header"><span>排名</span><span>族群</span><span>漲跌幅</span><span>領漲股</span><span>強度</span></div>
            )}
            {rankMode === "stocks" && (
              <div className="mobile-stock-rank-header">
                <span>排名</span><span>名稱</span><span>代號</span>
                <button type="button" onClick={toggleStockChangeSort} aria-label={`漲跌幅排序，目前${stockChangeSort === "asc" ? "升冪" : stockChangeSort === "desc" ? "降冪" : "預設"}`}>
                  漲跌幅 <b>{stockChangeSort === "asc" ? "▲" : stockChangeSort === "desc" ? "▼" : "⇅"}</b>
                </button>
                <span>漲跌</span><span>{stockPriceColumnLabel}</span><button type="button" className={`rank-group-sort-button ${stockGroupSort === "group" ? "active" : ""}`} onClick={toggleStockGroupSort} aria-pressed={stockGroupSort === "group"} aria-label={`所屬族群排列，目前${stockGroupSort === "group" ? "同族群集中" : "原強弱排名"}`}>所屬族群 <b>{stockGroupSort === "group" ? "群" : "⇅"}</b></button>
              </div>
            )}
            {isDesktop && displayedRows.length === 0 && <div className="stock-ranking-loading">正在讀取最新前二十名排行…</div>}
            {displayedRows.map((row) => {
              const clickable = row.name.match(/^\d{4}/) || row.lead.match(/^\d{4}/);
              const isGroupRow = bottomMode === "ranking" && rankMode === "groups";
              const ticker = rankMode === "stocks" ? rankStockTicker(row.name) : "";
              const rankQuote = ticker ? stockRankQuotes[ticker] ?? stockRankQuotes[`${stockRankExchange(ticker)}:${ticker}`] : undefined;
              const rankChangePercent = rankQuote?.changePct ?? Number.parseFloat(row.change);
              const rankTone = rankChangePercent < 0 ? "negative" : rankChangePercent > 0 ? "positive" : "neutral";
              const afterHoursForce = ticker ? rankingAfterHoursForceByTicker.get(ticker) : undefined;
              return (
                <button
                  className={`rank-row ${rankMode === "stocks" ? "stock-rank-row" : ""}`}
                  key={`${bottomMode}-${rankMode}-${row.name}`}
                  onClick={() => isGroupRow ? setSelectedGroup(row.name) : clickable && openOriginalKline((row.name.match(/^\d{4}/) ? ticker : row.lead))}
                  aria-label={isGroupRow ? `查看 ${row.name} 全部個股` : clickable ? `開啟 ${(row.name.match(/^\d{4}/) ? row.name : row.lead)} 完整 K 線` : undefined}
                  title={isGroupRow ? `查看 ${row.name} 即時個股清單` : clickable ? "開啟原始版完整 K 線（預設五分 K，可切換週期）" : undefined}
                >
                  {rankMode === "stocks" ? <>
                    <span className="rank-number">{row.rank.toString().padStart(2, "0")}</span>
                    <span className="rank-ticker">{ticker}</span>
                    <span className="rank-name"><strong>{rankStockName(row.name)}</strong><StockTradingBadges ticker={ticker} compact /><small>{row.leadChange}</small><em className={`ranking-after-hours-force ${afterHoursForce === undefined ? "pending" : afterHoursForce < 0 ? "negative" : afterHoursForce > 0 ? "positive" : "neutral"}`}>盤後大戶力 {afterHoursForce === undefined ? "待補" : formatSigned(afterHoursForce, 1)}</em></span>
                    <span className={`rank-change ${rankTone}`}>{rankQuote?.changePct == null ? row.change : formatSigned(rankQuote.changePct, 2, "%")}</span>
                    <span className={`rank-price-change ${rankTone}`}>{rankQuote?.change == null ? "—" : formatSigned(rankQuote.change, 2)}</span>
                    <span className={`rank-latest-price ${rankTone}`}>{rankQuote?.price == null ? "—" : rankQuote.price.toFixed(2)}</span>
                    <span className="rank-lead">{row.lead}</span>
                    {!isDesktop && <><StrengthGauge score={row.score} /><b className={`rank-score-number ${isWeak ? "negative" : "positive"}`}>{row.score}</b></>}
                  </> : <>
                    <span className="rank-number">{row.rank.toString().padStart(2, "0")}</span>
                    <span className="rank-name"><strong>{row.name}</strong><small>{row.leadChange}</small></span>
                    <span className={`rank-change ${row.change.startsWith("-") ? "negative" : "positive"}`}>{row.change}</span>
                    <span className="rank-lead">{row.lead}</span>
                    <span className="rank-score"><MiniChart down={isWeak} /><b>{row.score}</b></span>
                  </>}
                  {rankMode === "stocks" && <span className="rank-mobile-details" aria-label={`${row.name} 行情資訊`}>
                    <span><small>漲跌幅</small><b className={rankTone}>{rankQuote?.changePct == null ? row.change : formatSigned(rankQuote.changePct, 2, "%")}</b></span>
                    <span><small>漲跌</small><b className={rankTone}>{rankQuote?.change == null ? "—" : formatSigned(rankQuote.change, 2)}</b></span>
                    <span><small>{stockPriceColumnLabel}</small><b className={rankTone}>{rankQuote?.price == null ? "—" : rankQuote.price.toFixed(2)}</b></span>
                    <span><small>所屬族群</small><b>{row.lead}</b></span>
                    <span><small>盤後大戶力</small><b className={afterHoursForce === undefined ? "pending" : afterHoursForce < 0 ? "negative" : afterHoursForce > 0 ? "positive" : "neutral"}>{afterHoursForce === undefined ? "待補" : formatSigned(afterHoursForce, 1)}</b></span>
                  </span>}
                  {rankMode === "stocks" && <span className="rank-device-line" aria-label={`${row.name} 單行行情`}>
                    <b>{row.rank}</b>
                    <strong>{rankStockName(row.name)}</strong>
                    <span>{ticker}</span>
                    <b className={rankTone}>{rankQuote?.changePct == null ? row.change : formatSigned(rankQuote.changePct, 2, "%")}</b>
                    <b className={rankTone}>{rankQuote?.change == null ? "—" : formatSigned(rankQuote.change, 2)}</b>
                    <b className={rankTone}>{rankQuote?.price == null ? "—" : rankQuote.price.toFixed(2)}</b>
                    <span>{row.lead}</span>
                  </span>}
                </button>
              );
            })}
          </div>
        )}
        {bottomMode === "ranking" && rankMode === "stocks" && !isWeak && !isDesktop && <HighDividendEtfPanel onOpenKline={openKlineByTicker} />}
      </section>}

      {bottomMode === "chips" && (
        <section className="group-chip-rankings" aria-label="盤後族群籌碼強弱排行">
          <header>
            <div><span className="eyebrow">GROUP CHIP STRENGTH</span><h2>族群籌碼強弱排行</h2><p>盤中隨時查看最新完整盤後籌碼，依目前權重彙整各族群個股分數。</p></div>
            <div className={`group-chip-update-state ${chipDataIsCurrent ? "is-ready" : "is-waiting"}`} aria-live="polite">
              <span><i />{chipAutomaticUpdate.label}</span>
              <strong>資料日 {officialChipData?.dataDate ?? "—"}</strong>
              <small>{chipDataIsCurrent ? "最新更新" : "最新檢查"} {formatTaipeiDateTime(chipUpdatedAt)}</small>
              <small>涵蓋 {groupChipRankings.length || "—"} 個族群</small>
            </div>
          </header>
          <div className="group-chip-ranking-grid">
            <article className="group-chip-side is-bullish">
              <header><div><span>多方</span><h3>籌碼增強前十族群</h3></div><b>由強到弱</b></header>
              <div className="group-chip-table-scroll" role="region" aria-label="多方族群籌碼排行" tabIndex={0}>
                <div className="group-chip-table">
                  <div className="group-chip-row is-head"><span>排名</span><span>族群</span><span>最新分數</span><span>五日趨勢</span><span>多／空家數</span><span>涵蓋</span></div>
                  {groupChipStrongRows.map((row, index) => <div className="group-chip-row" key={`chips-bullish-group-${row.name}`}><span>{index + 1}</span><button type="button" onClick={() => setSelectedGroup(row.name)}>{row.name}<i>›</i></button><strong className="positive">{formatSigned(row.latestScore, 1)}</strong><FiveDayTrendPreview code={`chips-bull-${index}`} name={row.name} value={row.fiveDayScore} series={row.trendSeries} /><b>{row.positiveCount}／{row.negativeCount}</b><small>{row.coveredCount}／{row.memberCount}</small></div>)}
                  {groupChipStrongRows.length === 0 && <div className="group-chip-loading">{groupChipRankings.length ? "目前沒有分數大於 0 的多方族群" : "正在計算多方族群籌碼排行…"}</div>}
                </div>
              </div>
            </article>
            <article className="group-chip-side is-bearish">
              <header><div><span>空方</span><h3>籌碼轉弱前十族群</h3></div><b>由弱到強</b></header>
              <div className="group-chip-table-scroll" role="region" aria-label="空方族群籌碼排行" tabIndex={0}>
                <div className="group-chip-table">
                  <div className="group-chip-row is-head"><span>排名</span><span>族群</span><span>最新分數</span><span>五日趨勢</span><span>多／空家數</span><span>涵蓋</span></div>
                  {groupChipWeakRows.map((row, index) => <div className="group-chip-row" key={`chips-bearish-group-${row.name}`}><span>{index + 1}</span><button type="button" onClick={() => setSelectedGroup(row.name)}>{row.name}<i>›</i></button><strong className="negative">{formatSigned(row.latestScore, 1)}</strong><FiveDayTrendPreview code={`chips-bear-${index}`} name={row.name} value={row.fiveDayScore} series={row.trendSeries} /><b>{row.positiveCount}／{row.negativeCount}</b><small>{row.coveredCount}／{row.memberCount}</small></div>)}
                  {groupChipWeakRows.length === 0 && <div className="group-chip-loading">{groupChipRankings.length ? "目前沒有分數小於 0 的空方族群" : "正在計算空方族群籌碼排行…"}</div>}
                </div>
              </div>
            </article>
          </div>
          <footer>點族群名稱可查看該族群全部個股；紅色代表籌碼偏多，綠色代表籌碼偏空。</footer>
        </section>
      )}

      {selectedGroup && createPortal(
        <div className="group-dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setSelectedGroup(null)}>
          <section className="group-dialog" role="dialog" aria-modal="true" aria-labelledby="group-dialog-title">
            <header className="group-dialog-head">
              <div>
                <span className="eyebrow">GROUP LIVE QUOTES</span>
                <h2 id="group-dialog-title">{selectedGroup}族群全部個股</h2>
                <p>{selectedGroupStocks.length ? `共 ${selectedGroupStocks.length} 檔` : "正在載入完整族群名單…"} · {groupStocksLoading ? "即時行情更新中" : (loadedGroupPriceType ?? "即時行情已更新")} · {loadedGroupRetrievedAt ? `行情取得 ${formatTaipeiDateTime(loadedGroupRetrievedAt)}` : "行情時間待確認"} · 點代號或名稱開啟完整 K 線</p>
              </div>
              <div className="group-dialog-summary">
                <span>族群平均漲跌幅</span>
                <strong className={selectedGroupTone}>{selectedGroupChange ?? "—"}</strong>
              </div>
              <button className="dialog-close" onClick={() => setSelectedGroup(null)} aria-label="關閉族群個股清單">×</button>
            </header>

            <div className="group-dialog-legend" role="note">
              <span><b>＊</b> 代表目前處置中股票；處置狀況會顯示距出關日或出關第幾天。</span>
              <span>大單淨額資金占比（隔日沖占比）＝大單淨額 ÷ 個股當日成交金額。{groupMemberFlowStatus?.dataDate ? `資料日 ${groupMemberFlowStatus.dataDate.replaceAll("-", "/")}｜${groupMemberFlowStatus.completed}/${groupMemberFlowStatus.requested} 檔完成` : ""}</span>
            </div>

            <div className="group-stock-table">
              <div className="group-stock-header"><span>代號</span><span>名稱</span><span>處置狀況</span><span>大單淨額資金占比<br />（隔日沖占比）</span><span>漲跌幅</span><span>漲跌</span><span>成交價</span></div>
              {selectedGroupStocks.map((stock) => {
                const memberMeta = groupMemberMetaByTicker[stock.ticker];
                const disposition = memberMeta ?? {
                  dispositionLabel: groupMemberMetaLoading ? "讀取中" : "非處置股",
                  dispositionTone: "notice" as const,
                  isActiveDisposition: false,
                  netFundingLabel: groupMemberMetaLoading ? "讀取中" : "逐筆待回補",
                  netFundingValue: null,
                  netFundingDataDate: null,
                  netFundingStatus: "force_pending" as const,
                };
                const quoteChangeValue = Number.parseFloat(stock.change);
                const quoteTone = !Number.isFinite(quoteChangeValue) || quoteChangeValue === 0
                  ? "neutral"
                  : quoteChangeValue < 0 ? "negative" : "positive";
                const fundingTone = disposition.netFundingValue === null || disposition.netFundingValue === 0
                  ? "neutral"
                  : disposition.netFundingValue < 0 ? "negative" : "positive";
                return (
                  <div className="group-stock-row" key={stock.ticker}>
                    <button className={`group-stock-code ${quoteTone}`} onClick={() => openOriginalKline(stock.ticker, stock.name)} title="開啟原始版完整 K 線">{disposition.isActiveDisposition && <i aria-label="處置中">＊</i>}{stock.ticker}</button>
                    <button className={`group-stock-name ${quoteTone}`} onClick={() => openOriginalKline(stock.ticker, stock.name)} title="開啟原始版完整 K 線"><span>{stock.name}</span><StockTradingBadges ticker={stock.ticker} compact /></button>
                    <span className={`group-stock-disposition is-${disposition.dispositionTone}`}>{disposition.dispositionLabel}</span>
                    <span className={`group-stock-funding ${fundingTone}`} title={disposition.netFundingDataDate ? `大單資料日 ${disposition.netFundingDataDate.replaceAll("-", "/")}` : disposition.netFundingLabel}>{disposition.netFundingLabel}</span>
                    <b className={`group-stock-change ${quoteTone}`}>{stock.change}</b>
                    <b className={`group-stock-delta ${quoteTone}`}>{stock.priceChange ?? "—"}</b>
                    <strong className={`group-stock-price ${quoteTone}`}>{stock.price}</strong>
                  </div>
                );
              })}
            </div>
            <footer className="group-dialog-foot"><i />{groupStocksLoading ? `先顯示 ${selectedGroupStocks.length} 檔成分股，行情背景更新中` : `完整顯示 ${selectedGroupStocks.length} 檔個股`}<span>預設開啟五分 K，可切換 1 分、日線等週期</span></footer>
          </section>
        </div>, document.body
      )}

      <Link className="education-entry-banner" href="/education" aria-label="開啟 HanStock 教學中心">
        <span>HANSTOCK LEARNING CENTER</span>
        <strong>名詞解釋、策略算法與功能操作，都整理在教學中心</strong>
        <b>開始查詢 ›</b>
      </Link>

      <footer className="site-disclaimer" aria-label="資料使用聲明">
        本站內容係依公開市場資料彙整與統計，僅供資訊查詢及研究參考，不代表任何投資建議或獲利保證。投資人應自行評估並承擔交易風險與盈虧。
      </footer>

      <nav className="bottom-nav" aria-label="主要選單">
        <button className={bottomMode === "watchlist" ? "active" : ""} onClick={() => selectBottomMode("watchlist")}><strong>自選股</strong></button>
        <button className={bottomMode === "triangles" ? "active" : ""} onClick={() => selectBottomMode("triangles")}><strong>三角收斂</strong></button>
        <button className={bottomMode === "daytrade" ? "active" : ""} onClick={() => selectBottomMode("daytrade")}><span>沖</span><strong>疑似隔日沖大單籌碼</strong></button>
        <button className={bottomMode === "stock-analysis" ? "active" : ""} onClick={() => selectBottomMode("stock-analysis")} title="開啟基本面河流圖、歷史價位位置均線與機構大戶追蹤" aria-label="開啟個股研究中心"><span>本</span><strong>個股研究中心</strong></button>
        <button type="button" className={bottomMode === "weekly-chips" ? "active" : ""} onClick={() => selectBottomMode("weekly-chips")} title="開啟每週五日平均籌碼分析" aria-label="每週籌碼分析"><span>週</span><strong>每週籌碼分析</strong></button>
        <button className={bottomMode === "chips" ? "active" : ""} onClick={() => selectBottomMode("chips")}><span>籌</span><strong>今日盤後籌碼排行</strong></button>
        <button onClick={() => window.location.assign("/stock-screener")}><span>選</span><strong>選股程式</strong></button>
        <button type="button" className={bottomMode === "intraday-tracking" ? "active" : ""} onClick={() => selectBottomMode("intraday-tracking")} title="追蹤自選個股的盤中訊號" aria-label="個股盤中訊號追蹤"><span>追</span><strong>個股盤中訊號追蹤</strong></button>
      </nav>
    </main>
  );
}

const ClientOnlyBattleHome = dynamic(
  () => Promise.resolve(BattleHome),
  {
    ssr: false,
    loading: () => (
      <main className="battle-startup" aria-live="polite" aria-busy="true">
        <strong>HanStock 盤中戰鬥版</strong>
        <span>系統載入中…</span>
      </main>
    ),
  },
);

export default function Home() {
  return <ClientOnlyBattleHome />;
}
