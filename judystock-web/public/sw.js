self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// HanStock is a live market dashboard. Network requests are intentionally not
// cached so installed copies always request the latest quotes and signals.
