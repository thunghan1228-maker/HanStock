import { NextRequest, NextResponse } from 'next/server';
import { fetchCachedMarket } from '../../../../lib/market-fetch-cache';
import { reportMarketSourceFailure } from '../../../../lib/market-source-diagnostics';

// The embedded chart calls this path every few seconds. It used to return 404
// on this site, so its live query-cache update never ran.
export async function GET(_request: NextRequest, context: { params: Promise<{ticker: string}> }) {
  const { ticker } = await context.params;
  if (!/^[0-9A-Z]{4,12}$/.test(ticker)) return NextResponse.json({status:'error',error:'invalid-ticker'}, {status:400});
  const started = Date.now();
  let httpStatus: number | undefined;
  let stage = 'fetch';
  try {
    const response = await fetchCachedMarket(`https://hanstock-battle-minimal.thunghan8.chatgpt.site/api/live-bars/${encodeURIComponent(ticker)}`, {
      cache:'no-store', signal:AbortSignal.timeout(8_000), headers:{Accept:'application/json'},
    }, {ticker, activeMs:3_000});
    httpStatus = response.status;
    stage = 'json';
    const data = await response.json();
    stage = 'validate';
    if (!response.ok || data.status !== 'ok' || !Array.isArray(data.candles) || !data.candles.length) throw Error('live-bars-source-unavailable');
    return NextResponse.json(data, {headers:{'Cache-Control':'private, no-store'}});
  } catch (error) {
    reportMarketSourceFailure('live-bars', 'hanstock-www', error, { elapsedMs: Date.now() - started, httpStatus, stage, ticker });
    // Do not invent OHLC/volume from a standalone quote or pass delayed bars as live.
    return NextResponse.json({status:'error',error:'live-bars-source-delayed',candles:[]}, {status:503,headers:{'Cache-Control':'private, no-store'}});
  }
}
