import {assertKlineRevisionConsistency, runtimeRevision} from "./helpers/kline-revisions.mjs";
import { readEarlySellSources } from "./helpers/early-sell-sources.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

test("renders development preview metadata", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  assert.equal(response.status, 200);
  assert.match(
    response.headers.get("content-type") ?? "",
    /^text\/html\b/i,
  );
  assert.match(await response.text(), developmentPreviewMeta);
});

test("moves the signal date into the header and removes stock search", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const signalCenter = page.slice(page.indexOf('className={`early-signal-center'), page.indexOf("function openGlobalKline"));
  assert.match(page, /early-signal-center-title[\s\S]{0,800}early-signal-header-date/);
  assert.doesNotMatch(signalCenter, /className="early-signal-search"/);
  assert.doesNotMatch(signalCenter, /placeholder="輸入代號或名稱"/);
  assert.match(styles, /\.early-signal-header-date\{/);
  assert.match(styles, /@media \(max-width: 900px\) and \(pointer: coarse\)[\s\S]*?\.early-signal-center-head\{min-height:0;padding:7px 9px;display:grid/);
  assert.match(styles, /\.early-signal-center-actions\{width:100%;display:grid;grid-template-columns:minmax\(118px,1fr\) auto auto 42px/);
  assert.match(styles, /\.early-signal-tabs\{padding:5px 7px;gap:4px 6px;align-items:start;grid-auto-rows:auto\}/);
  assert.match(styles, /\.early-signal-tabs button:first-child,\.early-signal-tabs button:last-child\{height:34px;min-height:34px\}/);
});

test("compacts only watchlist stock information on touch iPhone and iPad", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const embed = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const touchBlock = css.match(/@media \(max-width:1180px\) and \(pointer:coarse\) \{[\s\S]*?\n\}/)?.[0] ?? "";
  assert.match(touchBlock, /\.watchlist-stock-info\{[^}]*display:grid[^}]*grid-template-areas:"index identity identity"[^}]*"\. signals signals"[^}]*"\. quote editor"[^}]*min-height:0/);
  assert.match(touchBlock, /\.watchlist-identity\{grid-area:identity;display:flex/);
  assert.match(touchBlock, /\.watchlist-signal-line\{grid-area:signals;display:flex/);
  assert.match(touchBlock, /\.watchlist-editor\{grid-area:editor/);
  assert.match(touchBlock, /\.watchlist-stock-info \.watchlist-stock-link/);
  assert.doesNotMatch(touchBlock, /watchlist-mini-chart (?:iframe|\.watchlist-mini-placeholder)/);
  assert.doesNotMatch(touchBlock, /original-kline-frame/);
  assert.match(css, /@media \(max-width:760px\) and \(pointer:coarse\) \{[\s\S]*?\.watchlist-stock-info\{[^}]*min-height:0/);
  assert.match(embed, /@media\(max-width:1179px\) and \(pointer:coarse\)/);
  assert.match(embed, /data-hanstock-period="5m"[^\n]*CandleChartDialog\.tsx:1986[^\n]*flex:1 1 auto!important/);
  assert.match(embed, /hanstock-battle-moved-indicator\{display:inline-flex!important;[^}]*visibility:visible!important/);
  assert.doesNotMatch(embed, /@media\(min-width:1180px\)[\s\S]{0,500}data-hanstock-period="5m"/);
  assert.match(embed, /controls\.id = 'hanstock-watchlist-indicator-controls'/);
  assert.match(embed, /proxy\.disabled !== !original/);
  assert.match(embed, /target\.closest\('#hanstock-watchlist-indicator-controls,svg'\)/);
  assert.match(css, /\.watchlist-card-grid \.watchlist-mini-chart header\{height:40px;min-height:40px;display:grid/);
  assert.match(embed, /groups = \['MACD', 'KD', 'MA設定', '指標參數'\]/);
  assert.match(embed, /original\?\.click\(\)/);
  assert.match(embed, /@media\(max-width:1179px\) and \(pointer:coarse\)\{[\s\S]*?CandleChartDialog\.tsx:2018[^}]*height:100%!important/);
});

test("allows pinch zoom across the iPhone and iPad site including embedded K-lines", async () => {
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const klineEmbed = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(layout, /minimumScale: 0\.5/);
  assert.match(layout, /maximumScale: 5/);
  assert.match(layout, /userScalable: true/);
  assert.doesNotMatch(layout, /maximumScale: 1/);
  assert.match(klineEmbed, /minimum-scale=0\.5,maximum-scale=5,user-scalable=yes/);
  assert.doesNotMatch(klineEmbed, /maximum-scale=1/);
});

test("auto-refreshes the weekly TDCC radar from the current CSV with a visible update time", async () => {
  const route = await readFile(new URL("../app/api/tdcc-radar/route.ts", import.meta.url), "utf8");
  const source = await readFile(new URL("../lib/tdcc-radar-source.ts", import.meta.url), "utf8");
  const storage = await readFile(new URL("../db/tdcc-radar.ts", import.meta.url), "utf8");
  const calculation = await readFile(new URL("../lib/tdcc-radar-calculation.ts", import.meta.url), "utf8");
  const panel = await readFile(new URL("../app/stock-screener/ChipSurgeRadarPanel.tsx", import.meta.url), "utf8");
  const strategy = await readFile(new URL("../app/stock-screener/StrategyWorkbench.tsx", import.meta.url), "utf8");
  const screenerPage = await readFile(new URL("../app/stock-screener/page.tsx", import.meta.url), "utf8");
  const worker = await readFile(new URL("../worker/index.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/stock-screener-advanced.css", import.meta.url), "utf8");

  assert.match(source, /opendata\.tdcc\.com\.tw\/getOD\.ashx\?id=1-5/);
  assert.match(source, /openapi\.tdcc\.com\.tw\/v1\/opendata\/1-5/);
  assert.match(source, /fetchCsvSnapshot/);
  assert.match(source, /fetchJsonSnapshot/);
  assert.match(route, /REFRESH_INTERVAL_MS = 10 \* 60 \* 1_000/);
  assert.match(route, /weeklyChangePp: previous\.has\(code\) \? calculateTdccWeeklyChangePp/);
  assert.match(calculation, /currentPct - previousPct/);
  assert.match(route, /saveAndReadTdccSnapshots\(\[\]\)/);
  assert.match(route, /params\.get\("auto"\) === "1"/);
  assert.match(route, /stale-while-revalidate=21600/);
  assert.match(storage, /Promise\.all\(selected\.map/);
  assert.match(storage, /WHERE data_date = \?/);
  assert.match(storage, /SELECT MAX\(updated_at\) AS updatedAt/);
  assert.match(panel, /最新更新 \{formatUpdateTime\(payload\.updatedAt\)\}/);
  assert.match(panel, /window\.setInterval\(\(\)=>void refresh\(\),AUTO_REFRESH_MS\)/);
  assert.match(panel, /每 \{payload\.autoRefreshMinutes\|\|10\} 分鐘自動檢查/);
  assert.match(panel, /<h2>千張以上持股集中度<\/h2>/);
  assert.match(panel, /第 15 級（1,000,001 股以上）占集保庫存比例/);
  assert.doesNotMatch(panel, /籌碼暴增雷達|暴增排行|大戶增加|大戶減少/);
  assert.match(screenerPage, /<strong>千張以上持股集中度<\/strong>/);
  assert.doesNotMatch(`${screenerPage}\n${strategy}`, /籌碼暴增/);
  assert.match(panel, /weeklyChangePp:number\|null/);
  assert.match(panel, /\.\.\.stock,largeHolderPct:r\.largeHolderPct,previousPct:r\.previousPct,weeklyChangePp:r\.weeklyChangePp/);
  assert.doesNotMatch(panel, /row\.changePct/);
  assert.match(worker, /TDCC_AUTO_REFRESH_MS = 10 \* 60 \* 1_000/);
  assert.match(worker, /new URL\("\/api\/tdcc-radar", request\.url\)/);
  assert.match(styles, /\.chip-radar-head aside em\{[^}]*color:#77dca3/);
});

test("reuses the last successful TDCC holder snapshot while checking updates in the background", async () => {
  const panel = await readFile(new URL("../app/StockResearchPanels.tsx", import.meta.url), "utf8");

  assert.match(panel, /TDCC_SNAPSHOT_STORAGE_KEY = "hanstock-tdcc-radar-fast-v1"/);
  assert.match(panel, /function readTdccSnapshot\(\)/);
  assert.match(panel, /function useTdccRadar\(\)/);
  assert.match(panel, /const \{ payload, refreshing \} = useTdccRadar\(\)/);
  assert.match(panel, /背景確認中/);
});

test("keeps the live alert panel from covering the signal center", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /early-signal-center-alerts/);
  assert.match(source, /最新提醒 \{popupSignals\.length\}/);
  assert.match(source, /topbar-status\$\{centerOpen \? " has-signal-center-open" : ""\}/);
  assert.match(styles, /@media \(min-width: 901px\)[\s\S]*?topbar-status\.has-signal-center-open:not\(\.is-signal-window\) \.early-signal-center/);
  const toastLayer = Number(styles.match(/^\.early-sell-toast \{[^}]*z-index: (\d+)/m)?.[1]);
  const centerLayer = Number(styles.match(/^\.early-signal-backdrop \{[^}]*z-index: (\d+)/m)?.[1]);
  assert.ok(centerLayer > toastLayer, 'Desktop signal center must stack above the live toast');
  assert.match(styles, /data-hanstock-device="ipad"\] \.topbar-status\.has-signal-center-open \.early-sell-toast,[\s\S]*?display: none/);
  assert.match(styles, /data-hanstock-device="iphone"\] \.early-signal-center\.is-positioned[\s\S]*?left: auto!important/);
});

test("moves the current live-alert category to an external signal window", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /url\.searchParams\.set\("signalMode", requestedMode\)/);
  assert.match(source, /INTRADAY_SIGNAL_CENTER_MODES\.includes\(requestedMode\)/);
  assert.match(source, /createPortal\(popupPanel[\s\S]*?popupExternalWindow\.document\.body/);
  assert.match(source, /early-sell-popout[^\n]*onClick=\{popupExternalWindow \? returnPopupToPage : openPopupOnExternalScreen\}/);
  assert.match(styles, /early-sell-toast header \.early-sell-popout/);
});

test("shows only the full signal center in the external-monitor window", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /!signalWindowMode && signalAlertsEnabled && popupSignals\.length > 0/);
  assert.match(source, /document\.documentElement\.style\.overflow = "hidden"/);
  assert.match(source, /document\.body\.style\.overflow = "hidden"/);
  assert.match(styles, /topbar-status\.has-signal-center-open:not\(\.is-signal-window\) \.early-signal-backdrop/);
  assert.match(styles, /\.early-signal-backdrop \{[^}]*inset: 0/);
  assert.match(styles, /\.early-signal-backdrop\.is-detached \{[^}]*place-items: stretch/);
});

test("pins the detached signal center above other windows with Document Picture-in-Picture", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(source, /documentPictureInPicture/);
  assert.match(source, /pictureInPicture\.requestWindow\(\{ width: 1500, height: 900 \}\)/);
  assert.match(source, /signalPinned=1/);
  assert.match(source, /📌 置頂最上層/);
  assert.match(source, /📌 最上層顯示中/);
  assert.match(source, /螢幕最上層顯示中｜可移到另一個螢幕/);
  assert.match(source, /解除置頂/);
  assert.match(source, /signalPinnedWindow\.document\.body/);
  assert.match(source, /請使用最新版 Chrome 或 Edge 電腦版/);
});

test("uses the primary HanStock Hub contract for intraday main-force bars", async () => {
  const source = await readFile(
    new URL("../app/api/force-bars/route.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /"https:\/\/hanstock\.xyz"/);
  assert.doesNotMatch(source, /HUB_BASES\s*=\s*\[[\s\S]*?"https:\/\/www\.hanstock\.xyz"/);
  assert.match(source, /\/api\/hub\/bars1m\//);
  assert.match(source, /\/api\/hub\/bars\//);
  assert.match(source, /payload\?\.bars\s*\?\?/);
  assert.match(source, /bar\.ts\s*\?\?\s*bar\.bar_ts/);
  assert.match(source, /bar\.main_net_volume/);
  assert.match(source, /bar\.main_buy_amount/);
  assert.match(source, /bar\.main_sell_amount/);
  assert.match(source, /bar\.main_net_amount/);
  assert.match(source, /buyAmount:/);
  assert.match(source, /sellAmount:/);
  assert.match(source, /netAmount:/);
  assert.match(source, /bar\.main_force_available\s*===\s*false\) return \[\]/);
  assert.match(source, /barTimestamp\(date\)/);
  assert.match(source, /epochMilliseconds/);
  assert.match(source, /days=31&limit=20000&backfill=false/);
  assert.doesNotMatch(source, /days=400/);
});

test("keeps historical main-force bars separate when timestamps are null", async () => {
  const source = await readFile(
    new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url),
    "utf8",
  );

  const bootstrap = await readFile(new URL("../lib/kline-force-bootstrap.ts", import.meta.url), "utf8");
  assert.match(bootstrap, /hanstock-candle-force-v4/);
  assert.match(bootstrap, /mergeForceHistory/);
  assert.match(source, /intradayForceBootstrap/);
});

test("shows per-bar main-force monetary amounts beside the K-line quote on every viewport", async () => {
  const embedSource = await readFile(
    new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url),
    "utf8",
  );
  const historySource = await readFile(
    new URL("../db/force-history.ts", import.meta.url),
    "utf8",
  );

  assert.match(embedSource, /hanstock-force-amount-quote/);
  assert.match(embedSource, /主力大單買進/);
  assert.match(embedSource, /主力大單賣出/);
  assert.match(embedSource, /主力大單淨額/);
  assert.match(embedSource, /setInterval\(load,10000\)/);
  assert.match(embedSource, /lastInteraction<1e4/);
  assert.match(embedSource, /alignClock/);
  assert.match(embedSource, /Number\(match\[2\]\)-5/);
  assert.match(embedSource, /document\.body\.appendChild\(box\)/);
  assert.match(embedSource, /fallbackOffset=matchMedia\("\(max-width:820px\)"\)\.matches\?62:40/);
  assert.match(embedSource, /isPhone=matchMedia\("\(max-width:600px\)"\)\.matches/);
  assert.match(embedSource, /ohlcRect=changeNode\?\.parentElement\?\.getBoundingClientRect\(\)/);
  assert.match(embedSource, /if\(isPhone\)box\.style\.setProperty/);
  assert.match(embedSource, /Math\.max\(198,ohlcRect\?\.height\?ohlcRect\.bottom\+12:198\)/);
  assert.match(embedSource, /style\.setProperty\("top",[^;]+,"important"\)/);
  assert.match(embedSource, /@media\(max-width:600px\)\{#hanstock-force-amount-quote\{top:198px!important\}\}/);
  assert.match(embedSource, /else box\.style\.top=Math\.max\(4,referenceRect\?\.height\?referenceRect\.bottom\+6:quoteRect\.top\+fallbackOffset\)/);
  assert.match(embedSource, /#hanstock-force-amount-quote\{position:fixed!important;z-index:48!important;top:8px!important;right:12px/);
  assert.match(embedSource, /主力大單買進 <em>讀取中<\/em>/);
  assert.match(embedSource, /hanstock-crosshair-date-highlight/);
  assert.match(embedSource, /hanstock-crosshair-date-part/);
  assert.match(embedSource, /@media\(max-width:820px\)[\s\S]*?#hanstock-force-amount-quote/);
  assert.match(embedSource, /font-size:11px;line-height:1\.35/);
  assert.match(embedSource, /hanstock-battle-search-form/);
  assert.match(embedSource, /hanstock-battle-search-shell/);
  assert.match(embedSource, /\["KD","MACD","指標參數","MA設定"\]/);
  assert.match(embedSource, /hanstock-battle-moved-indicator/);
  assert.match(embedSource, /searchShell\.appendChild\(button\)/);
  assert.match(embedSource, /matchMedia\("\(max-width:820px\)"\)\.matches/);
  assert.match(embedSource, /width:40%!important;max-width:40%!important/);
  assert.match(historySource, /intraday_force_bars_v2/);
  assert.match(historySource, /buy_amount REAL NOT NULL/);
  assert.match(historySource, /sell_amount REAL NOT NULL/);
  assert.match(historySource, /net_amount REAL NOT NULL/);
});

test("hides the five-minute signal legend only in four-grid charts", async () => {
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.doesNotMatch(pageSource, /className="kline-force-amount-strip"/);
  assert.match(pageSource, /className="mobile-signal-legend"/);
  assert.match(pageSource, /const syncMobileSignalLegend = \(\) =>/);
  assert.match(pageSource, /if \(!window\.matchMedia\("\(max-width: 700px\)"\)\.matches\) \{[\s\S]*?legend\.style\.removeProperty\("display"\);[\s\S]*?setMobileSignalLegendHtml\(""\);[\s\S]*?return;/);
  assert.match(embedSource, /const gridMode = request\.nextUrl\.searchParams\.get\("view"\) === "grid"/);
  assert.match(embedSource, /gridMode \? "&view=grid" : homeMode \? "&view=home" : ""/);
  assert.match(embedSource, /const gridSignalLegendBootstrap = !gridMode \? "" :/);
  assert.match(embedSource, /hanstock-grid-hide-signal-legend/);
  assert.match(embedSource, /legend\.style\.setProperty\("display","none","important"\)/);
  assert.match(embedSource, /text\.includes\("破905D"\)&&text\.includes\("A8空"\)&&text\.includes\("20MA轉向"\)/);
  assert.match(embedSource, /if\(gridMode\)\{legend\.style\.setProperty\("display","none","important"\)/);
  assert.match(embedSource, /html\[data-hanstock-grid-mode="true"\] \.hanstock-grid-signal-legend/);
  assert.match(embedSource, /\$\{signalTooltipBootstrap\}\$\{break35LegendBootstrap\}\$\{gridSignalLegendBootstrap\}\$\{signalSessionFilterBootstrap\}/);
  assert.match(embedSource, /const syncMobileLegend=\(\)=>\{if\(parent===window\)return;const moveToParent=matchMedia\("\(max-width:700px\)"\)\.matches&&!gridMode/);
  assert.match(embedSource, /if\(!moveToParent\)\{legend\.style\.removeProperty\("display"\)/);
  assert.doesNotMatch(embedSource, /signalTooltipBootstrap = interval === "5m"/);
  assert.doesNotMatch(embedSource, /@media\(max-width:700px\)\{div\[data-loc="client\/src\/components\/CandleChartDialog\.tsx:1960"\][\s\S]*?display:none!important/);
  assert.match(embedSource, /hanstock-mobile-signal-legend/);
  assert.match(styles, /\.mobile-signal-legend \{[^}]*min-height:106px[^}]*font-size:14px/);
  assert.match(styles, /\.mobile-signal-legend svg\{[^}]*width:28px;height:28px/);
});

test("keeps the K-line toolbar compact and bright while separating the latest close labels", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /1762"\]>button\{min-height:28px!important;height:28px!important;[^}]*brightness\(1\.45\)/);
  assert.match(embedSource, /1768"\]\{padding:0 10px!important;font-size:11px!important\}/);
  assert.match(embedSource, /1781"\][^\n]*font-size:10px!important\}/);
  assert.match(embedSource, /1762"\]\{position:relative!important;[^}]*width:100%!important;[^}]*min-height:28px!important;[^}]*flex-wrap:wrap!important/);
  assert.match(embedSource, /hanstock-battle-moved-indicator\{[^}]*height:40px!important;[^}]*font-size:11px!important;[^}]*brightness\(1\.42\)/);
  assert.match(embedSource, /2396"\]\{height:32px!important;transform:translateY\(-7px\)/);
  assert.match(embedSource, /2404"\]\{font-size:22px!important/);
  assert.match(embedSource, /2415"\]\{font-size:17px!important;[^}]*transform:translate\(-80px,-5px\)/);
});

test("keeps the embedded K-line full-size and reflows its controls when the window narrows", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /hanstock-responsive-chart-layout/);
  assert.doesNotMatch(embedSource, /DESIGN_WIDTH|MIN_SCALE|body\.style\.transform=scale/);
  assert.match(embedSource, /body\.style\.transform="none"/);
  assert.match(embedSource, /1581"\]\{inset:0!important;[^}]*width:100vw!important;height:100vh!important/);
  assert.match(embedSource, /2018"\]\{display:block!important;width:100%!important;height:100%!important/);
  assert.match(embedSource, /1745"\]\{position:relative!important;[^}]*width:100%!important;[^}]*flex-wrap:wrap!important/);
  assert.match(embedSource, /1762"\]\{position:relative!important;[^}]*width:100%!important;[^}]*flex-wrap:wrap!important/);
  assert.match(embedSource, /#hanstock-force-amount-quote\{[^}]*top:8px!important;[^}]*max-width:min\(680px,48vw\)/);
  assert.match(embedSource, /1988"\]\{position:fixed!important;[^}]*width:max-content!important;[^}]*max-width:calc\(100vw - clamp\(590px,31vw,700px\) - 24px\)!important;[^}]*overflow:visible!important;[^}]*white-space:nowrap!important/);
  assert.match(embedSource, /1988"\]>div\{min-width:max-content!important;max-width:none!important;overflow:visible!important\}/);
  assert.doesNotMatch(embedSource, /1988"\]\{position:fixed!important;[^}]*max-width:260px!important/);
  assert.match(embedSource, /window\.addEventListener\("resize",queue/);
});

test("keeps four-grid MA settings selectable and below the compact quote rows", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const gridSource = await readFile(new URL("../app/kline-grid/page.tsx", import.meta.url), "utf8");

  assert.match(embedSource, /1581"\]\{gap:1px!important;padding:1px 4px!important\}/);
  assert.match(embedSource, /1622"\]\{min-height:24px!important;padding:0!important\}/);
  assert.match(embedSource, /1899"\]\{position:relative!important;[^}]*flex:0 0 auto!important;[^}]*overflow:visible!important/);
  assert.match(embedSource, /1901"\]\{min-height:32px!important\}/);
  assert.match(embedSource, /1988"\]\{position:relative!important;[^}]*flex:0 0 32px!important;margin-top:2px!important/);
  assert.match(embedSource, /#hanstock-force-amount-quote\{top:104px!important;right:8px;max-width:calc\(50vw - 12px\)/);
  assert.match(embedSource, /#hanstock-reference-row\{min-height:30px!important;padding:1px 8px!important\}#hanstock-reference-prices\.hanstock-reference-dock\{max-width:100%!important;min-height:26px!important/);
  assert.match(gridSource, /view=grid&uiRev=[\w-]+/);
});

test("stores daily, one-minute, and five-minute MA settings independently", async () => {
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(runtimeSource, /hanstock-ma-config-v3:/);
  assert.match(runtimeSource, /kD\(c\)/);
  assert.match(runtimeSource, /enabled:a==="1d"\?!0:o\.enabled!==!1/);
  assert.match(runtimeSource, /a!=="1d"\?o:Vi\.map\(c=>\(\{\.\.\.c,\.\.\.\(o\.find\(d=>Number\(d\.period\)===Number\(c\.period\)\)\|\|\{\}\),enabled:!0\}\)\)/);
  assert.match(runtimeSource, /const hanstockMaIntervalRef=v\.useRef\(c\),hanstockMaConfigRef=v\.useRef\(j\);hanstockMaConfigRef\.current=j/);
  assert.match(runtimeSource, /hanstockCommitMa=A=>k\(Q=>\{const X=typeof A==="function"\?A\(Q\):A;return hanstockMaConfigRef\.current=X,hanstockSaveMa\(hanstockMaIntervalRef\.current,X\),X\}\)/);
  assert.match(runtimeSource, /Pa=\(A,K\)=>hanstockCommitMa\(te=>te\.map/);
  assert.match(runtimeSource, /za=A=>hanstockCommitMa\(K=>K\.filter/);
  assert.match(runtimeSource, /hanstockCommitMa\(ue=>\[\.\.\.ue,\{id:`ma-\$\{Date\.now\(\)\}`/);
  assert.match(runtimeSource, /onClick:\(\)=>hanstockCommitMa\(Vi\)/);
  assert.match(runtimeSource, /const Q=\["1d","1m","5m"\]\.includes\(A\)\?A:"1d",X=kD\(Q\);hanstockSaveMa\(hanstockMaIntervalRef\.current,hanstockMaConfigRef\.current\),hanstockMaIntervalRef\.current=Q,hanstockMaConfigRef\.current=X,N\(Q\),k\(X\)/);
  assert.doesNotMatch(runtimeSource, /v\.useEffect\(\(\)=>\{hanstockSaveMa\(hanstockMaIntervalRef\.current,j\)\},\[j\]\)/);
  assert.match(runtimeSource, /onClick:\(\)=>hanstockSetInterval\(A\.key\)/);
  assert.match(runtimeSource, /localStorage\.setItem\("hanstock-ma-config-v3:"\+A/);
  assert.match(runtimeSource, /"X-HanStock-MA-Config": maPeriodSettingsPatched \? "1d-1m-5m-independent"/);
  assert.match(embedSource, /hanstock-period-ma-storage-isolation/);
  assert.match(embedSource, /Storage\.prototype\.getItem/);
  assert.match(embedSource, /Storage\.prototype\.setItem/);
  assert.match(embedSource, /name===legacy\)return originalSet\.call\(this,key\(\),value\)/);
  assert.match(embedSource, /\$\{periodMaStorageIsolationBootstrap\}\$\{routeBootstrap\}[\s\S]*?<script type="module" crossorigin src="\/api\/kline-runtime/);
});

test("places reference prices in their own row between the toolbar and signal legend", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /hanstock-reference-dock-bootstrap/);
  assert.match(embedSource, /toolbar=document\.querySelector\('[^']*1762[^']*'\)/);
  assert.match(embedSource, /row\.previousElementSibling!==toolbar/);
  assert.match(embedSource, /toolbar\.insertAdjacentElement\("afterend",row\)/);
  assert.match(embedSource, /row\.appendChild\(strip\)/);
  assert.match(embedSource, /strip\.classList\.add\("hanstock-reference-dock"\)/);
  assert.match(embedSource, /1888"\]\{min-height:20px!important;flex:0 0 20px!important;overflow:visible!important\}/);
  assert.match(embedSource, /#hanstock-reference-row\{position:relative!important;[^}]*flex:0 0 auto!important;[^}]*justify-content:flex-end!important/);
  assert.match(embedSource, /#hanstock-reference-prices\.hanstock-reference-dock\{position:relative!important;[^}]*justify-content:flex-end!important;[^}]*margin:0!important/);
  assert.match(embedSource, /@media\(min-width:1180px\)\{[\s\S]*?#hanstock-reference-row\{position:fixed!important;[^}]*top:48px!important;[^}]*right:12px!important;[^}]*height:0!important;[^}]*flex:0 0 0!important/);
  assert.match(embedSource, /@media\(min-width:1180px\)\{[\s\S]*?1988"\]\{position:fixed!important;[^}]*top:104px!important;[^}]*left:clamp\(590px,31vw,700px\)!important;[^}]*flex:0 0 auto!important/);
  assert.match(embedSource, /1960"\]\{position:relative!important;[^}]*top:auto!important;[^}]*flex:0 0 auto!important;[^}]*margin:0 12px 4px!important/);
  assert.doesNotMatch(embedSource, /anchor\.insertAdjacentElement\("beforebegin",strip\)/);
  assert.match(embedSource, /@media\(max-width:820px\)[\s\S]*?1899"\]\{position:relative!important;[^}]*flex:0 0 auto!important;[^}]*overflow:visible!important/);
  assert.match(embedSource, /@media\(max-width:820px\)[\s\S]*?1988"\]\{position:relative!important;[^}]*flex:0 0 32px!important;margin-top:3px!important/);
  assert.match(embedSource, /quoteAndReferenceBootstrap\}\$\{authoritativeQuoteBootstrap\}\$\{referenceDockBootstrap\}/);
});

test("shows open high close low percent change and price change in the top stock quote", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /hanstock-authoritative-full-quote/);
  assert.match(embedSource, /window\.__hanstockAllCandles/);
  assert.match(embedSource, /valid\[0\]\?\.open/);
  assert.match(embedSource, /Math\.max\(\.\.\.highs\)/);
  assert.match(embedSource, /Math\.min\(\.\.\.lows\)/);
  assert.match(embedSource, /<b>開 <em>/);
  assert.match(embedSource, /<b>高 <em>/);
  assert.match(embedSource, /<b>收 <em>/);
  assert.match(embedSource, /<b>低 <em>/);
  assert.match(embedSource, /<b>漲跌幅 <em>/);
  assert.match(embedSource, /<b>漲跌 <em>/);
  assert.match(embedSource, /@media\(max-width:820px\)[\s\S]*?#hanstock-authoritative-change-values\{width:100%!important/);
});

test("loads the K-line shell and runtime through a bounded cached fast path", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  const proxySource = await readFile(new URL("../app/api/trpc/[...path]/route.ts", import.meta.url), "utf8");
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");

  assert.match(embedSource, /function localKlineShell\(\)/);
  assert.match(embedSource, /route-independent SPA shell/);
  assert.doesNotMatch(embedSource, /loadKlineShell\(sourceUrl\)/);
  const forceBootstrap = await readFile(new URL("../lib/kline-force-bootstrap.ts", import.meta.url), "utf8");
  assert.match(forceBootstrap, /AbortSignal\.timeout\(12000\)/);
  assert.match(forceBootstrap, /void loadSaved\(\)/);
  assert.doesNotMatch(forceBootstrap, /await loadSaved\(\)/);
  assert.match(forceBootstrap, /if\(pending\)return pending/);
  assert.match(embedSource, /rev=\$\{KLINE_RUNTIME_REVISION\}&asset=/);
  assert.match(embedSource, /public, max-age=15, s-maxage=60, stale-while-revalidate=300/);
  assert.match(layoutSource, /rel="modulepreload"[\s\S]*kline-runtime\?rev=/);
  assertKlineRevisionConsistency();

  assert.match(runtimeSource, /runtimeSourceCache = new Map/);
  assert.match(runtimeSource, /loadRuntimeSource\(asset\)/);
  assert.match(runtimeSource, /cache: "no-store"/);
  assert.doesNotMatch(runtimeSource, /cache: "force-cache"/);
  assert.match(runtimeSource, /Cloudflare Workers only accepts no-store\/no-cache/);
  assert.match(runtimeSource, /public, max-age=31536000, immutable/);
  assert.match(runtimeSource, /X-HanStock-Runtime-Revision/);

  assert.match(proxySource, /KLINE_REPAIR_BUDGET_MS = 80/);
  assert.match(proxySource, /loadCachedRepair\(targetRepair\)/);
  assert.match(proxySource, /repair: await repairPromise/);
  assert.match(proxySource, /X-HanStock-Kline-Auto-Repair", "deferred/);
  assert.match(proxySource, /X-HanStock-Kline-Repair-Source", "original-fast-path/);
  assert.match(proxySource, /max-age=3, s-maxage=8, stale-while-revalidate=30/);
});

test("switches K-line periods in place, preserves the current chart, and rebuilds only after a genuine stall", async () => {
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(pageSource, /const \[framePeriod, setFramePeriod\] = useState<Period>/);
  assert.match(pageSource, /const frameInterval = framePeriod === "day" \? "1d" : framePeriod/);
  const periodBranch = pageSource.match(/if \(message\.type === "hanstock-kline-period"\) \{[\s\S]*?return;\r?\n      \}/)?.[0] ?? "";
  assert.match(periodBranch, /setPeriod\(nextPeriod\)/);
  assert.match(periodBranch, /setFramePeriod\(nextPeriod\)/);
  assert.match(periodBranch, /setFrameReloadRevision/);
  assert.match(embedSource, /window\.__hanstockActiveInterval=interval/);
  assert.match(embedSource, /new CustomEvent\("hanstock-period-change"/);
  assert.match(embedSource, /requestAnimationFrame\(\(\)=>parent\.postMessage/);
  assert.match(embedSource, /periodAwareCandleCacheBootstrap/);
  assert.match(embedSource, /periodAwareForceAmountQuoteBootstrap/);
  assert.match(embedSource, /periodAwareCrosshairBridgeBootstrap/);
  assert.match(embedSource, /hanstock-kline-data-ready/);
  assert.match(embedSource, /hanstock-period-snapshot/);
  assert.match(embedSource, /window\.__hanstockPeriodRequest=\{interval,signature:/);
  assert.match(embedSource, /window\.__hanstockPeriodRendered=bars=>/);
  assert.match(runtimeSource, /window\.__hanstockPeriodRendered\?\.\(ke\)/);
  assert.match(pageSource, /periodSwitchTimerRef/);
  assert.match(pageSource, /setFrameReloadRevision/);
  assert.match(pageSource, /5_000/);
});

test("keeps the narrowed desktop K-line drawable without recursive height writes and enlarges its bottom date labels", async () => {
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(pageSource, /key=\{`\$\{klineFrameUrl\}:\$\{frameReloadRevision\}`\}/);
  assert.match(embedSource, /queue=\(\)=>\{clearTimeout\(timer\);timer=setTimeout\(run,96\)\}/);
  assert.match(embedSource, /body\.dataset\.hanstockStableViewport!=="1"/);
  assert.doesNotMatch(embedSource, /getBoundingClientRect\(\)\.top|hanstockStableHeight|style\.setProperty\("height"/);
  assert.match(embedSource, /1986"\],div\[data-loc="client\/src\/components\/CandleChartDialog\.tsx:2017"\][^{]*\{[^}]*display:flex!important;[^}]*flex:1 1 auto!important/);
  assert.match(embedSource, /1988"\]\{position:relative!important;[^}]*height:32px!important;[^}]*min-height:32px!important;[^}]*display:flex!important;[^}]*flex:0 0 32px!important/);
  assert.match(embedSource, /1990"\]\{[^}]*height:auto!important;[^}]*flex:1 1 auto!important/);
  assert.doesNotMatch(embedSource, /1988"\],div\[data-loc="client\/src\/components\/CandleChartDialog\.tsx:1990"\]\{height:100%!important/);
  assert.match(embedSource, /@media\(min-width:821px\) and \(max-width:1179px\)[\s\S]*?2530"\][^}]*font-size:29px!important/);
  assert.match(embedSource, /2630"\][^}]*font-size:24px!important/);
  assert.match(runtimeSource, /\?18:24,[^;]*\?18:30/);
  assert.match(runtimeSource, /\?42:50/);
  assert.match(runtimeSource, /y:Ma-10/);
  assert.match(runtimeSource, /"X-HanStock-Bottom-Time-Axis": bottomTimeAxisPatched \? "below-force-chart"/);
});

test("moves the latest-trade status below the stock identity and leaves the bottom time axis unobstructed", async () => {
  const quoteSource = await readFile(new URL("../lib/kline-live-quote-bootstrap.ts", import.meta.url), "utf8");

  assert.match(quoteSource, /CandleChartDialog\.tsx:1622/);
  assert.match(quoteSource, /insertAdjacentElement\('afterend',strip\)/);
  assert.doesNotMatch(quoteSource, /position:fixed;bottom:0/);
});

test("keeps desktop stock identity and the complete selected-bar quote visible", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /hanstock-resolved-stock-name-bootstrap/);
  assert.match(embedSource, /\/api\/stock-search\?q=/);
  assert.match(embedSource, /CandleChartDialog\.tsx:1640/);
  assert.match(embedSource, /text\.startsWith\(ticker\)&&text\.includes\("收"\)&&text\.includes\("%"\)/);
  assert.match(embedSource, /1988"\]\{position:relative!important;[^}]*height:32px!important;[^}]*min-height:32px!important;[^}]*flex:0 0 32px!important/);
  assert.match(embedSource, /1990"\]\{[^}]*height:auto!important/);
  assert.match(runtimeSource, /children:\["漲跌幅 "/);
  assert.match(runtimeSource, /"　漲跌 "/);
});

test("tightens only desktop five-minute candle spacing while preserving other periods and devices", async () => {
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.equal(runtimeRevision(runtimeSource), runtimeRevision(embedSource));
  assert.match(runtimeSource, /!\$\{compactRuntimeVar\}&&\$\{fiveMinuteVar\}/);
  assert.match(runtimeSource, /\$\{candleStepVar\}\*\.98/);
  assert.match(runtimeSource, /\$\{candleStepVar\}\*\.82/);
  assert.match(runtimeSource, /desktop-5m-98pct_other-82pct/);
});

test("keeps persisted intraday K-line markers across weekends, holidays and pre-open hours", async () => {
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const gridSource = await readFile(new URL("../app/kline-grid/page.tsx", import.meta.url), "utf8");
  const layoutSource = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");

  assert.match(runtimeSource, /window\.__hanstockSignalSinceTs/);
  assert.match(runtimeSource, /latest-session-until-0845/);
  assert.doesNotMatch(runtimeSource, /getUTCHours\(\)\*60.*return \$>=525/);
  assert.match(embedSource, /"破905D"/);
  assert.match(embedSource, /"12空"/);
  assert.match(embedSource, /"加強12空"/);
  assertKlineRevisionConsistency();
});

test("draws a visible fallback signal layer when the upstream five-minute marker query does not reach the SVG", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /hanstock-signal-overlay-bootstrap/);
  assert.match(embedSource, /schedule\.intradaySignalsByTicker\?batch=1&input=/);
  assert.match(embedSource, /hanstock-stable-market-signal-bootstrap/);
  assert.match(embedSource, /schedule\.intradaySignals\?batch=1&input=/);
  assert.match(embedSource, /window\.__hanstockStableBreak35Count/);
  assert.match(embedSource, /signal\?\.kind==="break15kLow"/);
  assert.match(embedSource, /window\.__hanstockVisibleCandles/);
  assert.match(embedSource, /CandleChartDialog\.tsx:2158/);
  assert.match(embedSource, /id:"hanstock-signal-overlay"/);
  assert.match(embedSource, /data-hanstock-signal-kind/);
  assert.match(embedSource, /window\.__hanstockSignalOverlayCount/);
  assert.match(embedSource, /case"break15kLow":return\{text:"三五",color:"#22c55e",filled:true,scale:1\.3\}/);
  assert.match(embedSource, /baseDiameter\*Number\(glyph\.scale\|\|1\)/);
  assert.match(embedSource, /hanstock-break35-legend \.hanstock-break35-icon\{transform:scale\(1\.3\)!important/);
  assert.match(embedSource, /circle\.setAttribute\("fill","#22c55e"\)/);
  assert.match(embedSource, /text\.setAttribute\("fill","#fff"\)/);
  for (const kind of ["crossUp905", "break905d", "watch12short", "short12", "enhanced12short", "fiveMinuteTwelveShort", "fiveMinuteOnePlusTwoLong"]) {
    assert.match(embedSource, new RegExp(`case\\"${kind}\\"`));
  }
  const template = embedSource.match(/const signalOverlayBootstrap = interval !== "5m" \? "" : `([\s\S]*?)`;\r?\n    const coLocatedSignalOverlayBootstrap/)?.[1];
  assert.ok(template);
  const rendered = new Function("ticker", `return \`${template}\`;`)("2481");
  const scriptBody = rendered.match(/<script[^>]*>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(scriptBody);
  assert.doesNotThrow(() => new Function(scriptBody));
  const legendTemplate = embedSource.match(/const break35LegendBootstrap = interval !== "5m" \? "" : `([\s\S]*?)`;\r?\n    const gridSignalLegendBootstrap/)?.[1];
  assert.ok(legendTemplate);
  const legendScript = legendTemplate.match(/<script[^>]*>([\s\S]*?)<\/script>/)?.[1];
  assert.ok(legendScript);
  assert.doesNotThrow(() => new Function(legendScript));
});

test("keeps signal markers on five-minute charts and removes them from one-minute charts", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(runtimeSource, /b==="5m"\?xa\.marks\.filter\(A=>window\.__hanstockIsSignalCandleVisible\?\.\(ke\[A\.i\]\)/);
  assert.match(runtimeSource, /X-HanStock-One-Minute-Signals/);
  assert.match(embedSource, /hanstock-signal-period-visibility-bootstrap/);
  assert.match(embedSource, /interval==="1m"\?"off":"on"/);
  assert.match(embedSource, /hanstock-period-change/);
  assert.match(embedSource, /data-hanstock-signal-period="off"[^\n]*CandleChartDialog\.tsx:2288/);
  assert.match(embedSource, /data-hanstock-signal-period="off"[^\n]*#hanstock-signal-overlay/);
});

test("uses the exchange session policy for fetched, native and overlay signals", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");
  assert.ok(embedSource.includes("createKlineSignalSessionBootstrap(ticker, await loadTwseClosedTradingDates())"));
  assert.ok(embedSource.includes("matched=signals.filter(signal=>window.__hanstockIsSignalVisible(signal)"));
  assert.ok(embedSource.includes('window.addEventListener("hanstock-signal-session-change"'));
  assert.ok(runtimeSource.includes('"data-hanstock-native-signal-date":ke[A.i]?.date'));
  assert.ok(embedSource.includes('${signalSessionFilterBootstrap}${stableMarketSignalBootstrap}${coLocatedSignalOverlayBootstrap}'));
});

test("keeps five-minute signal markers in the candle layer coordinate space", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /const coLocatedSignalOverlayBootstrap = signalOverlayBootstrap/);
  assert.match(embedSource, /allGroups=\[\.\.\.document\.querySelectorAll/);
  assert.match(embedSource, /br\.width\*br\.height-ar\.width\*ar\.height/);
  assert.match(embedSource, /groups=svg\?\[\.\.\.svg\.querySelectorAll/);
  assert.match(embedSource, /candleLayer=groups\[0\]\?\.parentElement/);
  assert.match(embedSource, /candleLayer\.appendChild\(overlay\)/);
  assert.match(embedSource, /data-coordinate-space","candle-layer/);
  assert.match(embedSource, /layerPoint=\(node,xAttr,yAttr,layer\)/);
  assert.match(embedSource, /layerMatrix\.inverse\(\)/);
  assert.match(embedSource, /candleByDate=new Map\(groups\.map/);
  assert.match(embedSource, /candle=candleByDate\.get\(signalTime\)/);
  assert.match(embedSource, /nativeMarkers=\[\.\.\.svg\.querySelectorAll/);
  assert.match(embedSource, /window\.__hanstockSignalNativeCount=nativeMarkers\.length/);
  assert.doesNotMatch(embedSource, /if\(nativeMarkers\.length\)\{existing\?\.remove\(\);lastKey=""/);
  assert.doesNotMatch(embedSource, /nativeMarker\.style\.setProperty\("display","none","important"\)/);
  assert.doesNotMatch(embedSource, /window\.__hanstockSignalLayer="native"/);
  assert.match(embedSource, /window\.__hanstockSignalLayer=nativeMarkers\.length\?"merged":"fallback"/);
  assert.match(runtimeSource, /"data-hanstock-bar-date":String\(A\?\.date\?\?""\)/);
  assert.match(embedSource, /\$\{coLocatedSignalOverlayBootstrap\}/);
  assert.doesNotMatch(embedSource, /signalTransformSyncBootstrap \+ \(interval/);
});

test("keeps reference prices out of the MA settings row on every viewport", async () => {
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");

  assert.match(embedSource, /toolbar=document\.querySelector\('[^']*1762[^']*'\)/);
  assert.match(embedSource, /row\.id="hanstock-reference-row"/);
  assert.match(embedSource, /#hanstock-reference-prices\.hanstock-reference-dock\{position:relative!important/);
  assert.doesNotMatch(embedSource, /settings=document\.querySelector\('[^']*1899[^']*'\).*anchor=settings/s);
  assert.doesNotMatch(embedSource, /hanstock-reference-in-flow\{position/);
});

test("opens a persistent four-up workspace with four independent five-minute K-lines", async () => {
  const gridSource = await readFile(new URL("../app/kline-grid/page.tsx", import.meta.url), "utf8");
  const pageSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const klineSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const cssSource = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(gridSource, /hanstock-four-kline-grid-v1/);
  assert.match(gridSource, /DEFAULT_STOCKS[\s\S]*?2330[\s\S]*?2317[\s\S]*?2454[\s\S]*?2382/);
  assert.match(gridSource, /interval=5m/);
  assert.match(gridSource, /Array\.isArray\(stored\) && stored\.length === 4/);
  assert.match(gridSource, /hanstock-battle-open-kline/);
  assert.match(pageSource, /openFourKlineGrid/);
  assert.match(pageSource, /▦ 四格 K 線/);
  assert.match(klineSource, /new URL\("\/kline-grid"/);
  assert.match(cssSource, /\.four-kline-grid\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)[^}]*grid-template-rows:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(cssSource, /@media\(max-width:700px\)[\s\S]*?\.four-kline-grid\{display:flex;flex-direction:column\}/);
});

test("gives every four-grid K-line Windows-style hide maximize and close controls", async () => {
  const gridSource = await readFile(new URL("../app/kline-grid/page.tsx", import.meta.url), "utf8");
  const cssSource = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(gridSource, /type PanelState = "open" \| "minimized" \| "closed"/);
  assert.match(gridSource, /four-kline-window-controls/);
  assert.match(gridSource, /隱藏第 \$\{index \+ 1\} 格/);
  assert.match(gridSource, /放大第 \$\{index \+ 1\} 格/);
  assert.match(gridSource, /關閉第 \$\{index \+ 1\} 格/);
  assert.match(gridSource, /恢復四格/);
  assert.match(gridSource, /visible-\$\{visibleCount\}/);
  assert.match(cssSource, /\.four-kline-card\.is-minimized,\.four-kline-card\.is-closed\{display:none\}/);
  assert.match(cssSource, /\.four-kline-grid\.has-maximized\{grid-template-columns:1fr;grid-template-rows:1fr\}/);
  assert.match(cssSource, /\.four-kline-card\.is-maximized\{grid-column:1\/-1;grid-row:1\/-1\}/);
});

test("switches K-lines by keyboard on larger screens and hides the single-chart shortcut on phones", async () => {
  const klineSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const gridSource = await readFile(new URL("../app/kline-grid/page.tsx", import.meta.url), "utf8");
  const cssSource = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  for (const source of [klineSource, gridSource]) {
    assert.match(source, /window\.addEventListener\("keydown"/);
    assert.match(source, /\/api\/stock-search\?q=/);
    assert.match(source, /event\.key === "Enter"/);
    assert.match(source, /event\.key === "Backspace"/);
    assert.match(source, /event\.key === "Escape"/);
    assert.match(source, /input, textarea, select, \[contenteditable=true\]/);
    assert.match(source, /直接輸入股票代號/);
    assert.match(source, /type="search"/);
    assert.match(source, /enterKeyHint="go"/);
    assert.match(source, /role="search"/);
  }
  assert.match(klineSource, /bindFrameKeyboard\(\)/);
  assert.match(gridSource, /setActiveIndex\(index\)/);
  assert.match(gridSource, /updateStock\(index,/);
  assert.match(gridSource, /keyboardActive=\{keyboardActive && activeIndex === index\}/);
  assert.match(cssSource, /\.four-kline-card\.is-keyboard-active/);
  assert.match(cssSource, /\.kline-quick-switch\{/);
  assert.match(cssSource, /\.kline-quick-switch input\{/);
  assert.match(cssSource, /pointer-events:auto/);
  assert.match(klineSource, /matchMedia\("\(max-width: 700px\)"\)\.matches/);
  assert.match(cssSource, /@media\(max-width:700px\)[\s\S]*?\.kline-quick-switch\{display:none\}/);
});

test("renders one scale-locked session-reset VWAP on one-minute and five-minute K-lines with a visible switch", async () => {
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");
  const embedSource = await readFile(new URL("../app/api/kline-embed/[ticker]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(pageSource, /VWAP 成交量加權均價/);
  assert.match(pageSource, /hanstock-vwap-visibility/);
  assert.match(pageSource, /period !== "day" && showVwap/);
  assert.match(embedSource, /candleReadyBridgeBootstrap\}\$\{vwapVisibilityBridgeBootstrap/);
  assert.doesNotMatch(embedSource, /candleReadyBridgeBootstrap\}\$\{brightVwapOverlayBootstrap/);
  assert.match(embedSource, /#hanstock-vwap-overlay polyline,g\[data-hanstock-vwap\] polyline\{stroke:#fff200!important;stroke-width:10px!important/);
  assert.match(embedSource, /@media\(max-width:820px\)\{#hanstock-vwap-overlay polyline,g\[data-hanstock-vwap\] polyline\{stroke-width:6\.4px!important\}\}/);
  assert.match(embedSource, /hanstock-vwap-visibility-bridge/);
  assert.match(embedSource, /window\.__hanstockVwapVisible/);
  assert.match(runtimeSource, /window\.__hanstockVisibleCandles=ke/);
  assert.match(runtimeSource, /X-HanStock-VWAP-Visible-Candles/);
  assert.match(runtimeSource, /data-hanstock-vwap/);
  assert.match(runtimeSource, /hanstock-vwap-line/);
  assert.match(runtimeSource, /fullHistoryCandlesVar = allCandlesVar/);
  assert.match(runtimeSource, /const fullSeries = fullHistoryCandlesVar \|\| "ke"/);
  assert.match(runtimeSource, /De=\[\.\.\.new Map\(xe\.filter/);
  assert.match(runtimeSource, /Ve\.set\(We,qe\)/);
  assert.match(runtimeSource, /ae\.x\(Ge\).*ae\.y\(Je\)/);
  assert.match(runtimeSource, /we\.push\(ae\.x\(Ge\)\.toFixed\(1\)\+","\+ae\.y\(Je\)\.toFixed\(1\)\)\)\}/);
  assert.match(runtimeSource, /stroke:\"#fff200\",strokeWidth:w\?6\.4:10/);
  assert.match(runtimeSource, /fill:\"#fff200\",stroke:\"#10151b\"/);
  assert.match(runtimeSource, /const withDesktopForceColors = withBrightRuntimeVwap\.replace/);
  assert.match(runtimeSource, /K\+=\(Ze\+Je\+\$e\)\/3\*ta/);
  assert.match(runtimeSource, /X-HanStock-VWAP-Runtime/);
});

test("opens five-minute K-lines with the previous and current sessions so reference lines stay aligned", async () => {
  const source = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(source, /withFullHistoryPan\.replace/);
  assert.match(source, /ct\.at\(-1\)\?\.date\?\.slice\(0,5\)/);
  assert.match(source, /ee=A\?\[\.\.\.new Set\(ct\.map\(Qe=>Qe\.date\?\.slice\(0,5\)\)\.filter\(Boolean\)\)\]:\[\]/);
  assert.match(source, /ee\.at\(-2\)/);
  assert.match(source, /:Math\.max\(0,ct\.length-60\)/);
  assert.match(source, /v\.useLayoutEffect\(\(\)=>\{zn\(null\)/);
  assert.match(source, /v\.useLayoutEffect\(\(\)=>\{if\(!ct\.length\)return/);
  assert.match(source, /"X-HanStock-Initial-Viewport": initialHistoryWindowPatched && initialViewportBeforePaintPatched \? "latest-before-first-paint_home-otc-72" : "0"/);
  assert.match(source, /"X-HanStock-Current-Opening-Label": currentOpeningDividerLabelPatched \? "always" : "0"/);
  assert.match(source, /Ja=hf&&!Mn&&ar>=525&&ar<545,ba=Mn\?va:Ja\?null:ge\.at\(-1\)\?\?null/);
  assert.match(source, /"X-HanStock-Reference-Cutover": referenceSessionCutoverPatched \? "08:45-Asia-Taipei" : "0"/);
});

test("fills incomplete one-minute and five-minute sessions from a second intraday source", async () => {
  const source = await readFile(new URL("../app/api/trpc/[...path]/route.ts", import.meta.url), "utf8");

  assert.match(source, /query2\.finance\.yahoo\.com/);
  assert.match(source, /range", "5d"/);
  assert.match(source, /interval", "1m"/);
  assert.match(source, /priceValues\.every\(\(value\) => typeof value === "number" && Number\.isFinite\(value\)\)/);
  assert.match(source, /aggregateYahooFiveMinuteBars/);
  assert.match(source, /Hub bars win on duplicate timestamps/);
  assert.match(source, /hubLotsToShares\(bar\.volume\)/);
  assert.match(source, /hasSparseMinuteVolume\(latestHubVolumes\)/);
  assert.match(source, /preferCompleteCumulativeVolume\(fallback\?\.volume, bar\.volume\)/);
  assert.match(source, /preferCompleteCumulativeVolume\(current\?\.volume, item\.volume\)/);
  assert.match(source, /fallback: \{ source: "yahoo", history_ok: !hasIncompleteIntradaySession/);
  assert.match(source, /X-HanStock-Kline-Repair-Source/);
  assert.doesNotMatch(source, /const currentPrefix = repaired\[0\]/);
});

test("adds the latest completed trading session as the current daily candle", async () => {
  const source = await readFile(new URL("../app/api/trpc/[...path]/route.ts", import.meta.url), "utf8");
  const runtimeSource = await readFile(new URL("../app/api/kline-runtime/route.ts", import.meta.url), "utf8");

  assert.match(source, /interval: "1m" \| "5m" \| "1d"/);
  assert.match(source, /\["1m", "5m", "1d"\]\.includes\(interval\)/);
  assert.match(source, /function aggregateLatestDailyBar/);
  assert.match(source, /latestTradingDateLabel\(bars\)/);
  assert.match(source, /sumMinuteVolumesToShares/);
  assert.match(source, /aggregateLatestDailyBar\(hubBars, "lots"\)/);
  assert.match(source, /target\.interval === "1d"/);
  assert.match(source, /fetchYahooDailyHistory/);
  assert.match(source, /endpoint\.searchParams\.set\("range", "2y"\)/);
  assert.match(source, /endpoint\.searchParams\.set\("interval", "1d"\)/);
  assert.match(source, /const yahooDailyHistoryPromise = target\.interval === "1d"/);
  assert.match(source, /const tpexDailyHistoryPromise = target\.interval === "1d" && target\.ticker === "OTC"/);
  assert.match(source, /const byDate = new Map\(yahooDaily\.map/);
  assert.match(source, /for \(const bar of hubDaily\) byDate\.set/);
  assert.match(source, /repairWithinInitialBudget\(repairPromise, 5_000\)/);
  assert.match(source, /repairCandle\(bar, interval\)/);
  assert.match(source, /const daily = date\.match/);
  assert.match(runtimeSource, /hanstockFlat=!M&&A\.close===A\.open/);
  assert.match(runtimeSource, /A\.close>hanstockPrev\?"#ef4444"/);
  assert.match(runtimeSource, /Math\.max\(hanstockFlat\?6:1,We-Ge\)/);
  assert.match(runtimeSource, /X-HanStock-Flat-Daily-Candle/);
});

test("returns from the K-line view to its originating feature section", async () => {
  const homeSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const pageSource = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");

  assert.match(homeSource, /url\.searchParams\.set\("returnTo",/);
  assert.match(homeSource, /url\.searchParams\.set\("section", mode\)/);
  assert.match(homeSource, /section === "watchlist"/);
  assert.match(homeSource, /section === "stock-analysis"/);
  assert.match(homeSource, /section === "etf-holdings"/);
  assert.match(pageSource, /get\("returnTo"\)/);
  assert.match(pageSource, /window\.location\.assign\(returnTo\)/);
  assert.match(pageSource, /window\.history\.back\(\)/);
  assert.match(pageSource, /aria-label="返回上一頁"/);
  assert.doesNotMatch(pageSource, /aria-label="返回盤中戰鬥版首頁"/);
});

test("shows explanatory score and trend captions in the battle ranking", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const scoring = await readFile(new URL("../lib/chip-scoring.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /最新分數<small>（今天變強或變弱）<\/small>/);
  assert.match(source, /五日平均<small>（延續性）<\/small>/);
  assert.match(source, /短線綜合<small>（60%／40%）<\/small>/);
  assert.match(source, /每週主力綜合<small>（35%／40%／25%）<\/small>/);
  assert.match(source, /與昨日比較<small>（綜合名次）<\/small>/);
  assert.match(source, /row\.series\.slice\(1\)/);
  assert.match(source, /calculateChipRankMovements/);
  assert.match(source, /chip-rank-move[\s\S]*?row\.rankChange > 0[\s\S]*?↑[\s\S]*?row\.rankChange < 0[\s\S]*?↓/);
  assert.match(styles, /\.chip-rank-move\.up \{ color: var\(--hot\)/);
  assert.match(styles, /\.chip-rank-move\.down \{ color: var\(--good\)/);
  assert.match(source, /calculateCombinedChipScore\(score, fiveDayTrend\)/);
  assert.match(scoring, /todayScore \* 0\.6 \+ fiveDayAverage \* 0\.4/);
  assert.match(source, /今天分數至少 60、五日平均至少 20/);
  assert.match(source, /row\.score >= 60 && row\.fiveDayTrend >= 20/);
  assert.match(source, /row\.score <= -60 && row\.fiveDayTrend <= -20/);
  assert.match(source, /多方雙強/);
  assert.match(source, /空方雙弱/);
  assert.match(styles, /\.chip-sort-label\s*\{/);
  assert.match(styles, /\.chip-sort-label small\s*\{/);
  assert.match(styles, /\.chip-combined-guide\s*\{/);
  assert.match(styles, /\.chip-selection-filter/);
});

test("opens an independent in-app stock chip analysis with the full indicator set", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const mainForceCards = await readFile(new URL("../app/WeeklyMainForceCards.tsx", import.meta.url), "utf8");
  const groupRoute = await readFile(new URL("../app/api/group-chip-members/route.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /selectBottomMode\("stock-analysis"\)/);
  assert.doesNotMatch(source, /window\.location\.assign\("https:\/\/hanstock-chip-score/);
  assert.match(source, /bottomMode !== "chips" && bottomMode !== "stock-analysis"/);
  assert.match(source, /主力、外資、投信、ETF 持股、自營自買、自營避險/);
  assert.match(source, /最近交易日分數/);
  assert.match(source, /五日籌碼分數/);
  assert.match(source, /最近五日趨勢圖/);
  assert.match(source, /ChipMomentumHistogram/);
  assert.match(source, /WEIGHTED CHIP MOMENTUM/);
  assert.match(mainForceCards, /短中線綜合籌碼週趨勢/);
  assert.match(mainForceCards, /主力綜合籌碼週趨勢/);
  assert.match(source, /最近交易日明細/);
  assert.match(source, /主力週綜合/);
  assert.match(source, /短中線綜合/);
  assert.match(source, /score \* 0\.6 \+ weeklyScore \* 0\.4/);
  assert.match(source, /tradingWeekEndDate\(point\.date\)/);
  assert.match(source, /族群籌碼強弱排行/);
  assert.match(source, /籌碼增強前十族群/);
  assert.match(source, /籌碼轉弱前十族群/);
  assert.match(source, /groupChipStrongRows/);
  assert.match(source, /groupChipWeakRows/);
  assert.match(source, /stock-analysis-history-row/);
  assert.match(groupRoute, /stock_groups\.py\?raw/);
  assert.match(groupRoute, /groups\.length >= 60/);
  assert.match(styles, /\.stock-analysis-weights\s*\{/);
  assert.match(styles, /\.stock-analysis-history-scroll\s*\{/);
  assert.match(styles, /\.chip-momentum-card\s*\{/);
  assert.match(styles, /\.group-chip-ranking-grid\s*\{/);
});

test("rejects stale TPEx Hub dates and joins the latest complete TWSE and TPEx trading day", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/market-ranking/route.ts", import.meta.url), "utf8");
  assert.match(route, /\/api\/hub\/official\/tpex-institutional-latest/);
  assert.match(route, /raw\.githubusercontent\.com\/thunghan1228-maker\/HanStock\/main\/data\/tpex-institutional-latest\.json/);
  assert.match(route, /Promise\.any\(attempts\)/);
  assert.match(route, /fetchTpexLatest\(minimumDate: string\)/);
  assert.match(route, /candidate\.date < minimumDate/);
  assert.match(route, /tpex_latest_stale_/);
  assert.match(route, /fetchWithConcurrency\(historyDates, 3, \(date\) => fetchHistoricalDayWithRetry\(date, fetchTwseDay\)\)/);
  assert.match(route, /legacyUrl\.toString\(\)\.replace\(\/&\/g, "%26"\)/);
  assert.match(route, /headers: proxied \? browserHeaders/);
  assert.match(route, /tpex_history_proxy_payload_missing/);
  assert.match(route, /fetchWithConcurrency\(historyDates, 1, \(date\) => fetchHistoricalDayWithRetry\(date, fetchTpexDay\)\)/);
  assert.match(route, /const fallbackSnapshots = \[snapshot, \.\.\.\(snapshotKey === "weekly" \? \[weeklyBaseline\] : \[\]\), \.\.\.durableSnapshots\]/);
  assert.match(route, /filter\(\(date\) => !verifiedDates\.has\(displayDate\(date\)\)\)\.slice\(0, 1\)/);
  assert.match(route, /const backfill = refresh\.startsWith\("backfill-"\)/);
  assert.match(route, /if \(backfill\) return backfillOneMissingDay\(snapshotKey, tradingDays\)/);
  assert.match(route, /historical_backfill_incomplete/);
  assert.match(route, /逐日官方歷史回補並永久保存/);
  assert.match(route, /RANKING_REFRESH_CACHE_MS = 5 \* 60 \* 1000/);
  assert.match(route, /rankingRefreshInFlight/);
  assert.match(route, /latest_complete_joint_market_day_unavailable/);
  assert.doesNotMatch(route, /six_complete_market_days_unavailable/);
  assert.match(route, /tradingDays: Math\.min\(tradingDays, \.\.\.latestRows\.map\(\(row\) => row\.series\.length\)\)/);
  assert.match(route, /const tradingDays = weekly \? 20 : 6/);
  assert.match(route, /snapshotKey = weekly \? "weekly" : "latest"/);
  assert.match(route, /latestTpexResult\.value\.date === dataDate/);
  assert.match(route, /fetchTpexDay\(candidate\)/);
  assert.match(route, /normalizeTpexOpenApiPayload/);
  assert.match(route, /www\.twse\.com\.tw\/rwd\/zh\/fund\/T86/);
  assert.match(route, /const proxyTarget = url\.toString\(\)\.replace\(\/\^https:\/, "http:"\)\.replace\(\/&\/g, "%26"\)/);
  assert.match(route, /normalizeTwsePayload/);
  assert.match(route, /export async function POST/);
  assert.match(route, /buildMarketRankingResponse\(tpexPayload, tradingDays, twsePayload\)/);
  assert.match(route, /latestRows\.length < 600/);
  assert.doesNotMatch(route, /candidate = new Date\(`\$\{tpexResult\.value\.date/);
  assert.match(source, /fetchTpexInstitutionalInBrowser/);
  assert.match(source, /fetchTwseInstitutionalInBrowser/);
  assert.match(source, /refreshMarketRankingWithBrowserOfficialSources/);
  assert.match(source, /if \(refreshed\.snapshotFallback\)/);
  assert.match(source, /twsePayload/);
  assert.match(source, /tpexPayload/);
});

test("keeps the newest successful after-hours ranking durable and never downgrades the client date", async () => {
  const route = await readFile(new URL("../app/api/market-ranking/route.ts", import.meta.url), "utf8");
  const storage = await readFile(new URL("../db/market-ranking-snapshot.ts", import.meta.url), "utf8");
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(route, /readLatestMarketRanking/);
  assert.match(route, /await saveLatestMarketRanking\(payload, snapshotKey\)/);
  assert.match(route, /return latestAvailableResponse\(undefined, snapshotKey\)/);
  assert.match(storage, /CREATE TABLE IF NOT EXISTS market_ranking_latest/);
  assert.match(storage, /WHERE excluded\.data_date >= market_ranking_latest\.data_date/);
  assert.match(page, /current && current\.dataDate > snapshot\.dataDate \? current : snapshot/);
  assert.match(page, /current && current\.dataDate > refreshed\.dataDate \? current : refreshed/);
});

test("shows green only for completed current data and yellow while rankings still wait for an update", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const liveRoute = await readFile(new URL("../app/api/live-ranking/route.ts", import.meta.url), "utf8");
  const autoRefresh = await readFile(new URL("../lib/chip-auto-refresh.ts", import.meta.url), "utf8");
  const worker = await readFile(new URL("../worker/index.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /function isChipRankingCurrent/);
  assert.match(source, /function isLiveRankingCurrent/);
  assert.match(autoRefresh, /今日盤後資料已更新/);
  assert.match(autoRefresh, /盤中沿用上一交易日/);
  assert.match(autoRefresh, /盤後資料整理中/);
  assert.match(source, /chipAutomaticUpdate\.label/);
  assert.match(source, /chipAutomaticUpdate\.detail/);
  assert.match(source, /伺服器盤後每 5 分鐘主動重試/);
  assert.doesNotMatch(source, /status === "fallback" \|\| payload\.snapshotFallback/);
  assert.match(source, /最新更新/);
  assert.match(source, /最新檢查/);
  assert.match(source, /全市場今天盤後籌碼增減排行榜/);
  assert.match(source, /近五日平均盤後籌碼增減排行/);
  assert.doesNotMatch(source, /全市場前一天盤後籌碼增減排行/);
  assert.doesNotMatch(source, /累計五天盤後籌碼增減排行/);
  assert.match(liveRoute, /sourceDate: quotes\.sourceDate \?\? quotes\.fetchedAt/);
  assert.match(styles, /\.data-freshness\.is-pending i/);
  assert.match(styles, /\.group-chip-update-state\.is-ready/);
  assert.match(worker, /shouldTriggerChipServerRefresh\(chipLastServerRefreshAt\)/);
  assert.match(worker, /\/api\/market-ranking/);
  assert.match(worker, /\/api\/broker-branch-daily/);
  assert.match(worker, /ctx\.waitUntil\(Promise\.allSettled/);
});

test("pins stock code and name on phone and iPad while keeping only score trend sorting", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const dailyRoute = await readFile(new URL("../app/api/broker-branch-daily/route.ts", import.meta.url), "utf8");

  assert.match(source, /<span>漲跌幅<\/span>/);
  assert.doesNotMatch(source, /toggleChipSort\("changePercent"\)/);
  assert.match(source, /toggleChipSort\("score"\)/);
  assert.match(source, /toggleChipSort\("fiveDayTrend"\)/);
  assert.match(source, /toggleChipSort\("weeklyMainForceScore"\)/);
  assert.match(source, /toggleChipSort\("dailyBranchNetAmount"\)/);
  assert.match(source, /toggleChipSort\("dailyBranchScore"\)/);
  assert.match(source, /分點主力日買賣超/);
  assert.match(source, /分點主力日分數/);
  assert.match(source, /分點主力日張數/);
  assert.match(source, /formatSigned\(row\.dailyBranchScore, 1, " 分"\)/);
  assert.match(source, /formatSignedLots\(row\.dailyBranchNetLots\)/);
  assert.match(source, /fetch\("\/api\/broker-branch-daily"/);
  assert.match(source, /payload\.rows\.length === 0\) return/);
  assert.match(source, /BROKER_BRANCH_DAILY_SNAPSHOT_KEY/);
  assert.doesNotMatch(dailyRoute, /payload\.complete === false\) return response\(\[\]/);
  assert.match(dailyRoute, /pendingMessage = "資料中心正在回補最新交易日分點資料"/);
  assert.match(dailyRoute, /const stored = await readLatestBrokerBranchDaily\(\)/);
  assert.match(styles, /\.chip-rank-table \{ min-width: 1560px/);
  assert.match(styles, /\.chip-rank-table \{ min-width: 1790px/);
  assert.match(styles, /@media \(max-width: 1180px\)[\s\S]*?\.chip-rank-row > :nth-child\(2\)[\s\S]*?position: sticky/);
  assert.match(styles, /\.chip-rank-row > :nth-child\(3\)[\s\S]*?left: 76px/);
  assert.match(styles, /grid-template-columns: 42px 64px 128px 112px 80px/);
  assert.match(styles, /\.chip-rank-row > :nth-child\(3\) \{ left: 67px; \}/);
});

test("enlarges battle ranking text and tightens columns only on iPhone and iPad", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(styles, /iphone-stock-rank \.rank-device-line \{ min-height: 49px;[^}]*grid-template-columns: 6% 18% 11% 16% 14% 18% 17%;[^}]*gap: 0/);
  assert.match(styles, /iphone-stock-rank \.rank-device-line > \* \{[^}]*font-size: 15\.5px;[^}]*font-weight: 950/);
  assert.match(styles, /ipad-stock-rank \.rank-device-line \{ min-height: 58px; \}/);
  assert.match(styles, /ipad-stock-rank \.rank-device-line > \* \{[^}]*font-size: 18px;[^}]*font-weight: 950/);
});

test("shows each stock's group in after-hours rankings on desktop, iPad, and iPhone", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /groupName: chipGroupByTicker\.get\(row\.code\)/);
  assert.match(source, /bottomMode !== "chips" && bottomMode !== "stock-analysis"/);
  assert.match(source, /<span>所屬族群<\/span>/);
  assert.match(source, /className="chip-group-name"/);
  assert.match(styles, /grid-template-columns: 42px 60px 116px 90px 68px/);
  assert.match(styles, /\.chip-sort-button \{[^}]*width: 100%;[^}]*justify-content: space-between/);
  assert.match(styles, /\.chip-table-scroll \{ width: 100%; overflow-x: auto/);
  assert.match(styles, /\.chip-rank-table \{ min-width: 1560px; width: 100%; \}/);
  assert.match(styles, /grid-template-columns: 44px 68px 142px 120px 82px/);
  assert.match(styles, /grid-template-columns: 42px 64px 128px 112px 80px/);
});

test("adds a Shioaji tick-derived suspected daytrade flow model as a dedicated tab", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/daytrade-brokers/route.ts", import.meta.url), "utf8");
  const settingsSource = await readFile(new URL("../lib/battle-settings.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /疑似隔日沖大單籌碼/);
  assert.doesNotMatch(source, /依券商分點前一日買進/);
  assert.match(source, /疑似隔日沖資金占比＝主動大單買進金額/);
  assert.match(source, /大單淨額資金占比＝大單淨額 ÷ 個股當日成交金額/);
  assert.match(source, /<span>成交金額<\/span><span>大單淨額資金占比<\/span><span>資金占比<\/span>/);
  assert.match(source, /row\.netLargeAmount \/ row\.turnoverAmount \* 100/);
  assert.match(source, /daytrade-net-funding-ratio/);
  assert.match(source, /<span>名稱<\/span><span>漲跌幅<\/span><span>漲跌<\/span>/);
  assert.match(source, /formatSigned\(row\.dayChangePct, 2, "%"\)/);
  assert.match(source, /formatSigned\(row\.dayChange, 2\)/);
  assert.match(route, /reference_price/);
  assert.match(source, /精選名單/);
  assert.match(source, /查看全部/);
  assert.match(source, /完整名單仍永久保存/);
  assert.match(settingsSource, /turnoverAmount: 200_000_000/);
  assert.match(settingsSource, /netLargeAmount: 50_000_000/);
  assert.match(settingsSource, /netBuyRate: 5/);
  assert.match(settingsSource, /suspicionScore: 60/);
  assert.match(settingsSource, /strongDayChangePct: 2/);
  assert.match(settingsSource, /strongLateBuyConcentration: 5/);
  assert.match(source, /fetch\("\/api\/runtime-settings"/);
  assert.match(source, /isSelectedDaytradeRow\(row, thresholds\)/);
  assert.match(source, /selectBottomMode\("daytrade"\)/);
  assert.match(source, /自選股<\/strong>[\s\S]*?三角收斂<\/strong>[\s\S]*?疑似隔日沖大單籌碼<\/strong>[\s\S]*?個股研究中心<\/strong>[\s\S]*?每週籌碼分析<\/strong>[\s\S]*?盤後籌碼排行<\/strong>[\s\S]*?選股程式<\/strong>/);
  assert.match(source, /selectBottomMode\("weekly-chips"\)/);
  assert.match(source, /<WeeklyChipAnalysisPanel/);
  assert.doesNotMatch(source, /\/stock-screener\?tab=radar/);
  assert.match(route, /\/api\/hub\/daytrade-flow-ranking/);
  assert.match(route, /participationRate \/ 25[\s\S]*?\* 40/);
  assert.match(route, /lateBuyConcentration \/ 80[\s\S]*?\* 20/);
  assert.match(route, /priceImpact \/ 5[\s\S]*?\* 15/);
  assert.match(route, /saveDaytradeFlow/);
  assert.match(route, /main_force_data_available/);
  assert.match(route, /force=true/);
  assert.match(source, /mainForceDataAvailable/);
  assert.match(source, /待回補/);
  assert.match(source, /每 30 分鐘自動重試/);
  assert.match(source, /createVisibilityGatedInterval\(\(\) => void load\(false\), 60_000\)/);
  assert.match(source, /load\(true\)/);
  assert.match(route, /不需要券商分點付費資料/);
  assert.match(styles, /\.daytrade-console\s*\{/);
  assert.match(styles, /\.daytrade-view-toggle\s*\{/);
  assert.match(styles, /\.daytrade-data-status\s*\{/);
  assert.match(styles, /grid-template-columns: repeat\(12, minmax\(0, 1fr\)\)/);
  assert.match(styles, /grid-template-rows: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.bottom-nav > button:nth-child\(-n\+4\) \{ grid-column: span 3/);
  assert.match(styles, /\.bottom-nav > button:nth-child\(n\+5\) \{ grid-column: span 3/);
  assert.match(styles, /\.daytrade-row > :nth-child\(-n \+ 3\)\s*\{[^}]*position: sticky/);
  assert.match(styles, /\.daytrade-row > :nth-child\(3\)\s*\{[^}]*left: 88px/);
  assert.match(styles, /\.daytrade-row:not\(\.daytrade-table-head\)\s*\{\s*font-size: 16px/);
  assert.match(styles, /\.bottom-nav > button \{[^}]*color: #fff/);
  assert.match(styles, /\.bottom-nav span \{ font-size: 19px/);
  assert.match(styles, /\.bottom-nav strong \{ font-size: 11\.5px/);
});

test("connects weekly chip analysis to Friday five-day averages for stock and group top twenties", async () => {
  const source = await readFile(new URL("../app/WeeklyChipAnalysisPanel.tsx", import.meta.url), "utf8");
  const trendSource = await readFile(new URL("../app/WeeklyChipStockTrend.tsx", import.meta.url), "utf8");
  const scoring = await readFile(new URL("../lib/weekly-chip-ranking.ts", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/market-ranking/route.ts", import.meta.url), "utf8");
  const historyRoute = await readFile(new URL("../app/api/weekly-chip-history/route.ts", import.meta.url), "utf8");
  const performanceRoute = await readFile(new URL("../app/api/weekly-chip-performance/route.ts", import.meta.url), "utf8");
  const historyDb = await readFile(new URL("../db/weekly-chip-history.ts", import.meta.url), "utf8");
  const brokerRoute = await readFile(new URL("../app/api/broker-branch-weekly/route.ts", import.meta.url), "utf8");
  const mainForceCards = await readFile(new URL("../app/WeeklyMainForceCards.tsx", import.meta.url), "utf8");
  const supplementFactors = await readFile(new URL("../app/StockChipSupplementFactors.tsx", import.meta.url), "utf8");
  const activeEtfRoute = await readFile(new URL("../app/api/active-etf-flow/route.ts", import.meta.url), "utf8");
  const pageSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const mainForceHistoryRoute = await readFile(new URL("../app/api/weekly-main-force-history/route.ts", import.meta.url), "utf8");
  const mainForceHistoryDb = await readFile(new URL("../db/weekly-main-force-history.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /每週籌碼分析/);
  assert.match(route, /market-ranking-snapshot-2026-08-14\.json/);
  assert.match(route, /mergeWeeklySnapshots/);
  assert.match(route, /weekly_verified_baseline/);
  assert.match(source, /performanceLoading \? "計算中" : "待回補"/);
  assert.match(performanceRoute, /AbortSignal\.timeout\(6_000\)/);
  assert.match(source, /依主題切換週報分頁，歷史追蹤保留完整比較/);
  assert.match(source, /上週.*20大.*本週成效/);
  assert.match(source, /<span>上週分數<\/span>/);
  assert.match(source, /<ScoreCell value=\{row\.previousScore\} \/>/);
  assert.match(source, /本週.*20大.*新名單/);
  assert.match(source, /星期五收盤價對星期五收盤價/);
  assert.match(trendSource, /個股歷週五日平均趨勢/);
  assert.match(trendSource, /與上方同一檔股票同步查詢/);
  assert.match(source, /歷史週次強弱變化/);
  assert.match(source, /上週排行｜本週排行/);
  assert.match(source, /<span>上週名次<\/span><span>上週族群<\/span><span>上上週分數<\/span><span>上週分數<\/span><span>上週增減<\/span><span className="weekly-group-current-start">本週名次<\/span><span>本週族群<\/span><span>上週分數<\/span><span>本週分數<\/span><span>本週增減<\/span>/);
  assert.match(source, /WeeklyDeltaCell/);
  assert.match(source, /oldRow\?\.name/);
  assert.match(source, /newRow\?\.name/);
  assert.doesNotMatch(source, /weekly-chip-toolbar/);
  assert.match(trendSource, /weeklyScoreChangePercent/);
  assert.match(source, /summarizeWeeklyPerformance/);
  assert.match(scoring, /weekday\(point\.date\) === 5/);
  assert.match(scoring, /series\.slice\(latestFridayIndex, latestFridayIndex \+ 5\)/);
  assert.match(scoring, /series\.slice\(latestFridayIndex \+ 5, latestFridayIndex \+ 10\)/);
  assert.match(scoring, /buildWeeklyGroupComparisons/);
  assert.match(route, /weeklyRankingRefreshCache/);
  assert.match(route, /saveLatestMarketRanking\(payload, snapshotKey\)/);
  assert.match(historyRoute, /saveWeeklyChipArchives/);
  assert.match(performanceRoute, /interval: "1d"/);
  assert.match(historyDb, /CREATE TABLE IF NOT EXISTS weekly_chip_history/);
  assert.match(historyDb, /CREATE TABLE IF NOT EXISTS weekly_chip_prices/);
  assert.match(source, /<span>分點週分<\/span><span>集保週分<\/span><span>三項綜合<\/span>/);
  assert.match(source, /<span>上週分數<\/span><span>增減<\/span><span>法人週分<\/span><span>分點週分<\/span><span>集保週分<\/span><span>三項綜合<\/span>/);
  assert.match(source, /法人、分點、集保週分怎麼看/);
  assert.match(source, /週淨額 65%＋方向化集中度 35%｜綜合占 40%/);
  assert.match(source, /法人週分 × 35%＋分點週分 × 40%＋集保週分 × 25%/);
  assert.match(source, /缺一項顯示「待三項」，不以 0 分代替/);
  assert.match(source, /className="weekly-chip-code"/);
  assert.match(source, /className="weekly-chip-stock-name"/);
  assert.match(source, /<span>排行<\/span><span>代號<\/span><span>名稱<\/span><span>族群<\/span>/);
  assert.match(source, /法人35%／分點40%／集保25%/);
  assert.match(mainForceCards, /每週主力綜合/);
  assert.match(mainForceCards, /assessWeeklyMainForce/);
  assert.match(mainForceCards, /COMBINED CHIP MOMENTUM/);
  assert.match(mainForceCards, /WEEKLY MAIN FORCE MOMENTUM/);
  assert.match(mainForceCards, /短線五日籌碼 60%＋每週主力綜合 40%/);
  assert.match(mainForceCards, /institutionalScore \* 0\.6 \+ point\.compositeScore \* 0\.4/);
  assert.match(mainForceCards, /weekly-main-force-history/);
  assert.match(mainForceCards, /顯示最近 26 週/);
  assert.match(pageSource, /<BrokerBranchDetailFactor code=\{analyzedStock\.code\} \/>/);
  assert.match(pageSource, /<ActiveEtfDetailFactor code=\{analyzedStock\.code\} \/>/);
  assert.match(supplementFactors, /brokerBranchScore/);
  assert.match(supplementFactors, /當日.*五日.*FinMind/);
  assert.match(activeEtfRoute, /api\/hub\/active-etf-flow/);
  assert.match(mainForceCards, /brokerScore \?\? latestSaved\?\.brokerBranchScore/);
  assert.match(mainForceCards, /const chartHistory = currentPoint \? mergeHistory\(history, \[currentPoint\]\) : history/);
  assert.match(mainForceCards, /<CombinedChipMomentumChart code=\{code\} history=\{chartHistory\} \/>/);
  assert.match(mainForceCards, /<WeeklyMainForceHistoryChart code=\{code\} history=\{chartHistory\} \/>/);
  assert.match(mainForceCards, /readCachedHistory\(code\)/);
  assert.match(mainForceCards, /cacheHistory\(code, next\)/);
  assert.match(source, /savedMainForceRows/);
  assert.match(source, /const loadSavedMainForce = \(\) =>/);
  assert.match(source, /window\.setInterval\(loadSavedMainForce, 30_000\)/);
  assert.match(source, /fetch\("\/api\/weekly-main-force-history"/);
  assert.match(mainForceHistoryRoute, /if \(!ticker\)/);
  assert.match(mainForceHistoryRoute, /readLatestWeeklyMainForceHistory/);
  assert.match(mainForceHistoryDb, /SELECT MAX\(week_end_date\)/);
  assert.match(mainForceHistoryDb, /WHERE excluded\.week_end_date >= \([\s\S]*MAX\(existing\.week_end_date\)[\s\S]*existing\.ticker=excluded\.ticker/);
  assert.match(brokerRoute, /readLatestBrokerBranchWeekly/);
  assert.match(brokerRoute, /new Set\(\["https:\/\/hanstock\.xyz", configuredHub/);
  assert.match(styles, /\.weekly-direct-pair/);
  assert.match(styles, /\.weekly-history-table/);
  assert.match(styles, /\.weekly-stock-trend-result/);
  assert.match(styles, /\.weekly-direct-pair \{ padding: 8px; display: grid; grid-template-columns: repeat\(2,minmax\(0,1fr\)\); gap: 7px; \}/);
  assert.match(styles, /\.weekly-performance-table \{ min-width: 640px; \}\.weekly-current-table \{ min-width: 900px/);
  assert.match(styles, /\.weekly-performance-head,\.weekly-current-head \{[^}]*font-size: 11px/);
  assert.match(styles, /\.weekly-performance-row,\.weekly-current-row \{[^}]*font-size: 12\.5px/);
  assert.match(styles, /\.weekly-performance-head,\.weekly-current-head \{ color: #d7e0e8; font-size: 12px/);
  assert.match(styles, /\.weekly-performance-row \.weekly-chip-name strong,\.weekly-current-row \.weekly-chip-name strong \{ color: #fff; font-size: 17px/);
  assert.match(styles, /\.weekly-group-grid \{ padding: 8px; display: grid; grid-template-columns: repeat\(2,minmax\(0,1fr\)\); gap: 7px; \}/);
  assert.match(styles, /\.weekly-group-compare>header h3 \{ margin: 0; font-size: 20px/);
  assert.match(styles, /\.weekly-group-table \{ min-width: 1040px/);
  assert.match(styles, /\.weekly-group-row button \{ overflow: hidden; border: 0; color: #fff; background: transparent; font-size: 14px/);
  assert.match(styles, /\.weekly-group-current-start \{[^}]*border-left: 3px solid #4da3ff/);
  assert.match(styles, /\.weekly-group-row strong\.positive \{ color: var\(--hot\); \}/);
  assert.match(styles, /\.weekly-group-row strong\.negative \{ color: var\(--good\); \}/);
  assert.match(styles, /\.weekly-main-force-chart \{ grid-column: 1 \/ -1/);
  assert.match(styles, /\.weekly-performance-table,\.weekly-current-table,\.weekly-group-table \{ width: 100%; min-width: 0; \}/);
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*?\.weekly-performance-head>:nth-child\(-n\+3\),\.weekly-current-head>:nth-child\(-n\+3\)/);
  assert.match(styles, /\.weekly-performance-head>:nth-child\(3\),\.weekly-current-head>:nth-child\(3\),\.weekly-performance-row>:nth-child\(3\),\.weekly-current-row>:nth-child\(3\) \{ left: 96px/);
});

test("protects the battle admin backend and keeps its global settings durable", async () => {
  const page = await readFile(new URL("../app/admin/page.tsx", import.meta.url), "utf8");
  const auth = await readFile(new URL("../app/admin/admin-auth.ts", import.meta.url), "utf8");
  const crypto = await readFile(new URL("../lib/admin-crypto.ts", import.meta.url), "utf8");
  const setupRoute = await readFile(new URL("../app/api/admin/setup/route.ts", import.meta.url), "utf8");
  const login = await readFile(new URL("../app/admin/login/LoginForm.tsx", import.meta.url), "utf8");
  const setup = await readFile(new URL("../app/admin/setup/SetupForm.tsx", import.meta.url), "utf8");
  const account = await readFile(new URL("../app/admin/account/AccountForm.tsx", import.meta.url), "utf8");
  const settingsRoute = await readFile(new URL("../app/api/admin/settings/route.ts", import.meta.url), "utf8");
  const refreshRoute = await readFile(new URL("../app/api/admin/refresh/route.ts", import.meta.url), "utf8");
  const schema = await readFile(new URL("../db/schema.ts", import.meta.url), "utf8");
  const migration = await readFile(new URL("../drizzle/0004_flippant_stellaris.sql", import.meta.url), "utf8");

  assert.match(page, /requireBattleAdmin\("\/admin"\)/);
  assert.match(auth, /hanstock_battle_admin/);
  assert.match(auth, /authenticateAdmin/);
  assert.match(auth, /SameSite=Strict/);
  assert.match(crypto, /PBKDF2_ITERATIONS = 100_000/);
  assert.ok(setupRoute.indexOf("createPasswordRecord(password)") < setupRoute.indexOf("consumeAdminSetupToken(tokenHash)"));
  assert.doesNotMatch(auth, /requireChatGPTUser/);
  assert.match(login, /戰鬥版獨立管理員帳號與密碼登入/);
  assert.match(setup, /首次設定管理員/);
  assert.match(account, /修改管理員帳密/);
  assert.match(settingsRoute, /getBattleAdmin/);
  assert.match(settingsRoute, /sameOriginRequest/);
  assert.match(settingsRoute, /writeAdminAudit/);
  assert.match(refreshRoute, /"daytrade-brokers": "\/api\/daytrade-brokers\?force=1"/);
  assert.match(refreshRoute, /sameOriginRequest/);
  assert.match(schema, /"admin_settings"/);
  assert.match(schema, /"admin_audit_logs"/);
  assert.match(schema, /"admin_sessions"/);
  assert.match(schema, /"admin_setup_tokens"/);
  assert.match(migration, /INSERT OR IGNORE INTO `admin_setup_tokens`/);
  assert.doesNotMatch(page, /password|token/i);
  assert.doesNotMatch(page, /ChatGPT/i);
});

test("polls and displays the inclusive 09:00 to 13:30 intraday signal center", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /盤中全部即時訊號｜09:00–13:30/);
  assert.match(source, /signalCenterTime/);
  assert.match(source, /Math\.min\(signal\.barTs, cutoff\)/);
  assert.match(source, /已存快照即時顯示/);
  assert.match(source, /daytradeEarlySell50/);
  assert.match(source, /createVisibilityGatedInterval\(\(\) => void load\(\), 3_000\)/);
  assert.match(source, /daytrade-early-sell\?limit=5000&snapshot=1/);
  assert.match(source, /daytrade-early-sell\?limit=5000&snapshot=1[\s\S]*?AbortSignal\.timeout\(20_000\)/);
  assert.match(source, /liveSignalCollectInFlight\.current/);
  assert.match(source, /fullSignalCollectInFlight\.current/);
  assert.match(source, /fetch\("\/api\/daytrade-early-sell\?collect=immediate"/);
  assert.match(source, /fetch\("\/api\/daytrade-early-sell\?limit=5000"/);
  assert.match(source, /createVisibilityGatedInterval\(\(\) => void collectImmediate\(\), 5_000\)/);
  assert.match(source, /createVisibilityGatedInterval\(\(\) => void collectLive\(\), 20_000\)/);
  assert.match(source, /正在讀取已存訊號/);
  assert.match(source, /先顯示今日快照，背景持續偵測新大單/);
  assert.doesNotMatch(source, /refreshDerivedSignals/);
  assert.match(source, /signal\.tradeDate.*signal\.ticker.*signal\.kind.*signal\.barTs/);
  assert.match(source, /開啟 5 分 K/);
  assert.match(route, /\/api\/hub\/daytrade-early-sell-signals/);
  assert.match(route, /\/api\/hub\/intraday-large-orders/);
  assert.match(route, /X-HanStock-Group-Rankings/);
  assert.match(route, /loadHubSignalSource\(base, 3_000, suppliedGroupRankings\)/);
  assert.match(route, /loadHubSignalSource\(base, 6_000, groupRankings, signal\)/);
  assert.match(route, /loadFullHubSignalSources\(suppliedGroupRankings\)/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("fast"\) === "1"/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("snapshot"\) === "1"/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("collect"\) === "immediate"/);
  assert.match(route, /Server-Timing": "collector;desc=partial-minute-bars"/);
  assert.match(route, /storedSnapshotCache\.clear\(\)/);
  assert.match(route, /snapshot;desc=stored-only/);
  assert.match(route, /readEarlySellSignals\(\{ tradeDate, query, limit: 10_000 \}\)/);
  assert.match(route, /Promise\.allSettled\(HUB_BASES\.map\(\(base\) => loadHubSignalSource\(base, 3_000, suppliedGroupRankings\)\)\)/);
  assert.match(route, /upstreamUnavailable: true/);
  assert.match(route, /fastStoredFallback \?\? await displayFastStoredPayload\(query, limit\)/);
  assert.match(route, /SIGNAL_WINDOW_START = "09:00"/);
  assert.match(route, /SIGNAL_WINDOW_END = "13:30"/);
  assert.match(route, /searchParams\.set\("end", SIGNAL_WINDOW_END\)/);
  assert.match(route, /SIGNAL_GRANULARITY = "1m"/);
  assert.match(route, /searchParams\.set\("interval", SIGNAL_GRANULARITY\)/);
  assert.match(styles, /\.early-sell-toast\s*\{/);
  assert.match(styles, /@media \(max-width:700px\)[\s\S]*?\.early-sell-toast/);
});

test("shows a clearly labelled 2026-08-14 historical early-sell alert demo", async () => {
  const source = readEarlySellSources();
  assert.match(source, /earlySellDemo/);
  assert.match(source, /歷史警示示範｜2026\/08\/14/);
  assert.match(source, /2303.*聯電.*比例 64\.6%/);
  assert.match(source, /歷史示範，不列入今日正式警示紀錄/);
});

test("shows every queued intraday alert in one scrollable draggable panel", async () => {
  const source = readEarlySellSources();
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.doesNotMatch(source, /\.slice\(0, 15\)/);
  assert.match(source, /early-sell-toast-list/);
  assert.match(source, /全部 \$\{popupSignalModes\.size\} 類訊號合併顯示｜共 \$\{popupSignals\.length\} 則/);
  assert.match(source, /已顯示全部 \{popupSignals\.length\} 則/);
  assert.match(source, /EARLY_SELL_TOAST_POSITION_KEY/);
  assert.match(source, /popupInitialSeeded\.current/);
  assert.match(source, /signal\.barTs >= tenDaysAgo/);
  assert.match(source, /const currentSession = current\.filter\(\(signal\) => signal\.tradeDate === payload\.tradeDate\)/);
  assert.match(source, /fitPopupToViewport/);
  assert.match(source, /clampFloatingPanelPosition\(rect\.left, rect\.top, rect\.width, rect\.height\)/);
  assert.match(source, /onPointerDown=\{startPopupDrag\}/);
  assert.match(source, /setPointerCapture\(event\.pointerId\)/);
  assert.match(source, /查看全部／歷史查詢/);
  assert.match(source, /setQueue\(\[\]\)/);
  assert.match(styles, /\.early-sell-toast-list\s*\{[^}]*overflow-y: auto/);
  assert.match(styles, /\.early-sell-toast\.is-dragging/);
  assert.match(styles, /\.early-sell-toast-row\s*\{/);
});

test("auto-pins the live signal panel and hides it after explicitly unpinning", async () => {
  const source = readEarlySellSources();
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /const \[popupPinned, setPopupPinned\] = useState\(true\)/);
  assert.match(source, /EARLY_SELL_PINNED_KEY/);
  assert.match(source, /EARLY_SELL_PINNED_QUEUE_KEY/);
  assert.match(source, /window\.localStorage\.setItem\(EARLY_SELL_PINNED_QUEUE_KEY/);
  assert.match(source, /if \(!popupPinned\) closePopup\(\); openKlineByTicker/);
  assert.match(source, /aria-pressed=\{popupPinned\}/);
  assert.match(source, /📌 已釘選/);
  assert.match(source, /📍 未釘選/);
  assert.match(source, /移出或點外面自動收起/);
  assert.match(source, /onPointerEnter=\{cancelPopupAutoHide\}/);
  assert.match(source, /onPointerLeave=\{schedulePopupAutoHide\}/);
  assert.match(source, /document\.addEventListener\("pointerdown", closeOnOutsidePointer, true\)/);
  assert.doesNotMatch(source, /setTimeout\(\(\) => setQueue\(\[\]\), 15_000\)/);
  assert.match(styles, /\.early-sell-toast\.is-pinned\s*\{/);
  assert.match(styles, /\.early-sell-pin-toggle\s*\{/);
  assert.match(styles, /@media \(max-width:700px\)[\s\S]*?\.early-sell-pin-toggle/);
});

test("resets the intraday popup switch to enabled for every fresh page session", async () => {
  const source = readEarlySellSources();
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /EARLY_SELL_ALERTS_ENABLED_KEY/);
  assert.match(source, /localStorage\.removeItem\(EARLY_SELL_ALERTS_ENABLED_KEY\)/);
  assert.doesNotMatch(source, /localStorage\.setItem\(EARLY_SELL_ALERTS_ENABLED_KEY/);
  assert.match(source, /signalAlertsEnabledRef\.current/);
  assert.match(source, /signalAlertsEnabledRef\.current = true/);
  assert.match(source, /if \(enabled\) \{[\s\S]*?popupInitialSeeded\.current = false/);
  assert.match(source, /freshSignals\.length && signalAlertsEnabledRef\.current/);
  assert.match(source, /signalAlertsEnabled && popupSignals\.length > 0/);
  assert.match(source, /role="switch" aria-checked=\{signalAlertsEnabled\}/);
  assert.match(source, /提醒開啟/);
  assert.match(source, /提醒暫停/);
  assert.match(source, /⏸ 暫停本次提醒/);
  assert.match(styles, /\.early-signal-master-toggle/);
  assert.match(styles, /\.early-signal-master-toggle\.is-on/);
});

test("opens one current K-line as a stable always-on-top document picture-in-picture window", async () => {
  const source = await readFile(new URL("../app/kline/page.tsx", import.meta.url), "utf8");

  assert.match(source, /documentPictureInPicture/);
  assert.match(source, /pictureInPicture\.requestWindow/);
  assert.match(source, /await openPinnedBoard\(\)/);
  assert.doesNotMatch(source, /hostIsAlive/);
  assert.match(source, /螢幕最上層 · 單一固定視圖/);
  assert.match(source, /📌 置頂目前 K 線/);
  assert.match(source, /取消置頂/);
  assert.doesNotMatch(source, /PINNED_CHARTS_KEY|MAX_PINNED_CHARTS|多重釘選/);
});

test("enlarges every intraday signal row by roughly one and a half times without enlarging the bell trigger", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(styles, /\.early-signal-trigger \{[^}]*min-height: 36px;[^}]*font-size: 11px/);
  assert.match(styles, /\.early-sell-toast-row \{[^}]*min-height: 92px/);
  assert.match(styles, /\.early-sell-toast-row time \{[^}]*font-size: 21px/);
  assert.match(styles, /\.early-sell-toast-stock-identity > b \{[^}]*font-size: 19\.5px/);
  assert.match(styles, /\.early-sell-toast-detail > b \{[^}]*font-size: 15px/);
  assert.match(styles, /@media \(max-width:700px\)[\s\S]*?\.early-sell-toast-row \{[^}]*min-height: 80px/);
  assert.match(styles, /@media \(max-width:700px\)[\s\S]*?\.early-sell-toast-stock-identity > b \{ font-size: 16\.5px/);
});

test("frames the popup stock code and name with a thick direction-colored rectangle", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /className="early-sell-toast-stock-identity"><b>\{item\.ticker\}<\/b><strong>\{displayName\}<\/strong><\/span>/);
  assert.match(styles, /\.early-sell-toast-stock-identity \{[^}]*border: 3px solid[^}]*border-radius: 4px/);
  assert.match(styles, /\.early-sell-toast-row\.is-buy \.early-sell-toast-stock-identity[^{]*\{ border-color: #ff5f64/);
  assert.match(styles, /\.early-sell-toast-row\.is-sell \.early-sell-toast-stock-identity \{ border-color: #48cf83/);
});

test("keeps all intraday alerts permanently searchable by date in the D1 signal center", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const storage = await readFile(new URL("../db/early-sell-history.ts", import.meta.url), "utf8");
  const stockSearch = await readFile(new URL("../app/api/stock-search/route.ts", import.meta.url), "utf8");
  const schema = await readFile(new URL("../db/schema.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /盤中訊號中心/);
  assert.match(source, /今日即時/);
  assert.match(source, /歷史查詢/);
  assert.match(source, /輸入代號或名稱/);
  assert.match(source, /EARLY_SELL_CENTER_POSITION_KEY/);
  assert.match(source, /EARLY_SELL_CENTER_PINNED_KEY/);
  assert.match(source, /signalWindowMode/);
  assert.match(source, /url\.searchParams\.set\("signalWindow", "1"\)/);
  assert.match(source, /hanstock-intraday-signal-center/);
  assert.match(source, /↗ 移到另一螢幕/);
  assert.match(source, /child\.focus\(\)/);
  assert.match(source, /centerPinned/);
  assert.match(source, /📌 已釘選/);
  assert.match(source, /!centerPinned && event\.target === event\.currentTarget/);
  assert.match(source, /centerMode === "history"[\s\S]*?\? !historyLoading/);
  assert.match(source, /signalPollInFlight\.current/);
  assert.match(source, /historyDateChanged\.current/);
  assert.match(source, /onPointerDown=\{startCenterDrag\}/);
  assert.match(source, /setPointerCapture\(event\.pointerId\)/);
  assert.match(source, /clampSignalCenterPosition/);
  assert.match(source, /可往上、下、左、右拖曳視窗/);
  assert.match(source, /signalTs/);
  assert.match(source, /所屬族群/);
  assert.match(source, /toneForChange/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("date"\)/);
  assert.match(route, /saveEarlySellSignals/);
  assert.match(route, /readEarlySellSignals/);
  assert.match(route, /resolveIntradaySignalDisplayDate/);
  assert.match(route, /displayStoredPayload/);
  assert.match(route, /if \(tradeDate !== taipeiTradeDate\(\)\)/);
  assert.match(storage, /CREATE TABLE IF NOT EXISTS early_sell_signals/);
  assert.match(storage, /ON CONFLICT\(trade_date, ticker, kind, bar_ts\)/);
  assert.match(storage, /DELETE FROM early_sell_signals\s+WHERE trade_date = \? AND kind = \? AND ticker IN/);
  assert.match(storage, /\.bind\(tradeDate, kind, \.\.\.batch\)/);
  assert.doesNotMatch(storage, /DELETE FROM early_sell_signals[^\x60]*?(?:bar_ts|updated_at)\s*</);
  assert.doesNotMatch(storage, /DROP TABLE/);
  assert.match(schema, /earlySellSignals/);
  assert.match(stockSearch, /searchParams\.get\("tickers"\)/);
  assert.match(stockSearch, /changePct/);
  assert.match(styles, /\.early-signal-center\s*\{/);
  assert.match(styles, /\.early-signal-center\.is-positioned \{ position: fixed/);
  assert.match(styles, /\.early-signal-center-head \{[^}]*cursor: grab/);
  assert.match(styles, /\.early-signal-backdrop\.is-pinned \{ pointer-events: none/);
  assert.match(styles, /\.early-signal-center \{[^}]*width: min\(1180px, calc\(100vw - 32px\)\)/);
  assert.match(styles, /\.early-signal-center \{[^}]*height: min\(840px, calc\(100dvh - 32px\)\)/);
  assert.match(styles, /\.early-signal-stock-meta \{[^}]*font-size: 18px/);
  assert.match(styles, /grid-template-columns: 68px minmax\(200px,\.9fr\) minmax\(320px,2\.2fr\) 86px 82px/);
  assert.match(styles, /\.early-signal-center\.is-pinned/);
  assert.match(styles, /\.early-signal-backdrop\.is-detached/);
  assert.match(styles, /\.early-signal-center\.is-detached/);
  assert.match(styles, /\.early-signal-stock-meta\.positive[^{]*\{ color: var\(--hot\)/);
  assert.match(styles, /\.early-signal-stock-meta\.negative[^{]*\{ color: var\(--good\)/);
  assert.match(styles, /@media \(max-width: 900px\)[\s\S]*?\.early-signal-center/);
});

test("keeps successful intraday signal rows visible across partial refreshes", async () => {
  const source = readEarlySellSources();

  assert.match(source, /function mergePermanentSignalRows/);
  assert.match(source, /if \(incoming\.length === 0\) return retained/);
  assert.match(source, /setTodaySignals\(\(current\) => mergePermanentSignalRows/);
  assert.match(source, /setInstantLargeSignals\(\(current\) => mergePermanentSignalRows/);
  assert.doesNotMatch(source, /setTimeout\(\(\) => setQueue\(\[\]\), 15_000\)/);
  assert.match(source, /移出或點外面自動收起/);
});

test("gives 12空 and 1+2多 separate five-minute signal tabs with counts and early-session backfill", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(source, /"fiveMinuteTwelveShort" \| "fiveMinuteOnePlusTwoLong"/);
  assert.match(source, /centerMode === "fiveMinuteTwelveShort" \? combinedTodaySignals\.filter\(isFiveMinuteTwelveShortSignal\)/);
  assert.match(source, /centerMode === "fiveMinuteOnePlusTwoLong" \? combinedTodaySignals\.filter\(isFiveMinuteOnePlusTwoLongSignal\)/);
  assert.match(source, /12空（五分K） <b>\{coreCount\(fiveMinuteTwelveShortSignalTotal\)\}/);
  assert.match(source, /1\+2多（五分K） <b>\{coreCount\(fiveMinuteOnePlusTwoLongSignalTotal\)\}/);
  assert.match(source, /今日由 09:00 起完整回補/);
  assert.match(source, /正在從今天 09:00 起回掃完整五分K/);
  assert.match(route, /calculateFiveMinutePatternSignals\([\s\S]*?barsByTicker/);
  assert.match(route, /if \(patternSignals\.length > 0\) await saveEarlySellSignals\(patternSignals\)/);
  assert.match(styles, /button\.five-minute-twelve-short\.active/);
  assert.match(styles, /button\.five-minute-one-plus-two-long\.active/);
});

test("adds a dedicated live daily-triangle list and merges its three alert states into the signal center", async () => {
  const page = await readFile(new URL("../app/triangles-intraday/page.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/triangles-intraday/route.ts", import.meta.url), "utf8");
  const home = readEarlySellSources();
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(page, /盤中日線三角收斂/);
  assert.match(page, /每 5 分鐘重新判斷/);
  assert.match(page, /\["全部", "放量突破", "突破待量", "接近突破"\]/);
  assert.match(page, /setInterval\(\(\) => void load\(\), 30_000\)/);
  assert.match(route, /\/api\/screener\/triangles\/intraday\/latest/);
  assert.match(home, /triangleNearBreakout/);
  assert.match(home, /triangleBreakoutPendingVolume/);
  assert.match(home, /triangleVolumeBreakout/);
  assert.match(home, /\/triangles-intraday/);
  assert.match(styles, /\.early-sell-toast-row\.is-triangle-near,\.early-sell-toast-row\.is-triangle-pending,\.early-sell-toast-row\.is-triangle-volume \{ border-left: 4px solid #ff5f64/);
  assert.match(styles, /\.early-signal-row\.is-triangle-near,\.early-signal-row\.is-triangle-pending,\.early-signal-row\.is-triangle-volume \{ border-left: 4px solid #ff5f64/);
  assert.match(styles, /\.triangle-live-row\.status-放量突破[\s\S]*?\.triangle-live-row\.status-突破待量[\s\S]*?\.triangle-live-row\.status-接近突破[\s\S]*?color: #ff7775/);
});

test("calculates and displays today's four-gate intraday signals without a fixed replay", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const calculator = await readFile(new URL("../lib/four-gate-signals.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.doesNotMatch(source, /FOUR_GATE_REPLAY_20260820/);
  assert.doesNotMatch(source, /fourGateDemo/);
  assert.doesNotMatch(source, /四項精選回放｜2026\/08\/20/);
  assert.doesNotMatch(source, /1,015 則縮減為 16 則、14 檔/);
  assert.match(source, /主力累計 A～D 同步濾網/);
  assert.match(source, /setFourGateSignals\(\(current\) => mergePermanentSignalRows\(current, fourGate, payload\.tradeDate\)\)/);
  assert.match(source, /const fourGateSignalTotal = strictFourGateSignals\.length/);
  assert.match(source, /四項精選 <b>\{coreCount\(fourGateSignalTotal\)\}/);
  assert.match(source, /四項精選即時訊號/);
  assert.match(route, /\/api\/hub\/bars1m\/batch/);
  assert.match(route, /\/api\/hub\/bars1m\/\$\{encodeURIComponent\(ticker\)\}/);
  assert.match(route, /loadCompleteMinuteBars\(base, fourGateTickers, barsByTicker\)/);
  assert.match(route, /calculateLiveDerivedSignals/);
  assert.match(route, /calculateFourGateSourceSignalsFromMinuteBars\(tradeDate, baselines, barsByTicker\)/);
  assert.match(route, /fourGateSignals = calculateFourGateSignals\(fourGateSourceSignals, completeBarsByTicker\)/);
  assert.match(route, /saveEarlySellSignals\(\[\.\.\.signals, \.\.\.fourGateSignals\]\)/);
  assert.match(route, /saveEarlySellSignals\(\[\.\.\.liveSignals, \.\.\.fourGateSignals, \.\.\.mainForceSignals, \.\.\.extraLargeSellSignals, \.\.\.extraLargeBuySignals\]\)/);
  assert.match(calculator, /pressureThresholdForTimestamp/);
  assert.match(calculator, /MIN_PREVIOUS_ESTIMATED_SELL_PRESSURE_AMOUNT = 100_000_000/);
  assert.match(calculator, /TODAY_IMMEDIATE_MIN_PRESSURE_RATIO = 200/);
  assert.match(route, /pressureAmountEligibleSignals = normalizeSignals\(payload\?\.signals\)\.filter\(passesPreviousEstimatedSellPressureFilter\)/);
  assert.match(route, /liveSignals = pressureAmountEligibleSignals[\s\S]*?filter\(passesTodayImmediatePressureRatioFilter\)/);
  assert.match(route, /calculateLiveDerivedSignals\(base, \[\.\.\.pressureAmountEligibleSignals, \.\.\.fallback\.fourGateSourceSignals\], liveTradeDate\)/);
  assert.match(source, /前日預估賣壓金額 > 1 億元/);
  assert.match(source, /12空須完成 1高、破惡均下彎、2不過1高/);
  assert.match(source, /1\+2多須同時站上昨日高與 905高/);
  assert.match(calculator, /netRatio >= 50 && previousNetRatio > 0/);
  assert.match(calculator, /price > vwap && price > firstHigh/);
  assert.match(calculator, /強多主力大單（四項通過）/);
  assert.match(calculator, /強空主力大單（四項通過）/);
  assert.match(source, /隔日沖加強/);
  assert.match(source, /全市場掃描/);
  assert.match(source, /全市場＋隔日沖加強/);
  assert.match(styles, /\.early-signal-source\.is-previous-day/);
  assert.match(styles, /\.early-signal-source\.is-full-market/);
  assert.match(styles, /\.early-signal-source\.is-both/);
  assert.match(styles, /\.early-signal-tabs \{[^}]*grid-template-columns: repeat\(7, minmax\(0, 1fr\)\)/);
});

test("adds a permanent intraday extra-large sell tab using the previous positive net amount", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const calculator = await readFile(new URL("../lib/intraday-extra-large-sell.ts", import.meta.url), "utf8");
  const flowStorage = await readFile(new URL("../db/daytrade-flow.ts", import.meta.url), "utf8");
  const signalStorage = await readFile(new URL("../db/early-sell-history.ts", import.meta.url), "utf8");
  const groupRanks = await readFile(new URL("../lib/main-force-group-ranks.ts", import.meta.url), "utf8");
  const chipRanks = await readFile(new URL("../lib/main-force-chip-ranks.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(calculator, /INTRADAY_EXTRA_LARGE_SELL_LABEL = "盤中大單賣出達前日大單淨額"/);
  assert.match(calculator, /cumulativeSell < threshold/);
  assert.match(calculator, /baseline\.dataDate >= tradeDate/);
  assert.match(calculator, /INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT = 50_000_000/);
  assert.match(calculator, /INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE = 10/);
  assert.match(calculator, /threshold <= INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT/);
  assert.match(calculator, /net \/ turnover \* 100/);
  assert.match(calculator, /netFundingRate <= INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE/);
  assert.match(calculator, /大單淨額（隔日沖）資金占比/);
  assert.match(flowStorage, /readPreviousDaytradeFlow/);
  assert.match(route, /EXTRA_LARGE_SELL_SIGNAL_KINDS/);
  assert.match(route, /backfillExtraLargeSellSignals/);
  assert.match(route, /request\.nextUrl\.searchParams\.get\("collect"\) === "extra-large"/);
  assert.match(route, /resolveIntradaySignalDisplayDate\(dates, new Date\(\), closedDates\)/);
  assert.match(source, /collectExtraLargeBackfill/);
  assert.match(source, /daytrade-early-sell\?collect=extra-large&limit=5000/);
  assert.match(route, /readIntradayForceForTickers/);
  assert.match(route, /loadStoredMinuteBars/);
  assert.match(route, /extraLargeSellSignals/);
  assert.match(route, /extraLargeSellNetFundingRate/);
  assert.match(route, /netFundingRate > INTRADAY_EXTRA_LARGE_SELL_MIN_NET_FUNDING_RATE/);
  assert.match(signalStorage, /intradayExtraLargeSell/);
  assert.match(groupRanks, /intradayExtraLargeSell/);
  assert.match(groupRanks, /kind === "intradayExtraLargeSell"/);
  assert.match(groupRanks, /ranking: "strong" as const, direction: "漲幅" as const/);
  assert.match(groupRanks, /ranking: "weak" as const, direction: "跌幅" as const/);
  assert.match(chipRanks, /intradayExtraLargeSell/);
  assert.match(source, /🟢 盤中特大賣單/);
  assert.match(source, /centerMode === "extraLargeSell" \? extraLargeSellSignals/);
  assert.match(source, /盤中大單賣出累計 ≥ 前日大單淨買超/);
  assert.match(source, /前日大單淨買超 > 5,000 萬元/);
  assert.match(source, /隔日沖淨額資金占比 > 10%/);
  assert.match(source, /同步標示族群漲跌前 10 與前日籌碼減少前 100/);
  assert.match(source, /前日籌碼衰退/);
  assert.match(source, /chipRank\.rank.*formatSigned\(chipRank\.score, 1\).*分/);
  assert.match(source, /intraday-extra-large-sell-note/);
  assert.match(source, /is-extra-large-sell/);
  assert.match(styles, /\.early-signal-row\.is-extra-large-sell,\.early-signal-row\.is-extra-large-buy \{[^}]*grid-template-columns:/);
  assert.match(styles, /\.intraday-extra-large-sell-note \{[^}]*color: #f0f6fb;[^}]*font-size: 14px;[^}]*white-space: pre-line/);
  assert.match(styles, /\.intraday-extra-large-sell-badge \{[^}]*color: #8affba !important;[^}]*font-size: 24px/);
  assert.match(styles, /\.early-sell-toast-detail \.early-signal-title-line > b\.intraday-extra-large-sell-badge \{ font-size: 30px; \}/);
});

test("adds the eighth personal intraday tracker with selectable signal sources and mirrored extra-large buys", async () => {
  const home = readEarlySellSources();
  const tracker = await readFile(new URL("../app/IntradayStockTrackingPanel.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const calculator = await readFile(new URL("../lib/intraday-extra-large-sell.ts", import.meta.url), "utf8");
  assert.match(home, /個股盤中訊號追蹤/);
  assert.match(home, /intraday-tracking/);
  assert.match(tracker, /不限檔數追蹤自選個股/);
  assert.match(tracker, /盤中大單買進 200%/);
  assert.match(tracker, /盤中特大買單/);
  assert.match(tracker, /族群瞬間大單/);
  assert.match(tracker, /🐋 盤中大戶力/);
  assert.match(tracker, /payload\.largeForceSignals/);
  assert.match(tracker, /payload\.instantLargeSignals/);
  assert.match(tracker, /signal\.kind === "instantLargeBuy" \|\| signal\.kind === "instantLargeSell"/);
  assert.match(tracker, /追蹤提醒開啟/);
  assert.match(tracker, /個股追蹤提醒/);
  assert.match(tracker, /setAlerts\(\[\]\);\s*onOpenTracker\(\)/);
  assert.match(tracker, /id="intraday-stock-tracking"/);
  assert.match(home, /IntradayTrackingNotifier/);
  assert.match(home, /getElementById\("intraday-stock-tracking"\)/);
  assert.match(home, /scrollIntoView\(\{ behavior: "smooth", block: "start" \}\)/);
  assert.match(tracker, /setInterval\(\(\) => void load\(\), 5_000\)/);
  assert.match(calculator, /INTRADAY_EXTRA_LARGE_BUY_LABEL/);
  assert.match(route, /extraLargeBuySignals/);
  assert.match(home, /🔴 盤中特大買單/);
  assert.match(home, /🐋 盤中大戶力/);
  assert.match(home, /centerMode === "largeForce" \? largeForceAjSignals/);
  assert.match(home, /盤中大戶力多空達標條件/);
  assert.match(home, /多方大戶力達標/);
  assert.match(home, /空方大戶力達標/);
  assert.doesNotMatch(home, /AJ 多空|AJ 多方|AJ 空方|AJ 族群|套用 AJ/);
  assert.match(home, /largeForceStageGroups/);
  assert.match(route, /calculateIntradayLargeForceSignals/);
  assert.match(route, /saveIntradayLargeForceMonitorRows/);
  assert.match(home, /centerMode === "extraLargeBuy" \? extraLargeBuySignals/);
  assert.match(home, /盤中大單買進累計 ≥ 前日大單淨賣超/);
  assert.match(home, /前日大單淨賣超 > 5,000 萬元/);
  assert.match(calculator, /net < -INTRADAY_EXTRA_LARGE_SELL_MIN_PREVIOUS_NET_AMOUNT/);
});

test("shows the actual instant-large signal count in the signal-center badge", async () => {
  const home = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  assert.match(home, /instantLargeCollector\?:/);
  assert.match(home, /daytrade-early-sell\?collector=1/);
  assert.match(home, /if \(payload\.instantLargeCollector\) setInstantLargeCollector/);
  assert.match(home, /instantLargeSignalTotal = instantLargeSignals\.filter\(isInstantLargeSignal\)\.length/);
  assert.match(home, /coreCount\(instantLargeSignalTotal\)/);
  assert.doesNotMatch(home, /coreCount\(instantLargeCandidateTotal\)/);
  assert.match(home, /candidateTickCount\?: number/);
  assert.match(home, /eligibleTickCount\?: number/);
  assert.match(home, /burstThresholdCount\?: number/);
  assert.match(home, /pendingSignalCount\?: number/);
  assert.match(home, /候選名單已建立，但後台尚未收到這些股票的逐筆成交/);
  assert.match(home, /同一秒累計達 100 張或 3,000 萬元/);
  assert.match(home, /className="instant-large-diagnostics"/);
  assert.match(home, /候選逐筆/);
  assert.match(home, /單筆達標/);
  assert.match(home, /同秒達標/);
  assert.match(home, /待補存/);
  assert.match(route, /loadInstantLargeCollectorStatus/);
  assert.match(route, /searchParams\.get\("collector"\) === "1"/);
  assert.match(route, /\/api\/hub\/intraday-large-orders/);
});

test("applies the A-D synchronized main-force filter and keeps every qualified signal permanent", async () => {
  const source = readEarlySellSources();
  const route = await readFile(new URL("../app/api/daytrade-early-sell/route.ts", import.meta.url), "utf8");
  const storage = await readFile(new URL("../db/early-sell-history.ts", import.meta.url), "utf8");
  const calculator = await readFile(new URL("../lib/main-force-zero-signals.ts", import.meta.url), "utf8");
  const groupRanks = await readFile(new URL("../lib/main-force-group-ranks.ts", import.meta.url), "utf8");
  const chipRanks = await readFile(new URL("../lib/main-force-chip-ranks.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");

  assert.match(route, /calculateLiveMainForceZeroSignals/);
  assert.match(route, /MAIN_FORCE_BATCH_SIZE = 40/);
  assert.match(route, /mainForceScan: mainForceScanStatus/);
  assert.match(route, /hasPotentialMainForceABCDSignal/);
  assert.match(route, /query2\.finance\.yahoo\.com/);
  assert.match(route, /noteIncludes: MAIN_FORCE_FILTER_MARKER/);
  assert.match(calculator, /FORMAL_START_MINUTE = 9 \* 60 \+ 5/);
  assert.match(calculator, /stableBars >= 2/);
  assert.match(calculator, /MIN_AMOUNT_RATE_PCT = 1/);
  assert.match(calculator, /SYNC_WINDOW_MS = 3 \* 60 \* 1_000/);
  assert.match(calculator, /MIN_DISTANCE_PCT = 0\.2/);
  assert.match(calculator, /MAX_DISTANCE_PCT = 1\.2/);
  assert.match(calculator, /VOLUME_MULTIPLE = 1\.5/);
  assert.match(calculator, /emittedDirections/);
  assert.match(calculator, /A～D同步濾網 V1/);
  assert.match(calculator, /主力累計強勢翻多/);
  assert.match(calculator, /主力累計強勢翻空/);
  assert.match(source, /全市場掃描・主力累計/);
  assert.match(source, /訊號依交易日永久保留/);
  assert.match(source, /const mainForceSignalTotal = mainForceSignals\.length/);
  assert.doesNotMatch(source, /if \(signal\.officialGroupSelection\) return true;/);
  assert.match(source, /const strong = groupRankings\?\.strong\?\.length \? groupRankings\.strong : signalGroupRankings\?\.strong/);
  assert.match(source, /主力累計 <b>\{coreCount\(mainForceSignalTotal\)\}/);
  assert.match(source, /mainForceStrongBullish.*main-force-strong-badge is-bullish/);
  assert.match(source, /mainForceStrongBearish.*main-force-strong-badge is-bearish/);
  assert.match(source, /className=\{intradaySignalStrongBadge\(item\.kind\)\}/);
  assert.match(styles, /\.main-force-strong-badge\.is-bullish \{[^}]*color: #fff !important; background: #d92731;/);
  assert.match(styles, /\.main-force-strong-badge\.is-bearish \{[^}]*color: #fff !important; background: #13824c;/);
  assert.match(source, /centerMode === "mainForce" \? mainForceSignals/);
  assert.match(route, /mainForceSignals,/);
  assert.match(storage, /kind IN/);
  assert.match(storage, /note LIKE/);
  assert.match(storage, /ON CONFLICT\(trade_date, ticker, kind, bar_ts\) DO UPDATE SET/);
  assert.match(storage, /COUNT\(DISTINCT ticker\) AS stockCount/);
  assert.match(route, /readEarlySellSignalSummary\(tradeDate, \{ mainForceNoteIncludes: MAIN_FORCE_FILTER_MARKER \}\)/);
  assert.match(source, /const todaySignalTotal = combinedTodaySignals\.length/);
  assert.match(route, /request\.headers\.get\("x-hanstock-group-rankings"\)/);
  assert.match(route, /AbortSignal\.timeout\(12_000\)/);
  assert.doesNotMatch(route, /new URL\("\/api\/live-ranking", origin\)/);
  assert.match(route, /annotateMainForceGroupRanks\(liveSignals, signalGroupsByTicker, groupRankings\)/);
  assert.match(route, /const groupedSignals = keepQualifiedGroupSignals\(filteredRawStoredSignals\)/);
  assert.match(route, /if \(hasCapturedMatchingTopGroup\(signal\)\) return \[signal\]/);
  assert.match(route, /clientGroupRankings\(request\)/);
  assert.match(route, /suppliedGroupRankings[\s\S]*?Promise\.resolve\(suppliedGroupRankings\)[\s\S]*?loadSignalGroupRankings\(\)/);
  assert.match(source, /X-HanStock-Group-Rankings/);
  assert.match(source, /groupRankingsRef\.current/);
  assert.match(storage, /early_sell_signals\.note LIKE '%｜族群同步 %'/);
  assert.match(storage, /excluded\.note NOT LIKE '%｜族群同步 %'/);
  assert.match(route, /displayStoredPayload\(query, limit, groupRankings\)/);
  assert.match(route, /annotatePreviousChipRanks\(groupedSignals, tradeDate\)/);
  assert.match(source, /A～D 同步發動濾網 · 09:05 起正式顯示/);
  assert.match(route, /groupRankings,/);
  assert.match(source, /signalGroupRankings/);
  assert.match(source, /signal\.tradeDate !== taipeiTradeDate\(\)/);
  assert.match(route, /SIGNAL_GROUP_RANK_LIMIT = 10/);
  assert.match(groupRanks, /mainForceStrongBullish/);
  assert.match(groupRanks, /return \{ ranking: "strong" as const, direction: "漲幅" as const \}/);
  assert.match(groupRanks, /mainForceStrongBearish/);
  assert.match(groupRanks, /return \{ ranking: "weak" as const, direction: "跌幅" as const \}/);
  assert.match(groupRanks, /MAIN_FORCE_GROUP_RANK_LIMIT = 10/);
  assert.match(source, /groupRank\.group.*groupRank\.direction.*groupRank\.rank/);
  assert.match(source, /`前日綜合\$\{chipRank\.direction\}`/);
  assert.match(source, /intradaySignalChipRankText\(item, chipRank\)/);
  assert.match(source, /extractMainForceChipRank/);
  assert.match(route, /buildPreviousSessionChipTopRanks/);
  assert.match(route, /annotatePreviousChipRanks/);
  assert.match(chipRanks, /MAIN_FORCE_CHIP_RANK_LIMIT = 100/);
  assert.match(chipRanks, /calculateCombinedChipScore\(todayScore, fiveDayAverage\)/);
  assert.match(storage, /appendEarlySellChipRankAnnotations/);
  assert.match(styles, /\.main-force-chip-rank\.is-increasing \{[^}]*border-color: #e1b84c/);
  assert.match(styles, /\.main-force-chip-rank\.is-decreasing \{[^}]*border-color: #50cf8a/);
  assert.match(styles, /\.main-force-group-rank\.is-bullish/);
  assert.match(styles, /\.main-force-group-rank\.is-bearish/);
  assert.match(source, /intradaySignalGroupRankClass/);
  assert.match(source, /groupRank\.direction === "漲幅" && groupRank\.rank <= 5/);
  assert.match(source, /groupRank\.direction === "跌幅" && groupRank\.rank <= 10/);
  assert.match(styles, /\.main-force-group-rank\.is-bullish\.is-top-five[^}]*color:#fff[^}]*background:#a82730/);
  assert.match(styles, /\.main-force-group-rank\.is-bearish\.is-bottom-ten[^}]*color:#fff[^}]*background:#147344/);
  assert.match(storage, /early_sell_signals\.note LIKE '%｜族群同步 %'/);
  assert.match(storage, /DELETE FROM early_sell_signals\s+WHERE trade_date = \? AND kind = \? AND ticker IN/);
  assert.match(storage, /\.bind\(tradeDate, kind, \.\.\.batch\)/);
  assert.doesNotMatch(storage, /DELETE FROM early_sell_signals[^\x60]*?(?:bar_ts|updated_at)\s*</);
});

test("keeps watchlists synchronized across all devices", async () => {
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const client = await readFile(new URL("../app/watchlist-cloud-sync.tsx", import.meta.url), "utf8");
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/watchlists/route.ts", import.meta.url), "utf8");
  const quotesRoute = await readFile(new URL("../app/api/quotes/route.ts", import.meta.url), "utf8");
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const migration = await readFile(new URL("../drizzle/0007_charming_tenebrous.sql", import.meta.url), "utf8");

  assert.match(layout, /<WatchlistCloudSync \/>/);
  assert.match(client, /detectDeviceKind/);
  assert.doesNotMatch(client, /"desktop-required"/);
  assert.match(client, /hanstock-watchlists-local-changed/);
  assert.match(client, /addEventListener\("storage", onStorage\)/);
  assert.match(client, /setInterval\(\(\) => void synchronize\(\), 4_000\)/);
  assert.match(route, /getChatGPTUser\(\)/);
  assert.match(route, /initializeWatchlistState/);
  assert.match(route, /updateWatchlistState/);
  assert.doesNotMatch(route, /deviceKind !== "desktop"/);
  assert.match(page, /\/api\/quotes\?items=\$\{encodeURIComponent\(items\)\}&refresh=\$\{Date\.now\(\)\}&preferOfficialLive=1/);
  assert.match(page, /createVisibilityGatedInterval\(\(\) => void load\(\), 3_000\)/);
  assert.doesNotMatch(page, /useIntradayForceHomework/);
  assert.doesNotMatch(page, /大戶力多空功課|即時前20多／空|盤前／盤後觀察|盤後情報/);
  assert.match(page, /六組自選清單可自由加入、移動與管理股票/);
  assert.match(page, /useWatchlistDetails\(activeTickerKey\)/);
  assert.match(page, /行情更新/);
  assert.match(quotesRoute, /LIVE_QUOTE_HEADERS = \{ "Cache-Control": "private, no-store, max-age=0" \}/);
  assert.match(quotesRoute, /preferOfficialLive/);
  assert.match(styles, /\.watchlist-compact-table \{[^}]*table-layout: fixed/);
  assert.match(styles, /:is\(th, td\):nth-child\(5\)[^}]*text-align: right/);
  assert.match(page, /<colgroup>[\s\S]*watchlist-col-group[\s\S]*watchlist-col-actions[\s\S]*<\/colgroup>/);
  assert.doesNotMatch(page, /watchlist-col-force|<th scope="col">盤中大戶力<\/th>/);
  assert.match(migration, /CREATE TABLE `watchlist_sync_state`/);
});

test("synchronizes every intraday tracking setting and tracked list across devices", async () => {
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const panel = await readFile(new URL("../app/IntradayStockTrackingPanel.tsx", import.meta.url), "utf8");
  const client = await readFile(new URL("../app/intraday-tracking-cloud-sync.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/intraday-tracking/route.ts", import.meta.url), "utf8");
  const storage = await readFile(new URL("../db/intraday-tracking.ts", import.meta.url), "utf8");
  const migration = await readFile(new URL("../drizzle/0012_perfect_spacker_dave.sql", import.meta.url), "utf8");

  assert.match(layout, /<IntradayTrackingCloudSync \/>/);
  assert.match(client, /\/api\/intraday-tracking/);
  assert.match(client, /INTRADAY_TRACKING_LOCAL_CHANGED_EVENT/);
  assert.match(client, /INTRADAY_TRACKING_PENDING_KEY/);
  assert.match(client, /INTRADAY_TRACKING_MIGRATED_KEY/);
  assert.match(client, /setInterval\(\(\) => void synchronize\(\), 4_000\)/);
  assert.match(client, /所有裝置已同步/);
  assert.match(panel, /INTRADAY_TRACKING_SYNC_STATUS_EVENT/);
  assert.match(panel, /追蹤股票、訊號勾選與提醒開關|所有裝置/);
  assert.match(route, /getChatGPTUser\(\)/);
  assert.match(route, /initializeIntradayTrackingState/);
  assert.match(route, /updateIntradayTrackingState/);
  assert.match(storage, /WHERE user_email = \? AND revision = \?/);
  assert.doesNotMatch(storage, /CREATE TABLE/);
  assert.match(migration, /CREATE TABLE `intraday_tracking_sync_state`/);
});



test("ranks groups with official preopen trial prices from 08:30 to 09:00", async () => {
  const sharedGroups = await readFile(new URL("../lib/live-group-quotes.ts", import.meta.url), "utf8");
  const trial = await readFile(new URL("../lib/preopen-trial.ts", import.meta.url), "utf8");
  const focus = await readFile(new URL("../app/api/focus-ranking/route.ts", import.meta.url), "utf8");
  const live = await readFile(new URL("../app/api/live-ranking/route.ts", import.meta.url), "utf8");
  const group = await readFile(new URL("../app/api/group/route.ts", import.meta.url), "utf8");
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(trial, /clock\.minutes >= 8 \* 60 \+ 30 && clock\.minutes < 9 \* 60/);
  assert.match(trial, /stock\/api\/getStockInfo\.jsp/);
  assert.match(trial, /numberValue\(row\.pz\)/);
  assert.match(trial, /Math\.ceil\(codes\.length \/ 100\)/);
  assert.match(trial, /Date\.now\(\) \+ 8_000/);
  assert.match(trial, /if \(pending\?\.key === cacheKey\) return pending\.promise/);
  assert.match(focus, /priceType: "盤前試撮"/);
  assert.match(focus, /direction === "both"/);
  assert.match(focus, /const strong = rankSide\("strong"\)/);
  assert.match(focus, /const weak = rankSide\("weak"\)/);
  assert.match(focus, /rankings: \{\s*strong,\s*weak,/);
  assert.match(sharedGroups, /configured-groups-incomplete/);
  assert.match(sharedGroups, /MINIMUM_CONFIGURED_GROUP_COVERAGE/);
  assert.match(focus, /signalGroupRows = sorted\.slice\(0, 10\)/);
  assert.match(live, /priceType: "盤前試撮"/);
  assert.match(group, /priceType: preopenQuotes\.size > 0 \? "盤前試撮"/);
  assert.match(page, /minutes < 8 \* 60 \+ 30/);
  assert.match(page, /focus-ranking\?direction=both/);
  assert.match(page, /payload\.rankings\?\.\[side\]/);
  assert.match(page, /ranked\.signalGroups\.length < 10/);
  assert.match(page, /isPreopenTrialClientWindow\(\) \? 5_000 : 30_000/);
  assert.match(page, /盤前四撮・依試撮價自動重排/);
  assert.match(page, /盤前四撮・每撮即時更新/);
  assert.match(page, /各族群前三\{isWeak \? "弱" : "強"\}個股/);
  assert.match(page, /第\{index \+ 1\}\{isWeak \? "弱" : "強"\}/);
  assert.match(page, /focus-stock-group-heading/);
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.focus-stock-groups \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  assert.match(css, /\.focus-stock-list \{[^}]*grid-template-columns: 1fr/);
  assert.match(css, /html\[data-hanstock-device="ipad"\] \.focus-stock-group-heading \{[^}]*background: #c92f3c[^}]*grid-template-columns: auto minmax\(0, 1fr\) auto/);
  assert.match(css, /html\[data-hanstock-device="ipad"\] \.focus-stock-group-heading > strong \{[^}]*color: #fff; font-size: 19px/);
  assert.match(css, /html\[data-hanstock-device="iphone"\] \.focus-stock-group-heading \{[^}]*background: #c92f3c[^}]*grid-template-columns: auto minmax\(0, 1fr\) auto/);
  assert.match(css, /html\[data-hanstock-device="iphone"\] \.focus-stock-group-heading > strong \{[^}]*color: #fff; font-size: 15px/);
  assert.match(css, /html\[data-hanstock-device="(?:ipad|iphone)"\] \.focus-stock-group-heading > span \{[^}]*color: #fff !important; background: transparent !important/);
  assert.match(css, /html\[data-hanstock-device="ipad"\] \.battle-shell\.is-weak \.focus-stock-group-heading,[\s\S]*html\[data-hanstock-device="iphone"\] \.battle-shell\.is-weak \.focus-stock-group-heading \{[^}]*background: #187c4a/);
});

test("opens group constituents immediately and refreshes their quotes without blanking the dialog", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const group = await readFile(new URL("../app/api/group/route.ts", import.meta.url), "utf8");
  const members = await readFile(new URL("../app/api/group-chip-members/route.ts", import.meta.url), "utf8");

  assert.match(source, /groupStockCacheRef/);
  assert.match(source, /configuredGroupStocks/);
  assert.doesNotMatch(source, /groupStocksLoading \? \[\] : groupStockData/);
  assert.doesNotMatch(source, /api\/group\?name=\$\{encodeURIComponent\(selectedGroup\)\}&refresh=/);
  assert.match(source, /即時行情更新中/);
  assert.match(members, /members: \[\.\.\.uniqueMembers\]/);
  assert.match(group, /GROUP_RESPONSE_TTL_MS = 10_000/);
  assert.match(group, /Math\.ceil\(tickers\.length \/ 50\)/);
  assert.match(group, /stale-while-revalidate=30/);
});

test("loads large-order funding ratios for every selected group member instead of only ranked day-trade stocks", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const route = await readFile(new URL("../app/api/group-member-flow/route.ts", import.meta.url), "utf8");

  assert.match(page, /\/api\/group-member-flow\?tickers=/);
  assert.match(page, /selectedGroupMemberTickerKey/);
  assert.match(page, /逐筆待回補/);
  assert.match(page, /成交額待回補/);
  assert.match(page, /completedCount/);
  assert.match(route, /\/api\/hub\/daytrade-flow-ranking/);
  assert.match(route, /include_all/);
  assert.match(route, /readAuthoritativeGroupNetAmount/);
  assert.match(route, /readAuthoritativeGroupTurnoverAmount/);
  assert.doesNotMatch(route, /tpex_mainboard_daily_close_quotes/);
  assert.match(route, /saveFullMarketLargeFlowBaselines/);
  assert.match(route, /private, no-store/);
});

test("keeps every signal category in today's list and the automatic popup", async () => {
  const source = readEarlySellSources();

  assert.match(source, /const popupSnapshotSignals = \[\.\.\.orderedSignals, \.\.\.fourGate\]/);
  assert.match(source, /\.\.\.strictFourGateSignals,[\s\S]*?\.\.\.extraLargeSellSignals,[\s\S]*?\.\.\.extraLargeBuySignals/);
  assert.match(source, /const popupSignalModes = new Set\(popupSignals\.map\(intradaySignalCenterModeFor\)\)/);
  assert.match(source, /popupSignalModes\.size === 1[\s\S]*?: "today"/);
  assert.match(source, /盤中全部即時訊號/);
});



test("removes retired market index and OTC technical features from the battle page", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.doesNotMatch(page, /GlobalMarketCards|globalMarkets|global-markets|marketOverview|OtcStrengthSignal|OtcFiveMinuteChart|櫃買指數完整 K 線|加權指數完整 K 線/);
  assert.doesNotMatch(css, /global-market-strip|global-kline-shell|otc-five-minute-chart|otc-signal|index-strip/);
});
