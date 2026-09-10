export type SavedForceSnapshot<T> = { value: T; available: boolean; refreshedAt: number; phase: string };
type Entry<T> = { snapshot?: SavedForceSnapshot<T>; read?: Promise<SavedForceSnapshot<T>>; refresh?: Promise<SavedForceSnapshot<T>>; readAt: number; retryAt: number };

/** Serve durable observations immediately; revalidation never blocks existing data. */
export function createSavedForceCache<T>(options: { now?: () => number; empty: () => T; weight: (value: T) => number; merge?: (current: T, incoming: T) => T; maxEntries?: number; maxWeight?: number }) {
  const now = options.now ?? Date.now, entries = new Map<string, Entry<T>>();
  const prune = () => {
    let weight = [...entries.values()].reduce((sum, entry) => sum + (entry.snapshot ? options.weight(entry.snapshot.value) : 0), 0);
    for (const [key, entry] of entries) {
      if (entries.size <= (options.maxEntries ?? 16) && weight <= (options.maxWeight ?? 30_000)) break;
      if (entry.read || entry.refresh) continue;
      entries.delete(key);
      weight -= entry.snapshot ? options.weight(entry.snapshot.value) : 0;
    }
  };
  return async (key: string, policy: { phase: string; ttl: number }, readSaved: () => Promise<SavedForceSnapshot<T>>,
    refresh: (previous: T) => Promise<SavedForceSnapshot<T>>, background: (task: Promise<unknown>) => void) => {
    let entry = entries.get(key);
    if (!entry) { entry = { readAt: 0, retryAt: 0 }; entries.set(key, entry); }
    else { entries.delete(key); entries.set(key, entry); }
    const current = entry;
    if (!current.snapshot || !current.refresh && now() - current.readAt >= 30_000) {
      current.read ??= readSaved().catch(() => current.snapshot ?? { value: options.empty(), available: false, refreshedAt: 0, phase: "" })
        .then(snapshot => {
          if (current.snapshot && options.merge) snapshot = { ...snapshot,
            value: options.merge(current.snapshot.value, snapshot.value),
            available: current.snapshot.available || snapshot.available };
          current.snapshot = snapshot; current.readAt = now(); return snapshot;
        }).finally(() => { current.read = undefined; });
      await current.read;
    }
    const saved = current.snapshot!;
    if (saved.available && saved.phase === policy.phase && now() - saved.refreshedAt < policy.ttl) { prune(); return saved; }
    if (saved.available && current.retryAt > now()) return saved;
    if (!current.refresh) {
      current.refresh = refresh(saved.value).then(snapshot => {
        current.snapshot = snapshot; current.readAt = now(); current.retryAt = 0; return snapshot;
      }).catch(error => { current.retryAt = now() + 10_000; throw error; }).finally(() => { current.refresh = undefined; prune(); });
      // Register the task in this request's execution context before returning.
      background(current.refresh.catch(() => undefined));
    }
    if (saved.available) { prune(); return saved; }
    return current.refresh;
  };
}
