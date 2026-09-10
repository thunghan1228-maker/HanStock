import { getMarketRequestScope } from "./market-request-scope.ts";

/** Cache completed values globally; share pending I/O only within its request. */
export function timedSingleFlight<T>(ttlMs: number, loader: () => Promise<T>) {
  let cached: { value: T; expiresAt: number } | undefined;
  const pendingByScope = new WeakMap<object, Promise<T>>();
  const standaloneScope = {}; // Node callers without a Worker request wrapper.
  return () => {
    if (cached && cached.expiresAt > Date.now()) return Promise.resolve(cached.value);
    const scope = getMarketRequestScope() ?? standaloneScope;
    const pending = pendingByScope.get(scope);
    if (pending) return pending;
    const loading = Promise.resolve().then(loader).then(value => {
      cached = { value, expiresAt: Date.now() + ttlMs };
      return value;
    }).finally(() => { pendingByScope.delete(scope); });
    pendingByScope.set(scope, loading);
    return loading;
  };
}

/** Bound keyed caches as well; their pending I/O remains request scoped. */
export function timedKeyedSingleFlight<K extends string, T>(ttlMs: number, loader: (key: K) => Promise<T>, maxKeys = 32) {
  const loaders = new Map<K, () => Promise<T>>();
  return (key: K) => {
    let load = loaders.get(key);
    if (!load) {
      load = timedSingleFlight(ttlMs, () => loader(key));
      loaders.set(key, load);
      while (loaders.size > maxKeys) loaders.delete(loaders.keys().next().value!);
    }
    return load();
  };
}
