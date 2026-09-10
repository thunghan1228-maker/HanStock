/** Cloudflare Worker entry point for the vinext-starter template. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { withMarketRequestScope } from "../lib/market-request-scope";
import { taipeiMarketClock } from "../lib/chip-auto-refresh";

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

let daytradeSignalsLastRefreshAt = 0;

async function consumeBackgroundResponse(response: Response) {
  const reader = response.body?.getReader();
  if (!reader) return;
  try { while (!(await reader.read()).done) { /* discard each chunk */ } }
  finally { reader.releaseLock(); }
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return withMarketRequestScope(async () => {
      const runtime = globalThis as typeof globalThis & { __HANSTOCK_DB?: D1Database };
      runtime.__HANSTOCK_DB = env.DB;
      const url = new URL(request.url);
      const riverClock = taipeiMarketClock();

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

      // Only the retained realtime signal center is refreshed in the background.
      // Retired technical/OTC/index, revenue, TDCC, after-hours and old chip
      // background refresh jobs are intentionally not started by the Worker.
      // This keeps the Cloudflare origin focused on the same core surface as
      // the simplified Railway backend: quotes, group rankings, main-force and
      // intraday signal center.
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

      return handler.fetch(request, env, ctx);
    });
  },
};

export default worker;
