/** Public market data only. Never use this cache for account data or mutations. */
export type MarketCachePolicy = { ticker?: string; activeMs?: number; history?: boolean };

// Keep the overnight futures session (including Saturday before 05:00) live.
// Unknown symbols use the conservative always-live policy.
export function marketCacheWindow(policy: MarketCachePolicy, now = Date.now()) {
  const clock = new Date(now + 28_800_000);
  const day = clock.getUTCDay(), minute = clock.getUTCHours() * 60 + clock.getUTCMinutes();
  const weekday = day >= 1 && day <= 5;
  const ticker = policy.ticker ?? "";
  const cash = /^(?:\d{4,6}[A-Z]?|OTC|TSE|TAIEX)$/.test(ticker);
  const future = /^(?:TXF|MXF|TMF)[A-Z0-9!]*$/.test(ticker);
  const live = cash ? weekday && minute >= 525 && minute < 875
    : future ? (weekday && ((minute >= 525 && minute < 825) || minute >= 900))
      || (day >= 2 && day <= 6 && minute < 300)
    : true;
  return {
    // Changing phase invalidates an off-hours entry immediately at the open.
    phase: `${clock.toISOString().slice(0, 10)}:${live ? "live" : "closed"}`,
    ttl: policy.history ? 300_000 : live ? policy.activeMs ?? 5_000 : 300_000,
  };
}

type SavedResponse = { body: string; status: number; headers: [string, string][] };
type Entry = { phase: string; expires: number; bytes: number; pending: Promise<SavedResponse> };

export function createMarketFetchCache(
  fetcher: typeof fetch = fetch, now: () => number = Date.now,
  maxEntries = 64, maxBytes = 8 * 1024 * 1024,
) {
  const cache = new Map<string, Entry>();
  const prune = () => {
    let bytes = [...cache.values()].reduce((sum, entry) => sum + entry.bytes, 0);
    for (const [key, entry] of cache) {
      if (entry.expires <= now() || cache.size > maxEntries || bytes > maxBytes) {
        cache.delete(key);
        bytes -= entry.bytes;
      }
    }
  };
  return async (url: string, init: RequestInit, policy: MarketCachePolicy = {}) => {
    if (init.method && init.method !== "GET" || new Headers(init.headers).has("authorization")) {
      throw new Error("market_cache_requires_public_get");
    }
    prune();
    const window = marketCacheWindow(policy, now());
    const key = `${url}|${policy.history ? "history" : policy.activeMs ?? 5000}`;
    let entry = cache.get(key);
    if (entry && entry.phase !== window.phase) { cache.delete(key); entry = undefined; }
    if (entry) { cache.delete(key); cache.set(key, entry); }
    else {
      const created: Entry = { phase: window.phase, expires: Infinity, bytes: 0, pending: Promise.resolve({body: "", status: 200, headers: []}) };
      created.pending = (async () => {
        const response = await fetcher(url, { ...init, cache: "no-store" });
        const body = await response.text();
        const headers = new Headers(response.headers);
        // Fetch has decoded compressed bodies; reconstructed responses must not
        // carry the old wire encoding or compressed Content-Length.
        headers.delete("content-encoding"); headers.delete("content-length");
        const result = { body, status: response.status, headers: [...headers.entries()] };
        const remove = () => { if (cache.get(key) === created) cache.delete(key); };
        if (!response.ok || headers.has("set-cookie") || /private|no-store/i.test(headers.get("cache-control") ?? "")) { remove(); return result; }
        try {
          const payload = JSON.parse(body);
          // Empty/failed providers must recover on the next request.
          const empty = payload?.ok === false || payload?.error || payload?.status === "error"
            || Array.isArray(payload?.bars) && !payload.bars.length
            || Array.isArray(payload) && payload.some(row => row?.error);
          if (empty) { remove(); return result; }
        } catch { remove(); return result; }
        created.bytes = body.length * 2; // Bound retained JS strings, not gzip bytes.
        created.expires = now() + window.ttl;
        prune();
        return result;
      })().catch(error => { if (cache.get(key) === created) cache.delete(key); throw error; });
      cache.set(key, created);
      prune();
      entry = created;
    }
    const saved = await entry.pending;
    return new Response(saved.body, {status: saved.status, headers: saved.headers});
  };
}

export const fetchCachedMarket = createMarketFetchCache();
