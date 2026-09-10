import { AsyncLocalStorage } from "node:async_hooks";

type MarketRequestScope = {
  active: number;
  waiting: Array<() => void>;
};

// Worker I/O belongs to the request that started it. In-flight promises and
// semaphore waiters must never outlive that request and block another viewer.
const scopes = new AsyncLocalStorage<MarketRequestScope>();

export function withMarketRequestScope<T>(run: () => T): T {
  return scopes.run({ active: 0, waiting: [] }, run);
}

export function getMarketRequestScope() {
  return scopes.getStore();
}
