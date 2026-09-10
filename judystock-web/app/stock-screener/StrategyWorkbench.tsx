"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import type { ResultRow } from "./page";
import StockTradingBadges from "../StockTradingBadges";
import { BLACK_DRAGON_MODEL_VERSION, buildBlackDragonRows, type BlackDragonRow, type BlackDragonSourceRow } from "../../lib/black-dragon";
import BlackDragonEvidence from "./BlackDragonEvidence";

type RuleKey = "today" | "threeDay" | "fiveDay" | "combined" | "acceleration" | "surge" | "strongDays" | "trustDays" | "maScore" | "candle" | "price" | "changePct" | "bullish" | "bearish" | "triangle" | "vcp" | "blackDragon" | "avoidRisk";
type ScoreRuleKey = Extract<RuleKey, "today" | "threeDay" | "fiveDay" | "combined" | "acceleration" | "surge" | "strongDays" | "trustDays" | "maScore">;
type Comparator = "min" | "max";
type CandleMode = "red" | "black";
type Market = "全部" | "上市" | "上櫃" | "ETF";
type SortKey = "code" | "name" | "group" | "market" | "price" | "changePct" | "today" | "threeDay" | "fiveDay" | "surge" | "strongDays" | "trustDays" | "combined" | "judgement";
type Direction = "asc" | "desc";
type VcpStatus = "全部VCP" | "VCP形成中" | "接近突破" | "今日帶量突破" | "突破後過熱";
type VcpDataState = "loading" | "ready" | "unavailable";
type VcpCandidate = {
  status: string;
  pivotPrice: number | null;
  distancePct: number | null;
  name: string;
  close: number | null;
  market: "上市" | "上櫃" | null;
};
type BlackDragonCandidate = Omit<BlackDragonRow, "market"> & {
  market: "上市" | "上櫃";
};

type Recipe = {
  name: string;
  desc: string;
  keys: RuleKey[];
  thresholds?: Partial<Record<ScoreRuleKey, number>>;
  changeRange?: [number, number];
};

type TechnicalRow = {
  code: string;
  maScore: number | null;
  aboveMa5: boolean | null;
  aboveMa10: boolean | null;
  aboveMa20: boolean | null;
  technicalReady?: boolean;
  candle: "red" | "black" | "flat";
  changePct: number;
};

type RiskPayload = {
  suspects?: Array<{ code: string }>;
  dispositions?: Array<{ code: string; status: string }>;
};

type ExRightRow = { code: string; date: string; type: string };

type TechnicalWarmCache = {
  savedAt: number;
  dataDate: string;
  updatedAt: string;
  rows: TechnicalRow[];
};

const TECHNICAL_WARM_CACHE_KEY = "hanstock:stock-screener:technical:v1";
const TECHNICAL_WARM_CACHE_MAX_AGE = 7 * 24 * 60 * 60_000;

function readTechnicalWarmCache() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(TECHNICAL_WARM_CACHE_KEY) ?? "null") as TechnicalWarmCache | null;
    if (!parsed || Date.now() - parsed.savedAt > TECHNICAL_WARM_CACHE_MAX_AGE || !Array.isArray(parsed.rows) || parsed.rows.length === 0) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeTechnicalWarmCache(payload: TechnicalWarmCache) {
  try {
    window.localStorage.setItem(TECHNICAL_WARM_CACHE_KEY, JSON.stringify(payload));
  } catch {
    // The fast API remains available when browser storage is unavailable.
  }
}

const ruleCards: Array<{
  key: RuleKey;
  group: string;
  title: string;
  note: string;
  min?: number;
  max?: number;
  unit?: string;
}> = [
  { key: "today", group: "籌碼面", title: "今日籌碼強度", note: "使用戰鬥版同一份今天盤後籌碼分數。", min: -100, max: 100, unit: "分" },
  { key: "threeDay", group: "法人面", title: "法人三日強度", note: "把三大法人近 3 日籌碼合併計分，觀察短線是否同步偏多。", min: -100, max: 100, unit: "分" },
  { key: "fiveDay", group: "籌碼面", title: "五日籌碼延續", note: "使用近五日平均盤後籌碼分數，觀察延續性。", min: -100, max: 100, unit: "分" },
  { key: "combined", group: "綜合面", title: "綜合籌碼分數", note: "今天 60% 加上近五日平均 40%，與戰鬥版完全共用。", min: -100, max: 100, unit: "分" },
  { key: "acceleration", group: "變化面", title: "籌碼加速度", note: "今天分數減去五日平均，找出正在快速轉強或轉弱的股票。", min: -200, max: 200, unit: "分" },
  { key: "surge", group: "新增雷達", title: "單日籌碼分數跳升", note: "比較今天與前一交易日籌碼分數，找出單日突然跳升的股票。", min: -200, max: 200, unit: "分" },
  { key: "strongDays", group: "法人面", title: "法人連強日數", note: "連續多日法人合力分數偏多，適合搭配三日與五日強度。", min: 1, max: 6, unit: "日" },
  { key: "trustDays", group: "法人面", title: "投信連強日數", note: "投信分數連續偏多的交易日數，觀察投信是否持續認養。", min: 1, max: 6, unit: "日" },
  { key: "maScore", group: "技術面", title: "六均線排列分數", note: "MA5、10、20、60、120、240 兩兩比較，共 15 組；排列越完整分數越高。", min: 0, max: 15, unit: "分" },
  { key: "candle", group: "技術面", title: "自訂紅 K／黑 K", note: "可選紅 K 或黑 K，再指定當日漲跌幅範圍。" },
  { key: "price", group: "價格面", title: "股價區間", note: "只保留現價落在指定範圍內的股票。" },
  { key: "changePct", group: "價格面", title: "漲跌幅區間", note: "依最新行情的漲跌幅範圍篩選。" },
  { key: "bullish", group: "快速判斷", title: "多方雙強", note: "今天分數至少 60，且五日平均至少 20。" },
  { key: "bearish", group: "快速判斷", title: "空方雙弱", note: "今天分數至多 -60，且五日平均至多 -20。" },
  { key: "triangle", group: "型態面", title: "三角收斂候選", note: "只保留 HanStock 日線三角收斂正式掃描名單。" },
  { key: "vcp", group: "型態面", title: "VCP 波動收縮", note: "高檔回檔與量能逐次收縮，可選形成中、接近突破、帶量突破或過熱。" },
  { key: "blackDragon", group: "型態面", title: "創高黑龍", note: "只限 HanStock 正式 67 族群；最近 5 個完成交易日內，當日最高價須高於前五個交易日最高點（不含當日、不含平高），同日收黑 K，且六均線排列至少 10 分。" },
  { key: "avoidRisk", group: "風險面", title: "排除處置風險", note: "排除疑似處置、處置中與即將處置的股票。" },
];

const recipes: Recipe[] = [
  { name: "法人認養", desc: "法人 3 日、5 日都偏多且股價 200 元內", keys: ["threeDay", "fiveDay", "price"], thresholds: { threeDay: 10, fiveDay: 10 } },
  { name: "籌碼分數跳升", desc: "單日籌碼分數跳升且綜合分數偏多", keys: ["surge", "combined"], thresholds: { surge: 25, combined: 10 } },
  { name: "均線強勢", desc: "六均線多頭排列至少 10 分", keys: ["maScore"], thresholds: { maScore: 10 } },
  { name: "強勢紅 K", desc: "紅 K 且當日漲幅為正", keys: ["candle"], changeRange: [0, 10] },
  { name: "多方雙強", desc: "今天與五日籌碼同步強", keys: ["bullish"] },
  { name: "今日點火", desc: "今日強度與加速度同時轉強", keys: ["today", "acceleration"], thresholds: { today: 40, acceleration: 20 } },
  { name: "五日延續", desc: "五日平均與綜合分數偏多", keys: ["fiveDay", "combined"], thresholds: { fiveDay: 20, combined: 20 } },
  { name: "偏多拉回", desc: "籌碼偏多且當日小幅拉回", keys: ["combined", "changePct"], thresholds: { combined: 20 }, changeRange: [-3, 0] },
  { name: "空方雙弱", desc: "今天與五日籌碼同步弱", keys: ["bearish"] },
  { name: "VCP 接近突破", desc: "波動與量能收縮，價格靠近樞紐點", keys: ["vcp"] },
  { name: "創高黑龍", desc: "正式 67 族群內最高價創高、同日黑 K、均線至少 10 分", keys: ["blackDragon"] },
];

const initialThresholds: Record<ScoreRuleKey, number> = {
  today: 20,
  threeDay: 10,
  fiveDay: 10,
  combined: 20,
  acceleration: 10,
  surge: 20,
  strongDays: 2,
  trustDays: 2,
  maScore: 10,
};

const initialComparators: Record<ScoreRuleKey, Comparator> = {
  today: "min",
  threeDay: "min",
  fiveDay: "min",
  combined: "min",
  acceleration: "min",
  surge: "min",
  strongDays: "min",
  trustDays: "min",
  maScore: "min",
};

function format(value: number | null, digits = 1, suffix = "") {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
}

function formatPrice(value: number | null) {
  return value === null || !Number.isFinite(value) ? "—" : value.toFixed(2);
}

function formatUpdateTime(value: string) {
  if (!value || value === "—") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function passes(value: number, comparator: Comparator, threshold: number) {
  return comparator === "min" ? value >= threshold : value <= threshold;
}

export default function StrategyWorkbench({ rows, loading, dataDate, quoteDataDate, updatedAt }: { rows: ResultRow[]; loading: boolean; dataDate: string; quoteDataDate: string; updatedAt: string }) {
  const [enabled, setEnabled] = useState<Set<RuleKey>>(new Set(["today", "combined"]));
  const [thresholds, setThresholds] = useState(initialThresholds);
  const [comparators, setComparators] = useState(initialComparators);
  const [priceRange, setPriceRange] = useState<[number, number]>([0, 5000]);
  const [changeRange, setChangeRange] = useState<[number, number]>([-10, 10]);
  const [candleRange, setCandleRange] = useState<[number, number]>([-10, 10]);
  const [candleMode, setCandleMode] = useState<CandleMode>("red");
  const [market, setMarket] = useState<Market>("全部");
  const [group, setGroup] = useState("全部族群");
  const [ran, setRan] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: Direction }>({ key: "combined", direction: "desc" });
  const [technical, setTechnical] = useState<Record<string, TechnicalRow>>({});
  const [technicalLoading, setTechnicalLoading] = useState(false);
  const [technicalRefreshAt, setTechnicalRefreshAt] = useState(0);
  const [technicalDataDate, setTechnicalDataDate] = useState("—");
  const [technicalUpdatedAt, setTechnicalUpdatedAt] = useState("—");
  const [riskCodes, setRiskCodes] = useState<Set<string>>(new Set());
  const [triangleCodes, setTriangleCodes] = useState<Set<string>>(new Set());
  const [vcpRows, setVcpRows] = useState<Record<string, VcpCandidate>>({});
  const [vcpStatus, setVcpStatus] = useState<VcpStatus>("接近突破");
  const [vcpDataState, setVcpDataState] = useState<VcpDataState>("loading");
  const [vcpMessage, setVcpMessage] = useState("正在取得 VCP 正式掃描名單…");
  const [blackDragonRows, setBlackDragonRows] = useState<Record<string, BlackDragonCandidate>>({});
  const [blackDragonDataState, setBlackDragonDataState] = useState<VcpDataState>("loading");
  const [blackDragonMessage, setBlackDragonMessage] = useState("正在取得創高黑龍正式名單…");
  const [exRights, setExRights] = useState<Record<string, ExRightRow>>({});

  useEffect(() => {
    const cached = readTechnicalWarmCache();
    if (!cached) return;
    queueMicrotask(() => {
      setTechnical(Object.fromEntries(cached.rows.map((row) => [row.code, row])));
      setTechnicalDataDate(cached.dataDate);
      setTechnicalUpdatedAt(cached.updatedAt);
      setTechnicalRefreshAt(cached.savedAt + 30 * 60_000);
    });
  }, []);

  useEffect(() => {
    if (!enabled.has("avoidRisk") || riskCodes.size > 0) return;
    const controller = new AbortController();
    void fetch("/api/disposition-risk", { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<RiskPayload> : null)
      .then((payload) => {
        if (!payload || controller.signal.aborted) return;
        const codes = new Set<string>();
        payload.suspects?.forEach((row) => codes.add(row.code));
        payload.dispositions?.filter((row) => row.status === "處置中" || row.status === "即將處置").forEach((row) => codes.add(row.code));
        setRiskCodes(codes);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [enabled, riskCodes.size]);

  useEffect(() => {
    if (!enabled.has("triangle") || triangleCodes.size > 0) return;
    const controller = new AbortController();
    void fetch("/api/triangles", { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ rows?: Array<Record<string, unknown>> }> : null)
      .then((payload) => {
        if (!payload || controller.signal.aborted) return;
        setTriangleCodes(new Set((payload.rows ?? []).map((row) => String(row.stock_code ?? row.code ?? row.ticker ?? "").trim().toUpperCase()).filter(Boolean)));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [enabled, triangleCodes.size]);

  useEffect(() => {
    if (!enabled.has("blackDragon") || blackDragonRows && Object.keys(blackDragonRows).length > 0) return;
    const controller = new AbortController();
    setBlackDragonDataState("loading");
    setBlackDragonMessage("正在取得創高黑龍正式名單…");
    void fetch("/api/black-dragon", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json() as { ok?: boolean; modelVersion?: string; rows?: BlackDragonSourceRow[]; coverage?: { missing?: number } };
        if (!response.ok || payload.ok !== true || payload.modelVersion !== BLACK_DRAGON_MODEL_VERSION || !Array.isArray(payload.rows) || Number(payload.coverage?.missing ?? 1) !== 0) {
          throw new Error("創高黑龍正式名單尚未完成");
        }
        return payload;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        const candidates = Object.fromEntries(buildBlackDragonRows(payload.rows ?? []).map((row) => [row.code, {
          ...row, market: row.market === "twse" ? "上市" : "上櫃",
        } satisfies BlackDragonCandidate]));
        setBlackDragonRows(candidates);
        setBlackDragonDataState("ready");
        setBlackDragonMessage(`創高黑龍正式名單已就緒，共 ${Object.keys(candidates).length.toLocaleString("zh-TW")} 檔`);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setBlackDragonDataState("unavailable");
        setBlackDragonMessage(error instanceof Error ? error.message : "創高黑龍正式名單尚未完成");
      });
    return () => controller.abort();
  }, [enabled, blackDragonRows]);


  useEffect(() => {
    if (!enabled.has("vcp")) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scheduleRetry = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = undefined;
        if (document.visibilityState === "visible") void loadVcp();
      }, 30_000);
    };
    const loadVcp = async () => {
      try {
        const response = await fetch("/api/vcp", { cache: "no-store", signal: controller.signal });
        const payload = await response.json() as { ok?: boolean; rows?: Array<Record<string, unknown>>; message?: string };
        if (!response.ok || payload.ok !== true || !Array.isArray(payload.rows)) {
          throw new Error(payload.message || "VCP 正式名單尚未產生");
        }
        setVcpRows(Object.fromEntries(payload.rows.map((row) => {
          const code = String(row.stock_code ?? row.code ?? row.ticker ?? "").trim().toUpperCase();
          return [code, {
            status: String(row.status ?? "VCP形成中"),
            pivotPrice: Number.isFinite(Number(row.pivot_price)) ? Number(row.pivot_price) : null,
            distancePct: Number.isFinite(Number(row.distance_to_pivot_pct)) ? Number(row.distance_to_pivot_pct) : null,
            name: String(row.stock_name ?? row.name ?? code),
            close: Number.isFinite(Number(row.close)) ? Number(row.close) : null,
            market: row.market === "上市" || row.market === "上櫃" ? row.market : null,
          }];
        }).filter(([code]) => Boolean(code))));
        setVcpDataState("ready");
        setVcpMessage(`VCP 正式名單已就緒，共 ${payload.rows.length.toLocaleString("zh-TW")} 檔`);
      } catch (error) {
        if (controller.signal.aborted) return;
        setVcpDataState("unavailable");
        setVcpMessage(error instanceof Error ? error.message : "VCP 正式名單尚未產生");
        scheduleRetry();
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (timer) { clearTimeout(timer); timer = undefined; }
        void loadVcp();
      }
    };
    void loadVcp();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  const groups = useMemo(
    () => [...new Set(rows.map((row) => row.group))].sort((a, b) => a.localeCompare(b, "zh-TW")),
    [rows],
  );
  const activeNames = useMemo(
    () => ruleCards.filter((rule) => enabled.has(rule.key)).map((rule) => rule.title),
    [enabled],
  );
  const selectedVcpCount = useMemo(
    () => Object.values(vcpRows).filter((row) => vcpStatus === "全部VCP" || row.status === vcpStatus).length,
    [vcpRows, vcpStatus],
  );

  const toggle = (key: RuleKey) => {
    setEnabled((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else {
        // 使用者第一次啟用 VCP 時先切成純 VCP，避免頁面預設的籌碼
        // 條件仍留著，將 38／42 檔誤篩成個位數。需要複合選股時，
        // 仍可在啟用 VCP 後再自行加回其他條件。
        if (key === "vcp" || key === "blackDragon") return new Set<RuleKey>([key]);
        next.add(key);
        if (key === "bullish") next.delete("bearish");
        if (key === "bearish") next.delete("bullish");
      }
      return next;
    });
    setRan(false);
  };

  const applyRecipe = (recipe: Recipe) => {
    setEnabled(new Set(recipe.keys));
    setThresholds({ ...initialThresholds, ...recipe.thresholds });
    setComparators(initialComparators);
    setChangeRange(recipe.changeRange ?? [-10, 10]);
    setCandleRange(recipe.changeRange ?? [-10, 10]);
    setCandleMode("red");
    setPriceRange(recipe.name === "法人認養" ? [0, 200] : [0, 5000]);
    setMarket("全部");
    setGroup("全部族群");
    setRan(false);
  };

  const results = useMemo(() => {
    if (!ran) return [];
    // 單獨使用型態名單（或只再加排除風險）時，以完整正式名單為母體。
    // 若另勾法人／籌碼條件，才維持原本必須具備籌碼序列的母名單。
    const patternOnly = (enabled.has("vcp") || enabled.has("blackDragon"))
      && [...enabled].every((key) => key === "vcp" || key === "blackDragon" || key === "avoidRisk");
    const patternCandidates = new Map<string, { name: string; close: number | null; market: "上市" | "上櫃"; judgement: string }>();
    if (enabled.has("vcp")) Object.entries(vcpRows).forEach(([code, candidate]) => {
      if (candidate.market) patternCandidates.set(code, {
        name: candidate.name,
        close: candidate.close,
        market: candidate.market,
        judgement: "VCP 型態",
      });
    });
    if (enabled.has("blackDragon")) Object.entries(blackDragonRows).forEach(([code, candidate]) => {
      patternCandidates.set(code, { ...candidate, judgement: "創高黑龍" });
    });
    const scanRows = patternOnly
      ? [...rows, ...[...patternCandidates.entries()].flatMap(([code, candidate]): ResultRow[] => {
          if (rows.some((row) => row.code === code)) return [];
          return [{
            code,
            name: candidate.name,
            group: "未分類",
            market: candidate.market,
            exchange: candidate.market === "上市" ? "twse" : "tpex",
            price: candidate.close,
            changePct: null,
            today: 0,
            threeDay: 0,
            fiveDay: 0,
            surge: 0,
            strongDays: 0,
            trustDays: 0,
            combined: 0,
            judgement: candidate.judgement,
          }];
        })]
      : rows;
    const filtered = scanRows.map((row) => enabled.has("blackDragon") && blackDragonRows[row.code]?.groupName
      ? { ...row, group: blackDragonRows[row.code].groupName! } : row).filter((row) => {
      const technicalRow = technical[row.code];
      if (market !== "全部" && row.market !== market) return false;
      if (group !== "全部族群" && row.group !== group) return false;
      if (enabled.has("today") && !passes(row.today, comparators.today, thresholds.today)) return false;
      if (enabled.has("threeDay") && !passes(row.threeDay, comparators.threeDay, thresholds.threeDay)) return false;
      if (enabled.has("fiveDay") && !passes(row.fiveDay, comparators.fiveDay, thresholds.fiveDay)) return false;
      if (enabled.has("combined") && !passes(row.combined, comparators.combined, thresholds.combined)) return false;
      if (enabled.has("acceleration") && !passes(row.today - row.fiveDay, comparators.acceleration, thresholds.acceleration)) return false;
      if (enabled.has("surge") && !passes(row.surge, comparators.surge, thresholds.surge)) return false;
      if (enabled.has("strongDays") && !passes(row.strongDays, comparators.strongDays, thresholds.strongDays)) return false;
      if (enabled.has("trustDays") && !passes(row.trustDays, comparators.trustDays, thresholds.trustDays)) return false;
      if (enabled.has("maScore") && (!technicalRow?.technicalReady || technicalRow.maScore === null || !passes(technicalRow.maScore, comparators.maScore, thresholds.maScore))) return false;
      if (enabled.has("candle") && (!technicalRow || technicalRow.candle !== candleMode || technicalRow.changePct < candleRange[0] || technicalRow.changePct > candleRange[1])) return false;
      if (enabled.has("price") && (row.price === null || row.price < priceRange[0] || row.price > priceRange[1])) return false;
      if (enabled.has("changePct") && (row.changePct === null || row.changePct < changeRange[0] || row.changePct > changeRange[1])) return false;
      if (enabled.has("bullish") && row.judgement !== "多方雙強") return false;
      if (enabled.has("bearish") && row.judgement !== "空方雙弱") return false;
      if (enabled.has("triangle") && !triangleCodes.has(row.code)) return false;
      if (enabled.has("vcp") && (!vcpRows[row.code] || (vcpStatus !== "全部VCP" && vcpRows[row.code].status !== vcpStatus))) return false;
      if (enabled.has("blackDragon") && !blackDragonRows[row.code]) return false;
      if (enabled.has("avoidRisk") && riskCodes.has(row.code)) return false;
      return true;
    });

    return filtered.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (typeof av === "number" || typeof bv === "number") {
        const an = typeof av === "number" ? av : Number.NEGATIVE_INFINITY;
        const bn = typeof bv === "number" ? bv : Number.NEGATIVE_INFINITY;
        return sort.direction === "asc" ? an - bn : bn - an;
      }
      return sort.direction === "asc"
        ? String(av).localeCompare(String(bv), "zh-TW")
        : String(bv).localeCompare(String(av), "zh-TW");
    });
  }, [ran, rows, market, group, enabled, thresholds, comparators, priceRange, changeRange, candleRange, candleMode, technical, riskCodes, triangleCodes, vcpRows, vcpStatus, blackDragonRows, sort]);

  useEffect(() => {
    if (!ran || !results.length || Object.keys(exRights).length > 0) return;
    const controller = new AbortController();
    void fetch("/api/ex-rights", { cache: "no-store", signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ rows?: ExRightRow[] }> : null)
      .then((payload) => {
        if (!payload || controller.signal.aborted) return;
        setExRights(Object.fromEntries((payload.rows ?? []).map((row) => [row.code, row])));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [ran, results.length, exRights]);

  const refreshTechnical = async (blocking: boolean) => {
    if (!blocking && technicalRefreshAt > Date.now()) return true;
    if (blocking) setTechnicalLoading(true);
    try {
      const response = await fetch("/api/technical-market?fast=1&view=stock-screener-v1", { cache: "default" });
      if (!response.ok) return false;
      const payload = await response.json() as { rows?: TechnicalRow[]; dataDate?: string; updatedAt?: string };
      const nextRows = payload.rows ?? [];
      if (!nextRows.length) return false;
      setTechnical(Object.fromEntries(nextRows.map((row) => [row.code, row])));
      setTechnicalDataDate(payload.dataDate ?? "—");
      setTechnicalUpdatedAt(payload.updatedAt ?? "—");
      setTechnicalRefreshAt(Date.now() + 30 * 60_000);
      writeTechnicalWarmCache({ savedAt: Date.now(), dataDate: payload.dataDate ?? "—", updatedAt: payload.updatedAt ?? "—", rows: nextRows });
      return true;
    } finally {
      if (blocking) setTechnicalLoading(false);
    }
  };

  const runScan = async () => {
    if (enabled.has("vcp") && vcpDataState !== "ready") return;
    if (enabled.has("blackDragon") && blackDragonDataState !== "ready") return;
    const needsTechnical = enabled.has("maScore") || enabled.has("candle") || enabled.has("price") || enabled.has("changePct");
    if (needsTechnical) {
      if (Object.keys(technical).length) {
        setRan(true);
        void refreshTechnical(false);
        return;
      }
      await refreshTechnical(true);
    }
    setRan(true);
  };

  const openKline = (row: ResultRow) => {
    const url = new URL("/kline", window.location.origin);
    url.searchParams.set("ticker", row.code);
    url.searchParams.set("name", row.name);
    url.searchParams.set("interval", "5m");
    url.searchParams.set("returnTo", "/stock-screener");
    if (window.matchMedia("(pointer: coarse)").matches || window.innerWidth <= 820) {
      window.location.assign(url.toString());
    } else {
      window.open(url.toString(), "_blank", "noopener,noreferrer");
    }
  };

  const toggleSort = (key: SortKey) => {
    setSort((current) => current.key === key
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "desc" });
  };

  const arrow = (key: SortKey) => sort.key === key ? (sort.direction === "asc" ? "▲" : "▼") : "↕";
  const displayedUpdatedAt = technicalUpdatedAt !== "—" && new Date(technicalUpdatedAt).getTime() > new Date(updatedAt).getTime()
    ? technicalUpdatedAt
    : updatedAt;
  const setThreshold = (key: ScoreRuleKey, value: number) => {
    setThresholds((current) => ({ ...current, [key]: value }));
    setRan(false);
  };
  const vcpBlocked = enabled.has("vcp") && vcpDataState !== "ready";
  const blackDragonBlocked = enabled.has("blackDragon") && blackDragonDataState !== "ready";
  const patternBlocked = vcpBlocked || blackDragonBlocked;

  return <section className="strategy-lab">
    <header className="strategy-lab-head">
      <div>
        <span>HANSTOCK SIGNAL STUDIO</span>
        <h2>策略工坊</h2>
        <p>所有條件已接正式全市場籌碼、族群與行情；每次執行都會重新產生名單。</p>
      </div>
      <aside>
        <b>正式資料已連線</b>
        <small>{loading ? "正在讀取全市場資料" : `目前可掃描 ${rows.length.toLocaleString("zh-TW")} 檔`}</small>
      </aside>
    </header>

    <div className="strategy-search">
      <div><strong>個股全景查詢</strong><small>輸入股票代號，可直接查看完整 K 線</small></div>
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：2330" inputMode="text" />
      <button onClick={() => {
        const code = query.trim().toUpperCase();
        const row = rows.find((item) => item.code === code);
        if (row) openKline(row);
        else if (code) window.location.assign(`/kline?ticker=${encodeURIComponent(code)}&interval=5m&returnTo=/stock-screener`);
      }}>開啟完整 K 線</button>
    </div>

    <section className="strategy-recipes">
      <header><strong>快速策略卡</strong><span>點一下套用正式條件，可再自行微調</span></header>
      <div>{recipes.map((recipe) => <button key={recipe.name} onClick={() => applyRecipe(recipe)}>
        <i /><strong>{recipe.name}</strong><small>{recipe.desc}</small>
      </button>)}</div>
    </section>

    <div className="strategy-layout">
      <section className="strategy-rules">
        <header><div><strong>條件積木</strong><span>可複選，所有啟用條件必須同時成立</span></div><b>{enabled.size} 項啟用</b></header>
        <div className="strategy-rule-grid">{ruleCards.map((rule) => {
          const scoreKey = (["today", "threeDay", "fiveDay", "combined", "acceleration", "surge", "strongDays", "trustDays", "maScore"] as RuleKey[]).includes(rule.key)
            ? rule.key as ScoreRuleKey
            : null;
          return <article className={enabled.has(rule.key) ? "enabled" : ""} key={rule.key}>
            <label>
              <input type="checkbox" checked={enabled.has(rule.key)} onChange={() => toggle(rule.key)} />
              <span>{rule.group}</span><strong>{rule.title}</strong>
            </label>
            <p>{rule.note}</p>
            {scoreKey && <>
              <div className="strategy-comparator">
                <button className={comparators[scoreKey] === "min" ? "active" : ""} onClick={() => { setComparators((current) => ({ ...current, [scoreKey]: "min" })); setRan(false); }}>至少</button>
                <button className={comparators[scoreKey] === "max" ? "active" : ""} onClick={() => { setComparators((current) => ({ ...current, [scoreKey]: "max" })); setRan(false); }}>至多</button>
              </div>
              <div className="strategy-slider">
                <input type="range" min={rule.min} max={rule.max} value={thresholds[scoreKey]} onChange={(event) => setThreshold(scoreKey, Number(event.target.value))} />
                <b>{thresholds[scoreKey]} {rule.unit}</b>
              </div>
            </>}
            {rule.key === "price" && <div className="strategy-number-range">
              <input type="number" min="0" value={priceRange[0]} onChange={(event) => { setPriceRange([Number(event.target.value), priceRange[1]]); setRan(false); }} />
              <span>到</span>
              <input type="number" min="0" value={priceRange[1]} onChange={(event) => { setPriceRange([priceRange[0], Number(event.target.value)]); setRan(false); }} />
              <small>元</small>
            </div>}
            {rule.key === "changePct" && <div className="strategy-number-range">
              <input type="number" step="0.1" value={changeRange[0]} onChange={(event) => { setChangeRange([Number(event.target.value), changeRange[1]]); setRan(false); }} />
              <span>到</span>
              <input type="number" step="0.1" value={changeRange[1]} onChange={(event) => { setChangeRange([changeRange[0], Number(event.target.value)]); setRan(false); }} />
              <small>%</small>
            </div>}
            {rule.key === "candle" && <div className="candle-builder">
              <div>
                <button className={candleMode === "red" ? "active" : ""} onClick={() => { setCandleMode("red"); setRan(false); }}>紅 K</button>
                <button className={candleMode === "black" ? "active" : ""} onClick={() => { setCandleMode("black"); setRan(false); }}>黑 K</button>
              </div>
              <label>
                <input type="number" step="0.1" value={candleRange[0]} onChange={(event) => { setCandleRange([Number(event.target.value), candleRange[1]]); setRan(false); }} />
                <span>到</span>
                <input type="number" step="0.1" value={candleRange[1]} onChange={(event) => { setCandleRange([candleRange[0], Number(event.target.value)]); setRan(false); }} />
                <small>%</small>
              </label>
            </div>}
            {rule.key === "vcp" && <label className="strategy-vcp-status">
              <span>VCP 階段</span>
              <select value={vcpStatus} onChange={(event) => { setVcpStatus(event.target.value as VcpStatus); setRan(false); }}>
                {(["全部VCP", "VCP形成中", "接近突破", "今日帶量突破", "突破後過熱"] as VcpStatus[]).map((status) => <option key={status}>{status}</option>)}
              </select>
              <small>原始名單 {vcpDataState === "ready" ? `${selectedVcpCount.toLocaleString("zh-TW")} 檔` : "載入中"}</small>
            </label>}
            {rule.key === "blackDragon" && <div className="strategy-vcp-status">
              <span>正式創高黑龍名單</span>
              <small>{blackDragonDataState === "ready" ? `${Object.keys(blackDragonRows).length.toLocaleString("zh-TW")} 檔；每檔沿用最近一次真正符合日` : "載入中"}</small>
            </div>}
          </article>;
        })}</div>
      </section>

      <aside className="strategy-runner">
        <header><span>RUN CONFIGURATION</span><h3>執行設定</h3></header>
        <label><span>市場範圍</span><select value={market} onChange={(event) => { setMarket(event.target.value as Market); setRan(false); }}>
          {(["全部", "上市", "上櫃", "ETF"] as Market[]).map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label><span>族群範圍</span><select value={group} onChange={(event) => { setGroup(event.target.value); setRan(false); }}>
          <option>全部族群</option>{groups.map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <div className="strategy-active"><span>目前條件</span>{activeNames.length
          ? activeNames.map((name) => <b key={name}>{name}</b>)
          : <small>尚未啟用條件</small>}
        </div>
        {enabled.has("vcp") && <div className={`strategy-vcp-data ${vcpDataState}`}>
          <b>{vcpDataState === "ready" ? "VCP 資料已就緒" : vcpDataState === "loading" ? "VCP 資料載入中" : "VCP 名單產生中"}</b>
          <small>{vcpMessage}{vcpDataState === "unavailable" ? "，系統每 30 秒自動重試。" : ""}</small>
          {vcpDataState === "ready" && <small>{vcpStatus}原始名單 {selectedVcpCount.toLocaleString("zh-TW")} 檔{enabled.size > 1 ? `；目前另有 ${enabled.size - 1} 項條件，最後結果會再取交集。` : "；目前為純 VCP 掃描。"}</small>}
        </div>}
        {enabled.has("blackDragon") && <div className={`strategy-vcp-data ${blackDragonDataState}`}>
          <b>{blackDragonDataState === "ready" ? "創高黑龍資料已就緒" : blackDragonDataState === "loading" ? "創高黑龍資料載入中" : "創高黑龍名單尚未完成"}</b>
          <small>{blackDragonMessage}</small>
          {blackDragonDataState === "ready" && <small>與「創高黑龍」專頁共用同一份正式結果，不會另外重算成不同名單。</small>}
        </div>}
        <button className="strategy-run" disabled={loading || technicalLoading || enabled.size === 0 || patternBlocked} onClick={() => void runScan()}>
          {loading ? "正在載入全市場資料…" : technicalLoading ? "正在計算全市場均線…" : vcpBlocked ? "等待 VCP 正式名單…" : blackDragonBlocked ? "等待創高黑龍正式名單…" : "啟動全市場掃描"}
        </button>
        <button className="strategy-clear" onClick={() => { setEnabled(new Set()); setMarket("全部"); setGroup("全部族群"); setRan(false); }}>全部清除</button>
        <p>結果不是示範名單。條件、門檻、市場或族群變更後，重新按一次掃描，系統會用最新正式資料重算。</p>
      </aside>
    </div>

    <section className="strategy-preview">
      <header>
        <div><span>SCREENING RESULTS</span><h3>{ran ? "策略候選名單" : "結果預覽區"}</h3></div>
        <aside className="strategy-preview-meta"><time>最新更新 {formatUpdateTime(displayedUpdatedAt)}</time><b>{ran ? `符合 ${results.length.toLocaleString("zh-TW")} 檔` : "等待執行"}</b><small>籌碼 {dataDate}｜行情 {quoteDataDate}{technicalDataDate !== "—" ? `｜技術 ${technicalDataDate}` : ""}</small></aside>
      </header>
      {enabled.has("blackDragon") && <p className="black-dragon-result-note">入選依據看「符合日」：最近五個完成交易日內曾符合，並非每檔今天都創高或收黑。行情價與籌碼分數仍依上方標示日期顯示；前五日高點不含符合日，平高不算創高。</p>}
      {ran ? <div className="strategy-preview-scroll"><div className={`strategy-preview-table real${enabled.has("blackDragon") ? " with-black-dragon" : ""}`}>
        <div>{([
          ["code", "股票代號"], ["name", "股票名稱"], ["group", "族群"], ["market", "市場"], ["price", enabled.has("blackDragon") ? "行情價" : "價格"],
          ["changePct", enabled.has("blackDragon") ? "行情漲跌幅" : "漲跌幅"], ["today", "今天分數"], ["threeDay", "法人3日"], ["fiveDay", "法人5日"],
          ["surge", "籌碼分數跳升"], ["strongDays", "法人連強"], ["trustDays", "投信連強"], ["combined", "綜合分數"], ["judgement", "判斷"],
        ] as Array<[SortKey, string]>).map(([key, label]) => <Fragment key={key}><button onClick={() => toggleSort(key)}>
          {label}<b>{arrow(key)}</b>
        </button>{key === "name" && enabled.has("blackDragon") && <span>創高黑龍入選依據</span>}</Fragment>)}<span>{enabled.has("blackDragon") ? "符合日均線" : "均線"}</span><span>{enabled.has("blackDragon") ? "符合日 K 棒" : "K 棒"}</span><span>預計除權息日</span></div>
        {results.map((row) => <button key={row.code} onClick={() => openKline(row)}>
          <strong className="strategy-result-code">{row.code}</strong>
          <span className="strategy-result-name">{row.name}<StockTradingBadges ticker={row.code} compact /></span>
          {enabled.has("blackDragon") && <BlackDragonEvidence row={blackDragonRows[row.code]} />}
          <span>{row.group}</span><span>{row.market}</span>
          <strong>{formatPrice(row.price)}</strong>
          <strong className={(row.changePct ?? 0) < 0 ? "negative" : (row.changePct ?? 0) > 0 ? "positive" : "neutral"}>{format(row.changePct, 2, "%")}</strong>
          <strong className={row.today < 0 ? "negative" : "positive"}>{format(row.today)}</strong>
          <strong className={row.threeDay < 0 ? "negative" : "positive"}>{format(row.threeDay)}</strong>
          <strong className={row.fiveDay < 0 ? "negative" : "positive"}>{format(row.fiveDay)}</strong>
          <strong className={row.surge < 0 ? "negative" : "positive"}>{format(row.surge)}</strong>
          <strong>{row.strongDays} 日</strong><strong>{row.trustDays} 日</strong>
          <strong className={row.combined < 0 ? "negative" : "positive"}>{format(row.combined)}</strong>
          <em className={row.judgement === "多方雙強" ? "bull" : row.judgement === "空方雙弱" ? "bear" : ""}>{row.judgement}</em>
          <span>{enabled.has("blackDragon") ? `${blackDragonRows[row.code].maScore}/15` : technical[row.code]?.technicalReady && technical[row.code].maScore !== null ? `${technical[row.code].maScore}/15` : "—"}</span>
          <span className={enabled.has("blackDragon") || technical[row.code]?.candle === "black" ? "negative" : technical[row.code]?.candle === "red" ? "positive" : "neutral"}>{enabled.has("blackDragon") ? "黑 K" : technical[row.code]?.candle === "red" ? "紅 K" : technical[row.code]?.candle === "black" ? "黑 K" : "—"}</span>
          <span title={exRights[row.code]?.type ?? "尚未公告"}>{exRights[row.code]?.date ?? "—"}</span>
        </button>)}
        {results.length === 0 && <p>目前沒有符合全部條件的股票，請放寬門檻或取消部分條件。</p>}
      </div></div> : <p>套用快速策略卡或自行勾選條件，再按「啟動全市場掃描」。</p>}
    </section>
  </section>;
}
