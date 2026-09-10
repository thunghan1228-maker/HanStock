import { getMarketRequestScope } from "./market-request-scope.ts";

// Bound decoded bodies and each request's fan-out. A global waiting queue can
// retain promises owned by canceled Worker requests and stall every later scan.
const MAX_JSON_BYTES = 4 * 1024 * 1024;

export async function fetchBoundedMarketJson<T>(input: string | URL, init: RequestInit | (() => RequestInit)): Promise<T> {
  const scope = getMarketRequestScope();
  if (scope) {
    if (scope.active >= 2) await new Promise<void>(resolve => scope.waiting.push(resolve));
    else scope.active++;
  }
  try {
    // Create a timeout only after acquiring a slot; queue time is not network time.
    const response = await fetch(input, typeof init === "function" ? init() : init);
    if (!response.ok) { await response.body?.cancel(); throw new Error(`market-data-${response.status}`); }
    const reader = response.body?.getReader();
    if (!reader) throw new Error('market-data-empty');
    const decoder = new TextDecoder();
    let size = 0, text = '';
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_JSON_BYTES) { await reader.cancel(); throw new Error('market-data-payload-too-large'); }
        text += decoder.decode(value, { stream: true });
      }
      text += decoder.decode();
      return JSON.parse(text) as T;
    } finally { reader.releaseLock(); }
  } finally {
    if (scope) {
      const next = scope.waiting.shift();
      if (next) next(); else scope.active--;
    }
  }
}

/** Preserve every item, but do not launch the whole market in one Promise.all. */
export async function mapMarketBatches<T, R>(items: T[], run: (item: T) => Promise<R>): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = [];
  let cursor = 0;
  await Promise.all(Array.from({length: Math.min(2, items.length)}, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      try { results[index] = {status:'fulfilled', value:await run(items[index])}; }
      catch (reason) { results[index] = {status:'rejected', reason}; }
    }
  }));
  return results;
}
