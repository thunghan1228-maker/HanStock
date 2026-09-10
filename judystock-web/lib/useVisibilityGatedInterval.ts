export type VisibilityGatedInterval = { cancel(): void };

/** Keep the original cadence, pausing while hidden and refreshing once on return. */
export function createVisibilityGatedInterval(callback: () => void, ms: number): VisibilityGatedInterval {
  if (typeof window === "undefined" || typeof document === "undefined") return { cancel() {} };

  let interval: number | undefined;
  let cancelled = false;
  let wasVisible = document.visibilityState === "visible";

  const pause = () => {
    if (interval === undefined) return;
    window.clearInterval(interval);
    interval = undefined;
  };

  const tick = () => {
    if (cancelled) return;
    // A timer task may already be queued when the tab becomes hidden.
    if (document.visibilityState !== "visible") {
      wasVisible = false;
      pause();
      return;
    }
    callback();
  };

  const schedule = () => {
    if (!cancelled && interval === undefined && document.visibilityState === "visible") {
      interval = window.setInterval(tick, ms);
    }
  };

  const onVisibilityChange = () => {
    const visible = document.visibilityState === "visible";
    if (cancelled || visible === wasVisible) return;
    wasVisible = visible;
    if (!visible) {
      pause();
      return;
    }
    try {
      tick();
    } finally {
      // Preserve future ticks even if this callback throws. A callback may also
      // cancel its own timer or hide the page, so schedule checks both again.
      schedule();
    }
  };

  document.addEventListener("visibilitychange", onVisibilityChange);
  schedule();

  return {
    cancel() {
      if (cancelled) return;
      cancelled = true;
      pause();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    },
  };
}
