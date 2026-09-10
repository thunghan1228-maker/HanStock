import { NextRequest, NextResponse } from "next/server";
import { createForceChartScale } from "@/lib/kline-force-scale";
import { selectWatchlistCandleWindow } from "@/lib/watchlist-candle-window";
import bundledRuntimeSource from "../../../vendor/hanstock-kline-runtime.txt?raw";

const HANSTOCK_ORIGIN = "https://hanstock-battle-minimal.thunghan8.chatgpt.site";
const KLINE_RUNTIME_REVISION = "20260908-force-gap-stability-v40";
const runtimeSourceCache = new Map<string, Promise<string>>();

function loadRuntimeSource(asset: string) {
  if (asset === "/assets/index-CCs-RpRr.js") return Promise.resolve(bundledRuntimeSource);
  const cached = runtimeSourceCache.get(asset);
  if (cached) return cached;

  const pending = fetch(new URL(asset, HANSTOCK_ORIGIN), {
    headers: { Accept: "text/javascript", "User-Agent": "HanStock-Battle/1.0" },
    // Cloudflare Workers only accepts no-store/no-cache for subrequest cache mode.
    // The module-level promise cache below still avoids re-downloading this runtime.
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  }).then(async (response) => {
    if (!response.ok) throw new Error(`kline_runtime_${response.status}`);
    return response.text();
  }).catch((error) => {
    runtimeSourceCache.delete(asset);
    throw error;
  });

  if (runtimeSourceCache.size >= 8) {
    const oldest = runtimeSourceCache.keys().next().value;
    if (oldest) runtimeSourceCache.delete(oldest);
  }
  runtimeSourceCache.set(asset, pending);
  return pending;
}

export async function GET(request: NextRequest) {
  const asset = request.nextUrl.searchParams.get("asset") ?? "";
  if (!/^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(asset)) {
    return new NextResponse("Invalid asset", { status: 400 });
  }

  try {
    const source = await loadRuntimeSource(asset);
    let dimensionsPatched = false;
    let desktopSubchartLayoutPatched = false;
    let desktopSubchartColorsPatched = false;
    let flatDailyCandlePatched = false;
    let candleWidthPatched = false;
    let rightGapPatched = false;
    let intradaySignalHistoryPatched = false;
    let oneMinuteSignalMarkersPatched = false;
    let desktopOpeningDividerPatched = false;
    let currentOpeningDividerLabelPatched = false;
    let referenceSessionCutoverPatched = false;
    let stickyTouchCrosshairPatched = false;
    let signalGlyphSizePatched = false;
    let axisTextSizingPatched = false;
    let bottomTimeAxisPatched = false;
    let crosshairTimePatched = false;
    let coarsePointerLockPatched = false;
    let crosshairForceBridgePatched = false;
    let forceScalePatched = false;
    let autoLatestCrosshairPatched = false;
    let mobileDefaultMacdPatched = false;
    let fullHistoryPanPatched = false;
    let initialHistoryWindowPatched = false;
    let initialViewportBeforePaintPatched = false;
    let initialHistoryOneMinuteVar = "";
    let initialHistoryFiveMinuteVar = "";
    let fullHistoryCandlesVar = "";
    let embeddedStockSearchPatched = false;
    let embeddedSearchNamePatched = false;
    let embeddedFiveMinuteButtonPatched = false;
    let openingRangeLineWidthPatched = false;
    let ohlcChangeLabelsPatched = false;
    let vwapVisibleCandlesPatched = false;
    let vwapRuntimePatched = false;
    let maPeriodSettingsPatched = false;
    let compactRuntimeVar = "w";
    let maReaderPatched = false;
    let maStatePatched = false;
    let maBridgePatched = false;
    let maButtonPatched = false;
    let maMutationHelpersPatched = false;
    let maAddPatched = false;
    let maResetPatched = false;
    const withFastCandles = source
      .replace('fetch(`/api/live-bars/${encodeURIComponent(x)}`,{cache:"no-store",headers:{Accept:"application/json"}})', 'fetch(`/api/live-bars/${encodeURIComponent(x)}`,{cache:"no-store",signal:AbortSignal.timeout(10000),headers:{Accept:"application/json"}})')
      .replace('const k=j.data.candles.filter(R=>!R.date.startsWith(N)).map(({ma5:R,ma20:O,...L})=>L),D=wR([...k,...b]);', 'const k=new Map(j.data.candles.map(R=>[R.date,R]));for(const R of b){const O=k.get(R.date);if(!O||Number(R.volume)>=Number(O.volume))k.set(R.date,R)}const D=wR([...k.values()].sort((R,O)=>R.date.localeCompare(O.date)));')
      .replace('T3({url:"/api/trpc",transformer:$s', 'T3({url:"/api/trpc",maxItems:1,transformer:$s')
      .replace('he.stocks.candles.useQuery({ticker:t??"00",interval:b},{enabled:g,', 'he.stocks.candles.useQuery({ticker:t??"00",interval:b},{initialData:()=>window.__hanstockReadCandleSnapshot?.(t,b),initialDataUpdatedAt:()=>window.__hanstockCandleSnapshotTime?.(t,b)||0,enabled:g,')
      .replace('refetch:P}=H;v.useEffect', 'refetch:P}=H;v.useEffect(()=>{const onForce=()=>P();window.addEventListener("hanstock-force-ready",onForce);return()=>window.removeEventListener("hanstock-force-ready",onForce)},[P]);v.useEffect');
    const withPeriodMaReader = withFastCandles.replace(
      'function kD(){try{const t=localStorage.getItem("hanstock-ma-config-v1");if(!t)return Vi;const a=JSON.parse(t);if(!Array.isArray(a))return Vi;const l=a.filter(o=>o&&Number.isFinite(Number(o.period))&&Number(o.period)>=2&&typeof o.color=="string").map((o,c)=>({id:String(o.id||`ma-${c}-${o.period}`),period:Math.round(Number(o.period)),color:o.color,enabled:o.enabled!==!1,lineWidth:Number.isFinite(Number(o.lineWidth))?Math.min(6,Math.max(1,Number(o.lineWidth))):2,lineStyle:o.lineStyle==="dashed"||o.lineStyle==="dotted"?o.lineStyle:"solid"}));return l.length?l:Vi}catch{return Vi}}',
      () => {
        maReaderPatched = true;
        return 'function kD(t="1d"){const a=["1d","1m","5m"].includes(t)?t:"1d",n=o=>a!=="1d"?o:Vi.map(c=>({...c,...(o.find(d=>Number(d.period)===Number(c.period))||{}),enabled:!0}));try{const l=localStorage.getItem("hanstock-ma-config-v3:"+a)||localStorage.getItem("hanstock-ma-config-v2:"+a)||localStorage.getItem("hanstock-ma-config-v1");if(!l)return n(Vi);const d=JSON.parse(l);if(!Array.isArray(d))return n(Vi);const u=d.filter(o=>o&&Number.isFinite(Number(o.period))&&Number(o.period)>=2&&typeof o.color==="string").map((o,c)=>({id:String(o.id||`ma-${c}-${o.period}`),period:Math.round(Number(o.period)),color:o.color,enabled:a==="1d"?!0:o.enabled!==!1,lineWidth:Number.isFinite(Number(o.lineWidth))?Math.min(6,Math.max(1,Number(o.lineWidth))):2,lineStyle:o.lineStyle==="dashed"||o.lineStyle==="dotted"?o.lineStyle:"solid"}));return u.length?n(u):n(Vi)}catch{return n(Vi)}}';
      },
    );
    const withPeriodMaState = withPeriodMaReader.replace(
      '[j,k]=v.useState(()=>kD()),[D,R]=v.useState(!1)',
      () => {
        maStatePatched = true;
        return '[j,k]=v.useState(()=>kD(c)),[D,R]=v.useState(!1)';
      },
    );
    const withPeriodMaBridge = withPeriodMaState.replace(
      'const T=v.useMemo(()=>Ui(t),[E,t]),M=b==="1m"||b==="5m",I=ls(t);v.useEffect(()=>{t&&N(c)},[t,c]),v.useEffect(()=>{try{localStorage.setItem("hanstock-ma-config-v1",JSON.stringify(j))}catch{}},[j]);const H=',
      () => {
        maBridgePatched = true;
        return 'const hanstockMaIntervalRef=v.useRef(c),hanstockMaConfigRef=v.useRef(j);hanstockMaConfigRef.current=j;const hanstockSaveMa=(A,Q)=>{try{localStorage.setItem("hanstock-ma-config-v3:"+A,JSON.stringify(Q))}catch{}},hanstockCommitMa=A=>k(Q=>{const X=typeof A==="function"?A(Q):A;return hanstockMaConfigRef.current=X,hanstockSaveMa(hanstockMaIntervalRef.current,X),X}),hanstockSetInterval=A=>{const Q=["1d","1m","5m"].includes(A)?A:"1d",X=kD(Q);hanstockSaveMa(hanstockMaIntervalRef.current,hanstockMaConfigRef.current),hanstockMaIntervalRef.current=Q,hanstockMaConfigRef.current=X,N(Q),k(X)},T=v.useMemo(()=>Ui(t),[E,t]),M=b==="1m"||b==="5m",I=ls(t);v.useEffect(()=>{t&&hanstockSetInterval(c)},[t,c]);const H=';
      },
    );
    const withPeriodMaButton = withPeriodMaBridge.replace(
      'onClick:()=>N(A.key),className:oe("h-7 px-2.5 rounded-md text-[11px] border transition-colors active:scale-95"',
      () => {
        maButtonPatched = true;
        return 'onClick:()=>hanstockSetInterval(A.key),className:oe("h-7 px-2.5 rounded-md text-[11px] border transition-colors active:scale-95"';
      },
    );
    const withImmediateMaHelpers = withPeriodMaButton.replace(
      'Pa=(A,K)=>k(te=>te.map(ue=>ue.id===A?{...ue,...K}:ue)),za=A=>k(K=>K.filter(te=>te.id!==A)),Qo=()=>',
      () => {
        maMutationHelpersPatched = true;
        return 'Pa=(A,K)=>hanstockCommitMa(te=>te.map(ue=>ue.id===A?{...ue,...K}:ue)),za=A=>hanstockCommitMa(K=>K.filter(te=>te.id!==A)),Qo=()=>';
      },
    );
    const withImmediateMaAdd = withImmediateMaHelpers.replace(
      'k(ue=>[...ue,{id:`ma-${Date.now()}`,period:K,color:te[ue.length%te.length],enabled:!0,lineWidth:2,lineStyle:"solid"}]),R(!0)};return',
      () => {
        maAddPatched = true;
        return 'hanstockCommitMa(ue=>[...ue,{id:`ma-${Date.now()}`,period:K,color:te[ue.length%te.length],enabled:!0,lineWidth:2,lineStyle:"solid"}]),R(!0)};return';
      },
    );
    const withImmediateMaReset = withImmediateMaAdd.replace(
      'onClick:()=>k(Vi),className:"h-8 rounded border border-border bg-secondary/40 px-2 text-[10px] text-muted-foreground hover:text-foreground",children:"恢復預設"',
      () => {
        maResetPatched = true;
        return 'onClick:()=>hanstockCommitMa(Vi),className:"h-8 rounded border border-border bg-secondary/40 px-2 text-[10px] text-muted-foreground hover:text-foreground",children:"恢復預設"';
      },
    );
    maPeriodSettingsPatched = maReaderPatched && maStatePatched && maBridgePatched && maButtonPatched && maMutationHelpersPatched && maAddPatched && maResetPatched;
    const withEmbeddedStockSearch = withImmediateMaReset.replace(
      'function kt(t,a="5m",l){const o=t.trim().toUpperCase();if(!o)return null;',
      () => {
        embeddedStockSearchPatched = true;
        return 'function kt(t,a="5m",l){const o=t.trim().toUpperCase();if(!o)return null;if(window.parent!==window){const c=a==="1m"?"1m":a==="1d"?"1d":"5m";window.parent.postMessage({type:"hanstock-battle-open-kline",ticker:o,name:l?.trim()||"",interval:c},location.origin);return window}';
      },
    );
    const withEmbeddedSearchName = withEmbeddedStockSearch.replace(
      'const y=w=>{kt(w,t),o(""),d(!1)},g=w=>',
      () => {
        embeddedSearchNamePatched = true;
        return 'const y=w=>{kt(w,t,x.find(b=>b.ticker===w)?.name),o(""),d(!1)},g=w=>';
      },
    );
    const withEmbeddedFiveMinuteButton = withEmbeddedSearchName.replace(
      'className:"min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/60"}),s.jsx(Q6,',
      () => {
        embeddedFiveMinuteButtonPatched = true;
        return 'className:"min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground/60"}),s.jsx("button",{"data-loc":"client/src/components/KlineWindowStockSearch.tsx:battle-open-5m",type:"submit",className:"shrink-0 rounded-md border border-primary px-2 py-1 text-xs font-bold text-primary",children:"開啟5分K"}),s.jsx(Q6,';
      },
    );
    const withFullHistoryPan = withEmbeddedFiveMinuteButton.replace(
      /([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\.useMemo\(\(\)=>([A-Za-z_$][\w$]*)\?([A-Za-z_$][\w$]*)\.slice\(-300\):([A-Za-z_$][\w$]*)\?\4:\4\.slice\(-60\),\[\4,\3,\5\]\)/,
      (_match, visibleVar: string, reactVar: string, oneMinuteVar: string, allCandlesVar: string, fiveMinuteVar: string) => {
        fullHistoryPanPatched = true;
        initialHistoryOneMinuteVar = oneMinuteVar;
        initialHistoryFiveMinuteVar = fiveMinuteVar;
        fullHistoryCandlesVar = allCandlesVar;
        return `${visibleVar}=${reactVar}.useMemo(()=>(${selectWatchlistCandleWindow.toString()})(${allCandlesVar},new URLSearchParams(location.search).get("view"),${oneMinuteVar}||${fiveMinuteVar}),[${allCandlesVar},${oneMinuteVar},${fiveMinuteVar}])`;
      },
    );
    const withInitialHistoryWindow = withFullHistoryPan.replace(
      'v.useEffect(()=>{zn(null),tn.current=null,Vn(!0),nn.current=null,an.current=null,St.current=null},[t,b]);',
      (match) => {
        if (!initialHistoryOneMinuteVar || !initialHistoryFiveMinuteVar) return match;
        initialHistoryWindowPatched = true;
        initialViewportBeforePaintPatched = true;
        return `v.useLayoutEffect(()=>{zn(null),tn.current=null,Vn(!0),nn.current=null,an.current=null,St.current=null},[t,b]);v.useLayoutEffect(()=>{if(!ct.length)return;const A=${initialHistoryOneMinuteVar}||${initialHistoryFiveMinuteVar},$=ct.at(-1)?.date?.slice(0,5),ee=A?[...new Set(ct.map(Qe=>Qe.date?.slice(0,5)).filter(Boolean))]:[],ue=A&&$?ct.findIndex(Qe=>Qe.date?.startsWith($)):-1,se=${initialHistoryFiveMinuteVar}&&ee.length>1?ct.findIndex(Qe=>Qe.date?.startsWith(ee.at(-2))):-1,Re=A?(se>=0?se:ue>=0?ue:Math.max(0,ct.length-(${initialHistoryOneMinuteVar}?300:160))):Math.max(0,ct.length-60),Xe=Math.max(1,ct.length-Re);zn(Xe<ct.length?{start:Re,count:Xe}:null)},[t,b,ct.length]);`;
      },
    );
    const withMobileDefaultMacd = withInitialHistoryWindow.replace(
      '[O,L]=v.useState(()=>({macd:!0,kd:!1,boll:!1}))',
      () => {
        mobileDefaultMacdPatched = true;
        return '[O,L]=v.useState(()=>({macd:c==="5m"?!1:new URLSearchParams(location.search).get("view")!=="watchlist"&&!matchMedia("(pointer:coarse)").matches,kd:!1,boll:!1}))';
      },
    );
    const resized = withMobileDefaultMacd.replace(
      /const ([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\?560:840,([A-Za-z_$][\w$]*)=\2\?265:270,([A-Za-z_$][\w$]*)=\2\?105:100,([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\?\2\?108:98:0/,
      (_match, widthVar: string, compactVar: string, priceVar: string, volumeVar: string, forceVar: string, intradayVar: string) => {
        dimensionsPatched = true;
        compactRuntimeVar = compactVar;
        desktopSubchartLayoutPatched = true;
        return `const ${widthVar}=new URLSearchParams(location.search).get("view")==="watchlist"?Math.max(760,Math.round(window.innerWidth/Math.max(200,window.innerHeight-45)*569)):${compactVar}?560:2500,${priceVar}=new URLSearchParams(location.search).get("view")==="watchlist"?270:${compactVar}?330:500,${volumeVar}=new URLSearchParams(location.search).get("view")==="watchlist"?65:${compactVar}?105:110,${forceVar}=${intradayVar}?new URLSearchParams(location.search).get("view")==="watchlist"?110:${compactVar}?210:230:0`;
      },
    );
    const withDesktopMacdHeight = resized.replace(
      `${compactRuntimeVar}?102:94:0`,
      `new URLSearchParams(location.search).get("view")==="watchlist"?90:${compactRuntimeVar}?102:140:0`,
    );
    const withBottomAxisRoom = withDesktopMacdHeight.replace(
      `${compactRuntimeVar}?34:32`,
      () => `${compactRuntimeVar}?42:50`,
    );
    const axisSizingPattern = new RegExp(`([A-Za-z_$][\\w$]*)=${compactRuntimeVar}\\?76:70,([A-Za-z_$][\\w$]*)=12,([A-Za-z_$][\\w$]*)=28,([A-Za-z_$][\\w$]*)=${compactRuntimeVar}\\?18:15,([A-Za-z_$][\\w$]*)=${compactRuntimeVar}\\?18:15`);
    const withAxisSizing = withBottomAxisRoom.replace(
      axisSizingPattern,
      (_match, rightAxisVar: string, plotTopVar: string, dividerVar: string, yFontVar: string, xFontVar: string) => {
        axisTextSizingPatched = withBottomAxisRoom !== resized;
        return `${rightAxisVar}=${compactRuntimeVar}?76:96,${plotTopVar}=12,${dividerVar}=28,${yFontVar}=${compactRuntimeVar}?18:24,${xFontVar}=${compactRuntimeVar}?18:30`;
      },
    );
    const withVisibleBottomTimeAxis = withAxisSizing.replace(
      'x:A.xPos,y:Ma-6,textAnchor:A.anchor,fontSize:En,fontWeight:"600",fill:"#f8fafc",className:"font-mono-num",children:A.label',
      () => {
        bottomTimeAxisPatched = true;
        return 'x:A.xPos,y:Ma-10,textAnchor:A.anchor,fontSize:En,fontWeight:"900",fill:"#ffffff",stroke:"#07090b",strokeWidth:"3",paintOrder:"stroke",className:"font-mono-num",children:A.label';
      },
    );
    const withRightGap = withVisibleBottomTimeAxis.replace(
      /function ([A-Za-z_$][\w$]*)\(t,a,l=10,o=!1\)\{const c=Math\.max\(0,Math\.floor\(t\)\),d=Math\.max\(1,Math\.floor\(a\)\),u=Math\.max\(0,Math\.floor\(l\)\),f=Math\.max\(o\?1:d,c\+u\);return\{slotCount:f,leadingSlots:Math\.max\(0,f-c-u\),trailingSlots:u\}\}/,
      (_match, layoutFunction: string) => {
        rightGapPatched = true;
        return `function ${layoutFunction}(t,a,l=5,o=!1){const c=Math.max(0,Math.floor(t)),d=Math.max(1,Math.floor(a)),u=5,f=Math.max(o?1:d,c+u);return{slotCount:f,leadingSlots:Math.max(0,f-c-u),trailingSlots:u}}`;
      },
    );
    const withCandleWidth = withRightGap.replace(
      /([A-Za-z_$][\w$]*)=Math\.max\(1\.2,Math\.min\(7,([A-Za-z_$][\w$]*)\*\.55\)\)/,
      (_match, candleWidthVar: string, candleStepVar: string) => {
        candleWidthPatched = true;
        const fiveMinuteVar = initialHistoryFiveMinuteVar || "false";
        // 只缩紧电脑五分 K；一分钟、日 K 与触控装置维持原来的 82% 棒宽。
        return `${candleWidthVar}=!${compactRuntimeVar}&&${fiveMinuteVar}?Math.max(2.4,Math.min(18,${candleStepVar}*.98)):Math.max(2.4,Math.min(12,${candleStepVar}*.82))`;
      },
    );
    const withOpeningRangeLineWidth = withCandleWidth.replace(
      'stroke:A.color,strokeWidth:"2.2",strokeDasharray:A.label==="昨高"||A.label==="昨低"?"7 5":void 0,opacity:"0.95"',
      () => {
        openingRangeLineWidthPatched = true;
        return 'stroke:A.color,strokeWidth:A.label==="首5高"||A.label==="首5低"?"5.5":"2.2",strokeDasharray:A.label==="昨高"||A.label==="昨低"?"7 5":void 0,opacity:"0.95"';
      },
    );
    const withSignalGlyphSize = withOpeningRangeLineWidth.replace(
      /const ([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\.length,([A-Za-z_$][\w$]*)=Math\.round\(Math\.max\(14,Math\.min\(20,14\+\(160-\1\)\/18\)\)\)/,
      (_match, countVar: string, barsVar: string, glyphVar: string) => {
        signalGlyphSizePatched = true;
        return `const ${countVar}=${barsVar}.length,${glyphVar}=${compactRuntimeVar}?Math.round(Math.max(14,Math.min(20,14+(160-${countVar})/18))):Math.round(Math.max(34,Math.min(52,36+(160-${countVar})/6)))`;
      },
    );
    const withIntradaySignalHistory = withSignalGlyphSize.replace(
      /const\[([A-Za-z_$][\w$]*)\]=([A-Za-z_$][\w$]*)\.useState\(\(\)=>Date\.now\(\)-4\*864e5\),/,
      (_match, cutoffVar: string) => {
        intradaySignalHistoryPatched = true;
        // Fetch only the retained trading session; visible candles keep their full history.
        return `const ${cutoffVar}=window.__hanstockSignalSinceTs?.()??Date.now()-20*864e5,`;
      },
    );
    const withFiveMinuteOnlySignalMarks = withIntradaySignalHistory.replace(
      'xa.marks.map((A,K)=>{',
      () => {
        oneMinuteSignalMarkersPatched = true;
        return '(b==="5m"?xa.marks.filter(A=>window.__hanstockIsSignalCandleVisible?.(ke[A.i])===true):[]).map((A,K)=>{';
      },
    );
    const withSignalSessionAttributes = withFiveMinuteOnlySignalMarks.replace(
      '"data-loc":"client/src/components/CandleChartDialog.tsx:2288",children:',
      '"data-loc":"client/src/components/CandleChartDialog.tsx:2288","data-hanstock-native-signal-date":ke[A.i]?.date,"data-hanstock-native-signal-side":A.side,children:',
    );
    const withSignalIdentity = withSignalSessionAttributes
      .replace('zt.glyphs.push(Rt)', 'zt.glyphs.push({...Rt,kind:Ye.kind,note:Ye.note,label:Ye.label})')
      .replace('"data-loc":"client/src/components/CandleChartDialog.tsx:2305",children:',
        '"data-loc":"client/src/components/CandleChartDialog.tsx:2305","data-hanstock-signal-kind":Me.kind,"data-hanstock-signal-note":Me.note,"data-hanstock-signal-label":Me.label,children:');
    const withCurrentOpeningDividerLabel = withSignalIdentity.replace(
      'ae.openingDividerPreview&&s.jsx("text",{"data-loc":"client/src/components/CandleChartDialog.tsx:2102"',
      () => {
        currentOpeningDividerLabelPatched = true;
        return 's.jsx("text",{"data-loc":"client/src/components/CandleChartDialog.tsx:2102"';
      },
    );
    const withReferenceSessionCutover = withCurrentOpeningDividerLabel.replace(
      "Ja=hf&&!Mn&&ar<545,ba=Mn?va:Ja?null:ge.at(-1)??null",
      () => {
        referenceSessionCutoverPatched = true;
        // Keep the most recently completed trading session active after midnight.
        // At 08:45 Taipei time it becomes the previous session, while the new
        // session waits for its first five-minute candle. Weekends keep the last
        // trading session active until the next weekday preparation window.
        return "Ja=hf&&!Mn&&ar>=525&&ar<545,ba=Mn?va:Ja?null:ge.at(-1)??null";
      },
    );
    const withDesktopOpeningDivider = withReferenceSessionCutover.replace(
      "}const ei=Tt+Bt+Yn,Nc=ei+8",
      () => {
        desktopOpeningDividerPatched = true;
        return '}if(ze&&!w){const yn=Be.at(-1)?.date.slice(0,5),en=yn?Be.findIndex(Ht=>Ht.date.startsWith(yn)):-1,lt=en-Un;if(lt>=0&&lt<Ee.length){Jo=Math.max(Le+1,Math.min(Ne-Ie-1,Dn(lt))),Pl=!1}}const ei=Tt+Bt+Yn,Nc=ei+8';
      },
    );
    const withDesktopVolumeColors = withDesktopOpeningDivider.replace(
      'Ee.map((A,$)=>{const ee=A.close>A.open,we=A.close===A.open?"#9ca3af":ee?"#ef4444":"#22c55e",Fe=se.x($);return s.jsx("rect",{"data-loc":"client/src/components/CandleChartDialog.tsx:2436",x:Fe-se.bodyW/2,y:se.volY(A.volume),width:se.bodyW,height:Math.max(.5,se.volH(A.volume)),fill:we,opacity:"0.65"},`v${$}`)})',
      () => {
        desktopSubchartColorsPatched = true;
        return 'Ee.map((A,$)=>{const ee=A.close>A.open,we=A.close===A.open?(w?"#9ca3af":"#6b7280"):ee?(w?"#ef4444":"#991b1b"):(w?"#22c55e":"#166534"),Fe=se.x($);return s.jsx("rect",{"data-loc":"client/src/components/CandleChartDialog.tsx:2436",x:Fe-se.bodyW/2,y:se.volY(A.volume),width:se.bodyW,height:Math.max(.5,se.volH(A.volume)),fill:we,opacity:w?"0.65":"0.9"},`v${$}`)})';
      },
    );
    const withFlatDailyCandle = withDesktopVolumeColors.replace(
      'ke.map((A,K)=>{const te=A.close>A.open,we=A.close===A.open?"#9ca3af":te?"#ef4444":"#22c55e",qe=ae.x(K),Ge=ae.y(Math.max(A.open,A.close)),We=ae.y(Math.min(A.open,A.close)),_e=Math.max(1,We-Ge);return s.jsxs("g",{"data-loc":"client/src/components/CandleChartDialog.tsx:2158",children:[s.jsx("line",{"data-loc":"client/src/components/CandleChartDialog.tsx:2159",x1:qe,x2:qe,y1:ae.y(A.high),y2:ae.y(A.low),stroke:we,strokeWidth:"1"}),s.jsx("rect",{"data-loc":"client/src/components/CandleChartDialog.tsx:2167",x:qe-ae.bodyW/2,y:Ge,width:ae.bodyW,height:_e,fill:we,stroke:we,strokeWidth:"1",rx:"0.5"})]},K)})',
      () => {
        flatDailyCandlePatched = true;
        return 'ke.map((A,K)=>{const te=A.close>A.open,hanstockFlat=!M&&A.close===A.open,hanstockPrev=K>0?Number(ke[K-1]?.close):NaN,we=A.close===A.open?hanstockFlat&&Number.isFinite(hanstockPrev)?A.close>hanstockPrev?"#ef4444":A.close<hanstockPrev?"#22c55e":"#9ca3af":"#9ca3af":te?"#ef4444":"#22c55e",qe=ae.x(K),Ge=ae.y(Math.max(A.open,A.close)),We=ae.y(Math.min(A.open,A.close)),_e=Math.max(hanstockFlat?6:1,We-Ge),hanstockBodyY=hanstockFlat?Ge-_e/2:Ge;return s.jsxs("g",{"data-loc":"client/src/components/CandleChartDialog.tsx:2158","data-hanstock-bar-date":String(A?.date??""),children:[s.jsx("line",{"data-loc":"client/src/components/CandleChartDialog.tsx:2159",x1:qe,x2:qe,y1:ae.y(A.high),y2:ae.y(A.low),stroke:we,strokeWidth:hanstockFlat?2:"1"}),s.jsx("rect",{"data-loc":"client/src/components/CandleChartDialog.tsx:2167",x:qe-ae.bodyW/2,y:hanstockBodyY,width:ae.bodyW,height:_e,fill:we,stroke:we,strokeWidth:"1",rx:"0.5"})]},K)})';
      },
    );
    const withRuntimeVwap = withFlatDailyCandle.replace(
      'rx:"0.5"})]},K)}),ae.bollPaths&&',
      () => {
        vwapRuntimePatched = true;
        const fullSeries = fullHistoryCandlesVar || "ke";
        return `rx:"0.5"})]},K)}),(()=>{const xe=Array.isArray(${fullSeries})&&${fullSeries}.length?${fullSeries}:ke,De=[...new Map(xe.filter(Ge=>Ge&&String(Ge.date??"")).map(Ge=>[String(Ge.date),Ge])).values()],Ve=new Map;let A="",K=0,te=0,qe=null;for(const Ge of De){const We=String(Ge?.date??""),_e=We.split(/[ T]/)[0];if(_e!==A){A=_e,K=0,te=0,qe=null}const Ze=Number(Ge?.high),Je=Number(Ge?.low),$e=Number(Ge?.close),ta=Number(Ge?.volume);if(Number.isFinite(Ze)&&Number.isFinite(Je)&&Number.isFinite($e)&&Number.isFinite(ta)&&ta>0){K+=(Ze+Je+$e)/3*ta,te+=ta,qe=K/te}Number.isFinite(qe)&&Ve.set(We,qe)}let ue=[],we=[],Be="";for(let Ge=0;Ge<ke.length;Ge++){const We=ke[Ge],_e=String(We?.date??""),Ze=_e.split(/[ T]/)[0];if(Ze!==Be){we.length>1&&ue.push(we),we=[],Be=Ze}const Je=Ve.get(_e);Number.isFinite(Je)&&(qe=Je,we.push(ae.x(Ge).toFixed(1)+","+ae.y(Je).toFixed(1)))}we.length>1&&ue.push(we);return!ue.length||!Number.isFinite(qe)?null:s.jsxs("g",{"data-hanstock-vwap":"1",pointerEvents:"none",style:{display:window.__hanstockVwapVisible===!1?"none":void 0},children:[ue.map((Ge,We)=>s.jsx("polyline",{"data-loc":"hanstock-vwap-line",points:Ge.join(" "),fill:"none",stroke:"#ff5fd2",strokeWidth:w?3.2:5,strokeLinejoin:"round",strokeLinecap:"round",filter:"drop-shadow(0 0 4px rgba(255,95,210,.82))"},\`hanstock-vwap-\${We}\`)),s.jsx("text",{"data-loc":"hanstock-vwap-label",x:Ie+12,y:Ya+24,fontSize:w?16:22,fontWeight:"900",fill:"#ff83dc",stroke:"#10151b",strokeWidth:w?3:5,paintOrder:"stroke",strokeLinejoin:"round",children:"VWAP "+qe.toLocaleString("zh-TW",{maximumFractionDigits:2})})]})})(),ae.bollPaths&&`;
      },
    );
    const withBrightRuntimeVwap = withRuntimeVwap
      .replace('stroke:"#ff5fd2",strokeWidth:w?3.2:5', 'stroke:"#fff200",strokeWidth:w?6.4:10')
      .replace('drop-shadow(0 0 4px rgba(255,95,210,.82))', 'drop-shadow(0 0 6px rgba(255,242,0,.95))')
      .replace('fill:"#ff83dc",stroke:"#10151b"', 'fill:"#fff200",stroke:"#10151b"');
    const withDesktopForceColors = withBrightRuntimeVwap.replace(
      'fill:A>=0?"#ef4444":"#22c55e",opacity:"0.84"},`main-force-${K}`)',
      'fill:A>=0?"#fb7185":"#4ade80",opacity:"1"},`main-force-${K}`)',
    );
    const withDesktopForceLine = withDesktopForceColors.replace(
      'stroke:"#facc15",strokeWidth:w?1.8:1.45,strokeLinejoin:"round",strokeLinecap:"round"',
      'stroke:w?"#facc15":"#ffe04f",strokeWidth:w?1.8:4,strokeLinejoin:"round",strokeLinecap:"round",filter:w?void 0:"drop-shadow(0 0 3px rgba(255,224,79,.75))"',
    );
    const withDesktopMacdColors = withDesktopForceLine.replace(
      'fill:A>=0?"#ef4444":"#22c55e",opacity:"0.72"},`macd-h-${$}`)',
      'fill:w?A>=0?"#ef4444":"#22c55e":A>=0?"#991b1b":"#166534",opacity:w?"0.72":"0.88"},`macd-h-${$}`)',
    );
    const withForceScale = withDesktopMacdColors.replace(
      'sf=[...Ko,...Cc].filter(ge=>typeof ge=="number"&&Number.isFinite(ge)),$o=Math.max(1,...sf.map(Math.abs)),Ac=af+Pl/2,kc=ge=>Ac-ge/$o*Math.max(1,Pl/2-3),rf=Cc.map((ge,ot)=>ge!=null?`${Dn(ot).toFixed(1)},${kc(ge).toFixed(1)}`:null).filter(Boolean)',
      () => {
        forceScalePatched = true;
        return 'hanstockBarScale=hanstockCreateForceScale(Ko,af,Pl),$o=Math.max(1,...Ko.filter(ge=>typeof ge=="number"&&Number.isFinite(ge)).map(Math.abs)),Ac=hanstockBarScale.zeroY,kc=hanstockBarScale.y,hanstockCumAbs=Math.max(1,...Cc.filter(ge=>typeof ge=="number"&&Number.isFinite(ge)).map(Math.abs)),hanstockCumY=ge=>Ac-ge/hanstockCumAbs*Math.max(1,Pl/2-3),rf=Cc.map((ge,ot)=>ge!=null?`${Dn(ot).toFixed(1)},${hanstockCumY(ge).toFixed(1)}`:null).filter(Boolean)';
      },
    );
    const withReadableForceBars = withForceScale
      .replace('if(A===null)return null;const te=ae.mainForceY(A)', 'if(A===null||A===0)return null;const te=ae.mainForceY(A)')
      .replace('children:"黃線累計"', 'children:"柱：對數／線：另尺"')
      .replace('ae.mainForceNet.map((A,K)=>{', 'ae.mainForceNet.map((A,K)=>{if(A!==null||K>0&&ae.mainForceNet[K-1]===null)return null;let end=K;while(end+1<ae.mainForceNet.length&&ae.mainForceNet[end+1]===null)end++;if(end-K<(end===ae.mainForceNet.length-1&&ke.at(-1)?.date===ct.at(-1)?.date?5:2))return null;return s.jsx("text",{"data-hanstock-force-missing":true,x:(ae.x(K)+ae.x(end))/2,y:ae.mainForceZeroY-12,textAnchor:"middle",fontSize:w?14:18,fill:"#cbd5e1",children:"主力資料待補"},"force-missing-"+K)}),ae.mainForceNet.map((A,K)=>{');
    const withCrosshairWidth = withReadableForceBars.replace(
      `const A=se.x(jn),$=${compactRuntimeVar}?92:78,`,
      `const A=se.x(jn),$=${compactRuntimeVar}?92:132,`,
    );
    const withCrosshairBox = withCrosshairWidth.replace(
      `width:$,height:24,rx:3,fill:"#0b2a5b",stroke:"#3b82f6",strokeWidth:"0.8"`,
      `width:$,height:${compactRuntimeVar}?24:36,rx:4,fill:"#123b7a",stroke:"#7dd3fc",strokeWidth:${compactRuntimeVar}?0.8:1.5`,
    );
    const withCrosshairTime = withCrosshairBox.replace(
      `x:ee+$/2,y:ue+17,textAnchor:"middle",fontSize:${compactRuntimeVar}?14:12,fontWeight:"700",fill:"#60a5fa"`,
      () => {
        crosshairTimePatched = withCrosshairWidth !== withIntradaySignalHistory && withCrosshairBox !== withCrosshairWidth;
        return `x:ee+$/2,y:ue+(${compactRuntimeVar}?17:26),textAnchor:"middle",fontSize:${compactRuntimeVar}?14:22,fontWeight:"900",fill:"#ffffff"`;
      },
    );
    const withTouchStartCrosshair = withCrosshairTime.replace(
      "if(!$t.current){$t.current={x:ee.clientX,y:ee.clientY,range:Rt,moved:!1};return}",
      "if(!$t.current){$t.current={x:ee.clientX,y:ee.clientY,range:Rt,moved:!1};on(Math.max(0,Math.min(Ee.length-1,Math.floor((ue-Le)/se.step-se.leadingSlots))));return}",
    );
    const crosshairIndexCall = "on(Math.max(0,Math.min(Ee.length-1,Math.floor((ue-Le)/se.step-se.leadingSlots))))";
    const withLegacyCrosshairForceBridge = withTouchStartCrosshair.replaceAll(
      crosshairIndexCall,
      '(()=>{const Qe=Math.max(0,Math.min(Ee.length-1,Math.floor((ue-Le)/se.step-se.leadingSlots)));on(Qe);window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:Ee[Qe]?.date??null},location.origin)})()',
    );
    const currentCrosshairIndexCall = "Yt(Math.max(0,Math.min(ke.length-1,Math.floor((ue-Ie)/ae.step-ae.leadingSlots))))";
    const withTouchCrosshairForceBridge = withLegacyCrosshairForceBridge.replaceAll(
      currentCrosshairIndexCall,
      '(()=>{const Qe=Math.max(0,Math.min(ke.length-1,Math.floor((ue-Ie)/ae.step-ae.leadingSlots)));Yt(Qe);window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[Qe]?.date??null,index:Qe},location.origin);clearTimeout(window.__hanstockLatestCrosshairTimer);window.__hanstockLatestCrosshairTimer=setTimeout(()=>{const Xe=Math.max(0,ke.length-1);vn.current=Xe;Yt(Xe);Vn(!0);window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[Xe]?.date??null,index:Xe,autoLatest:!0},location.origin)},1e4)})()',
    );
    const withCrosshairForceBridge = withTouchCrosshairForceBridge.replace(
      "vn.current=te,Yt(te)",
      () => {
        autoLatestCrosshairPatched = true;
        return 'vn.current=te,Yt(te),window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[te]?.date??null,index:te},location.origin),clearTimeout(window.__hanstockLatestCrosshairTimer),window.__hanstockLatestCrosshairTimer=setTimeout(()=>{const Qe=Math.max(0,ke.length-1);vn.current=Qe,Yt(Qe),Vn(!0),window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[Qe]?.date??null,index:Qe,autoLatest:!0},location.origin)},1e4)';
      },
    );
    const withCurrentTouchStartBridge = withCrosshairForceBridge.replace(
      "if(!St.current){St.current={x:te.clientX,y:te.clientY,range:Ct,moved:!1};return}",
      'if(!St.current){St.current={x:te.clientX,y:te.clientY,range:Ct,moved:!1};const Qe=Math.max(0,Math.min(ke.length-1,Math.floor((ue-Ie)/ae.step-ae.leadingSlots)));Yt(Qe);window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[Qe]?.date??null,index:Qe},location.origin);clearTimeout(window.__hanstockLatestCrosshairTimer);window.__hanstockLatestCrosshairTimer=setTimeout(()=>{const Xe=Math.max(0,ke.length-1);vn.current=Xe;Yt(Xe);Vn(!0);window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:ke[Xe]?.date??null,index:Xe,autoLatest:!0},location.origin)},1e4);return}',
    );
    crosshairForceBridgePatched = withCurrentTouchStartBridge !== withTouchStartCrosshair;
    const withLegacyLockedCoarsePointer = withCurrentTouchStartBridge.replace(
      "onMouseMove:Wu,onMouseLeave:()=>on(null),onTouchStart:Tl,onTouchMove:Tl,onTouchEnd:Vo,onTouchCancel:Vo",
      () => {
        coarsePointerLockPatched = true;
        return 'onMouseMove:A=>{!matchMedia("(pointer:coarse)").matches&&Date.now()-(window.__hanstockLastTouch||0)>1200&&Wu(A)},onMouseLeave:()=>{!matchMedia("(pointer:coarse)").matches&&Date.now()-(window.__hanstockLastTouch||0)>1200&&(on(null),window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:null},location.origin))},onTouchStart:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),Tl(A)},onTouchMove:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),Tl(A)},onTouchEnd:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),Vo(A)},onTouchCancel:A=>{window.__hanstockLastTouch=Date.now(),Vo(A)}';
      },
    );
    const withLockedCoarsePointer = withLegacyLockedCoarsePointer.replace(
      "onMouseMove:hc,onMouseLeave:()=>{vn.current=null,Yt(null)},onTouchStart:Kr,onTouchMove:Kr,onTouchEnd:xc,onTouchCancel:xc",
      () => {
        coarsePointerLockPatched = true;
        return 'onMouseMove:A=>{!matchMedia("(pointer:coarse)").matches&&hc(A)},onMouseLeave:()=>{if(!matchMedia("(pointer:coarse)").matches){vn.current=null,Yt(null),window.parent!==window&&window.parent.postMessage({type:"hanstock-kline-crosshair",date:null,index:null},location.origin)}},onTouchStart:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),Kr(A)},onTouchMove:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),Kr(A)},onTouchEnd:A=>{window.__hanstockLastTouch=Date.now(),A.preventDefault(),xc(A)},onTouchCancel:A=>{window.__hanstockLastTouch=Date.now(),xc(A)}';
      },
    );
    const withLegacyStickyTouch = withLockedCoarsePointer.replace(
      "Vo=A=>{A.touches.length<2&&(Et.current=null),A.touches.length===0&&($t.current=null),A.touches.length===0&&on(null)}",
      () => {
        stickyTouchCrosshairPatched = withTouchStartCrosshair !== withCrosshairTime && coarsePointerLockPatched;
        return "Vo=A=>{A.touches.length<2&&(Et.current=null),A.touches.length===0&&($t.current=null),window.__hanstockCrosshairLocked=!0}";
      },
    );
    const withStickyTouch = withLegacyStickyTouch.replace(
      "xc=A=>{A.touches.length<2&&(an.current=null),A.touches.length===0&&(St.current=null),A.touches.length===0&&Yt(null)}",
      () => {
        stickyTouchCrosshairPatched = coarsePointerLockPatched;
        return "xc=A=>{A.touches.length<2&&(an.current=null),A.touches.length===0&&(St.current=null),window.__hanstockCrosshairLocked=!0}";
      },
    );
    const patchedRuntime = withStickyTouch.replaceAll("M?ju(mt.date,it?1:5):mt.date", "M?ou(mt.date,it?1:5):mt.date").replaceAll("M?ju(Za.date,it?1:5):Za.date", "M?ou(Za.date,it?1:5):Za.date").replace(
      'Tn!==null&&s.jsxs("span",{"data-loc":"client/src/components/CandleChartDialog.tsx:2000",className:"text-slate-200",children:["漲跌 ",s.jsxs("span",{"data-loc":"client/src/components/CandleChartDialog.tsx:2000",className:oe("font-bold",Tn>0?"text-loss":Tn<0?"text-gain":"text-white"),children:[Tn>0?"+":"",Tn.toFixed(2),"%"]})]})',
      () => {
        ohlcChangeLabelsPatched = true;
        vwapVisibleCandlesPatched = true;
        return '(window.__hanstockVisibleCandles=ke,window.__hanstockPeriodRendered?.(ke),mt&&(()=>{const Qe=String(mt.date??"").split(/[ T]/)[0],Xe=ke.findIndex(A=>A===mt||A?.date===mt.date);let Je=null;for(let A=(Xe>=0?Xe:ke.length)-1;A>=0;A--){if(String(ke[A]?.date??"").split(/[ T]/)[0]!==Qe&&Number.isFinite(Number(ke[A]?.close))){Je=Number(ke[A].close);break}}if(!Number.isFinite(Je)||Je===0)return null;const $e=Number(mt.close)-Je,ta=$e/Je*100;return s.jsxs("span",{"data-loc":"client/src/components/CandleChartDialog.tsx:2000",className:"text-slate-200",children:["漲跌幅 ",s.jsxs("span",{"data-loc":"client/src/components/CandleChartDialog.tsx:2000",className:oe("font-bold",ta>0?"text-loss":ta<0?"text-gain":"text-white"),children:[ta>0?"+":"",ta.toFixed(2),"%"]}),"　漲跌 ",s.jsxs("span",{className:oe("font-bold",$e>0?"text-loss":$e<0?"text-gain":"text-white"),children:[$e>0?"+":"",$e.toFixed(2)," 元"]})]})})())';
      },
    );

    return new NextResponse("const hanstockCreateForceScale=" + createForceChartScale.toString() + ";\n" + patchedRuntime, {
      headers: {
        "Content-Type": "text/javascript; charset=utf-8",
        "Cache-Control": `public, max-age=31536000, immutable`,
        "X-HanStock-Runtime-Revision": KLINE_RUNTIME_REVISION,
        "X-HanStock-Wide-Chart": dimensionsPatched ? "1" : "0",
        "X-HanStock-Desktop-Subcharts": desktopSubchartLayoutPatched ? "price-500-volume-110-force-230-macd-140" : "0",
        "X-HanStock-Desktop-Subchart-Colors": desktopSubchartColorsPatched ? "dark-red-green" : "0",
        "X-HanStock-Flat-Daily-Candle": flatDailyCandlePatched ? "previous-close-color_min-6px" : "0",
        "X-HanStock-Candle-Width": candleWidthPatched ? "desktop-5m-98pct_other-82pct" : "0",
        "X-HanStock-Right-Gap": rightGapPatched ? "5" : "0",
        "X-HanStock-Opening-Range-Line-Width": openingRangeLineWidthPatched ? "5.5" : "0",
        "X-HanStock-Intraday-Signal-History": intradaySignalHistoryPatched ? "latest-session-until-0845" : "0",
        "X-HanStock-One-Minute-Signals": oneMinuteSignalMarkersPatched ? "hidden" : "unchanged",
        "X-HanStock-Desktop-Opening-Divider": desktopOpeningDividerPatched ? "latest-session-first-5m" : "native-position",
        "X-HanStock-Current-Opening-Label": currentOpeningDividerLabelPatched ? "always" : "0",
        "X-HanStock-Reference-Cutover": referenceSessionCutoverPatched ? "08:45-Asia-Taipei" : "0",
        "X-HanStock-Sticky-Touch-Crosshair": stickyTouchCrosshairPatched ? "1" : "0",
        "X-HanStock-Desktop-Signal-Glyph": signalGlyphSizePatched ? "34-56" : "0",
        "X-HanStock-Desktop-Axis-Text": axisTextSizingPatched ? "22" : "0",
        "X-HanStock-Bottom-Time-Axis": bottomTimeAxisPatched ? "below-force-chart" : "0",
        "X-HanStock-Desktop-Crosshair-Time": crosshairTimePatched ? "22" : "0",
        "X-HanStock-Mobile-Crosshair-Lock": coarsePointerLockPatched ? "tap-and-drag" : "0",
        "X-HanStock-Crosshair-Force-Bridge": crosshairForceBridgePatched ? "1" : "0",
        "X-HanStock-Force-Scale": forceScalePatched ? "symlog-bars-linear-cumulative" : "0",
        "X-HanStock-Auto-Latest-Crosshair": autoLatestCrosshairPatched ? "10s" : "0",
        "X-HanStock-Mobile-Default-Macd": mobileDefaultMacdPatched ? "off" : "unchanged",
        "X-HanStock-Full-History-Pan": fullHistoryPanPatched && initialHistoryWindowPatched ? "1m-801_5m-160_day-486" : "0",
        "X-HanStock-Initial-Viewport": initialHistoryWindowPatched && initialViewportBeforePaintPatched ? "latest-before-first-paint" : "0",
        "X-HanStock-Embedded-Stock-Search": embeddedStockSearchPatched && embeddedSearchNamePatched ? "parent-switch" : "0",
        "X-HanStock-Embedded-Open-5m": embeddedFiveMinuteButtonPatched ? "button" : "0",
        "X-HanStock-OHLC-Change-Labels": ohlcChangeLabelsPatched ? "pct-and-amount" : "0",
        "X-HanStock-VWAP-Visible-Candles": vwapVisibleCandlesPatched ? "1" : "0",
        "X-HanStock-VWAP-Runtime": vwapRuntimePatched ? "react-svg" : "0",
        "X-HanStock-MA-Config": maPeriodSettingsPatched ? "1d-1m-5m-independent" : "0",
      },
    });
  } catch (error) {
    console.error("[kline-runtime] upstream runtime unavailable", error instanceof Error ? error.message : "unknown-error");
    return new NextResponse("K-line runtime unavailable", {
      status: 502,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
}
