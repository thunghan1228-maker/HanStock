/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { shouldTriggerChipServerRefresh, taipeiMarketClock } from "../lib/chip-auto-refresh";
import { withMarketRequestScope } from "../lib/market-request-scope";
import { loadAfterHoursWatchlist } from "../db/after-hours-watchlist";

interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

const TDCC_AUTO_REFRESH_MS = 10 * 60 * 1_000;
let tdccLastAutoRefreshAt = 0;
let chipLastServerRefreshAt = 0;
let technicalHistoryLastAutoRefreshAt = 0;
let daytradeSignalsLastRefreshAt = 0;
let monthlyRevenueLastRefreshAt = 0;
let afterHoursLastRefreshAt = 0;

async function consumeBackgroundResponse(response: Response) {
  const reader = response.body?.getReader();
  if (!reader) return;
  try { while (!(await reader.read()).done) { /* discard each chunk */ } }
  finally { reader.releaseLock(); }
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return withMarketRequestScope(async () => {
    const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
    runtime.__HANSTOCK_DB = env.DB;
    const url = new URL(request.url);
    const riverClock = taipeiMarketClock();
    const intradaySession = riverClock.weekday !== "Sat" && riverClock.weekday !== "Sun"
      && riverClock.minutes >= 8 * 60 + 55 && riverClock.minutes <= 13 * 60 + 35;

    if (url.pathname === "/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    if (env.DB && request.method === "GET" && (url.pathname === "/" || url.pathname === "/stock-screener") && Date.now() - tdccLastAutoRefreshAt >= TDCC_AUTO_REFRESH_MS) {
      tdccLastAutoRefreshAt = Date.now();
      const refreshUrl = new URL("/api/tdcc-radar", request.url);
      refreshUrl.searchParams.set("auto", "1");
      ctx.waitUntil(handler.fetch(new Request(refreshUrl, { headers: { Accept: "application/json" } }), env, ctx)
        .then(consumeBackgroundResponse)
        .then(() => undefined)
        .catch(() => undefined));
    }

    // 月營收申報期間會陸續加入新公司；使用者只要開著網站，每五分鐘
    // 便在背景重抓一次官方當月申報表，並把新高／新低事件永久寫入 D1。
    if (env.DB
      && request.method === "GET"
      && url.pathname !== "/api/monthly-revenue-records"
      && (url.pathname === "/" || url.pathname === "/stock-screener")
      && Date.now() - monthlyRevenueLastRefreshAt >= 5 * 60 * 1_000) {
      monthlyRevenueLastRefreshAt = Date.now();
      const revenueUrl = new URL("/api/monthly-revenue-records", request.url);
      revenueUrl.searchParams.set("auto", "1");
      ctx.waitUntil(handler.fetch(new Request(revenueUrl, { headers: { Accept: "application/json" } }), env, ctx)
        .then(consumeBackgroundResponse)
        .catch((error) => { console.error("monthly-revenue-background-refresh-failed", error); }));
    }

    if (env.DB && !intradaySession && request.method === "GET" && (url.pathname === "/" || url.pathname === "/stock-screener") && Date.now() - technicalHistoryLastAutoRefreshAt >= 2 * 60 * 1_000) {
      technicalHistoryLastAutoRefreshAt = Date.now();
      const twseTechnicalUrl = new URL("/api/technical-market", request.url);
      twseTechnicalUrl.searchParams.set("backfill", "1");
      twseTechnicalUrl.searchParams.set("compact", "1");
      twseTechnicalUrl.searchParams.set("stockIndicatorBackfill", "1");
      twseTechnicalUrl.searchParams.set("indicatorMarket", "twse");
      const tpexTechnicalUrl = new URL(twseTechnicalUrl);
      tpexTechnicalUrl.searchParams.set("indicatorMarket", "tpex");
      const riverUrl = new URL("/api/river-radar/daily", request.url);
      riverUrl.searchParams.set("side", "bull");
      riverUrl.searchParams.set("limit", "300");
      riverUrl.searchParams.set("refresh", "1");
      ctx.waitUntil(Promise.allSettled([
        handler.fetch(new Request(twseTechnicalUrl, { headers: { Accept: "application/json" } }), env, ctx).then(consumeBackgroundResponse),
        handler.fetch(new Request(tpexTechnicalUrl, { headers: { Accept: "application/json" } }), env, ctx).then(consumeBackgroundResponse),
      ])
        .then(() => handler.fetch(new Request(riverUrl, { headers: { Accept: "application/json" } }), env, ctx))
        .then(consumeBackgroundResponse)
        .catch(() => undefined));
    }

    // 當沖早賣訊號仍由 Worker 背景更新；均線多／空的長時間全市場
    // 掃描則由前景請求持有，避免 waitUntil 在完成前被平台終止。
    if (env.DB
      && request.method === "GET"
      && url.pathname !== "/api/daytrade-early-sell"
      && riverClock.weekday !== "Sat"
      && riverClock.weekday !== "Sun"
      && riverClock.minutes >= 8 * 60 + 55
      && riverClock.minutes <= 13 * 60 + 35
      && Date.now() - daytradeSignalsLastRefreshAt >= 60_000) {
      daytradeSignalsLastRefreshAt = Date.now();
      const daytradeSignalsUrl = new URL("/api/daytrade-early-sell", request.url);
      daytradeSignalsUrl.searchParams.set("limit", "2000");
      ctx.waitUntil(handler.fetch(new Request(daytradeSignalsUrl, { headers: { Accept: "application/json" } }), env, ctx)
        .then(consumeBackgroundResponse)
        .catch(() => undefined));
    }

    // The signal popup also needs the 14:30 executions even when folder 7 is closed.
    if (env.DB && request.method === "GET" && riverClock.minutes >= 14 * 60 + 30
      && (url.pathname === "/" || url.pathname === "/api/daytrade-early-sell")
      && Date.now() - afterHoursLastRefreshAt >= 30_000) {
      afterHoursLastRefreshAt = Date.now();
      ctx.waitUntil(loadAfterHoursWatchlist().catch(error => console.error("after-hours-import-failed", error)));
    }

    // 盤後籌碼不能再依賴使用者打開籌碼頁才更新。15:10 後（或隔日
    // 09:00 前的補救時段），任何網站流量都會在背景主動刷新官方排行與
    // FinMind 分點快取；來源尚未完整時，每五分鐘允許下一次請求重試。
    if (env.DB
      && request.method === "GET"
      && url.pathname !== "/api/market-ranking"
      && url.pathname !== "/api/broker-branch-daily"
      && shouldTriggerChipServerRefresh(chipLastServerRefreshAt)) {
      chipLastServerRefreshAt = Date.now();
      const clock = taipeiMarketClock();
      const bucket = Math.floor(clock.minutes / 5);
      const rankingUrl = new URL("/api/market-ranking", request.url);
      rankingUrl.searchParams.set("refresh", `server-auto-${clock.date}-${bucket}`);
      const branchUrl = new URL("/api/broker-branch-daily", request.url);
      ctx.waitUntil(Promise.allSettled([
        handler.fetch(new Request(rankingUrl, { headers: { Accept: "application/json" } }), env, ctx).then(consumeBackgroundResponse),
        handler.fetch(new Request(branchUrl, { headers: { Accept: "application/json" } }), env, ctx).then(consumeBackgroundResponse),
      ]).then(() => undefined));
    }

    return handler.fetch(request, env, ctx);
    });
  },
};

export default worker;
