/** Start independent sources together, consume successes without waiting for a slow peer. */
export async function* successfulSourcesInCompletionOrder<T>(
  loaders: Array<(signal: AbortSignal) => Promise<T>>,
  onError: (error: unknown) => void = () => {},
): AsyncGenerator<T> {
  const controller = new AbortController();
  const pending = new Map(loaders.map((load, index) => [index,
    Promise.resolve().then(() => load(controller.signal)).then(
      value => ({ index, status: "fulfilled" as const, value }),
      reason => ({ index, status: "rejected" as const, reason }),
    ),
  ]));
  try {
    while (pending.size) {
      const result = await Promise.race(pending.values());
      pending.delete(result.index);
      if (result.status === "fulfilled") yield result.value;
      else onError(result.reason);
    }
  } finally {
    // Breaking/returning from the consumer also stops outstanding fetches.
    controller.abort();
  }
}
