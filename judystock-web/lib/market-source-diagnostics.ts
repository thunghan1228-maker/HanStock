type DiagnosticContext = {
  elapsedMs?: number;
  httpStatus?: number;
  stage?: string;
  ticker?: string;
  alternatives?: readonly string[];
};

const lastWarnings = new Map<string, { at: number; suppressed: number }>();
const WARNING_INTERVAL_MS = 60_000;

function describeError(error: unknown, alternatives: readonly string[] = [], depth = 0): unknown {
  const detail = error && typeof error === "object" ? error as { name?: unknown; message?: unknown; errors?: unknown } : null;
  const name = typeof detail?.name === "string" ? detail.name : "Error";
  // JSON parse errors may quote response bodies. Never log bodies, headers,
  // credentials or full URLs; retain bounded provider error descriptions only.
  const raw = name === "SyntaxError" ? "invalid JSON response" : typeof detail?.message === "string" ? detail.message : String(error);
  const message = raw.replace(/https?:\/\/\S+/gi, "[url]")
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/\b(token|authorization|cookie|api[_-]?key)\b\s*[:=]\s*[^,;\s]+/gi, "$1=[redacted]")
    .replace(/[\r\n\t]/g, " ").slice(0, 240);
  const causes = depth < 2 && Array.isArray(detail?.errors)
    ? detail.errors.slice(0, 6).map((cause, index) => ({ source: alternatives[index] ?? `attempt-${index + 1}`, error: describeError(cause, [], depth + 1) }))
    : undefined;
  return { name: name.slice(0, 80), message, ...(causes ? { causes } : {}) };
}

export function reportMarketSourceFailure(route: string, source: string, error: unknown, context: DiagnosticContext = {}) {
  // Diagnostics must never change source selection, response data or exceptions.
  try {
    const now = Date.now(), key = `${route}:${source}`, prior = lastWarnings.get(key);
    if (prior && now - prior.at < WARNING_INTERVAL_MS) { prior.suppressed++; return; }
    if (!prior && lastWarnings.size >= 64) lastWarnings.delete(lastWarnings.keys().next().value!);
    lastWarnings.set(key, { at: now, suppressed: 0 });
    console.warn("[HanStock source failure]", JSON.stringify({
      event: "market-source-failure", route, source, at: new Date(now).toISOString(),
      ...(context.elapsedMs === undefined ? {} : { elapsedMs: context.elapsedMs }),
      ...(context.httpStatus === undefined ? {} : { httpStatus: context.httpStatus }),
      ...(context.stage === undefined ? {} : { stage: context.stage }),
      ...(context.ticker === undefined ? {} : { ticker: context.ticker }),
      suppressedSinceLastWarning: prior?.suppressed ?? 0,
      error: describeError(error, context.alternatives),
    }));
  } catch { /* Logging failures must not mask the original source failure. */ }
}

export async function observeMarketSource<T>(route: string, source: string, pending: Promise<T>, alternatives?: readonly string[]): Promise<T> {
  const started = Date.now();
  try { return await pending; }
  catch (error) {
    reportMarketSourceFailure(route, source, error, { elapsedMs: Date.now() - started, alternatives });
    throw error;
  }
}
