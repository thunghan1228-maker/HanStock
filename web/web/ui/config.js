/** HanStock browser configuration. No secret may be placed in this file. */
window.HanStockConfig = Object.freeze({
  apiBaseUrl: window.HANSTOCK_API_BASE_URL || '',
  requestTimeoutMs: 8000,
  realtimeRefreshMs: 2000,
  // Production must never silently replace failed live data with fake quotes.
  useMockFallback: window.HANSTOCK_USE_MOCK_FALLBACK === true || new URLSearchParams(location.search).get('demo') === '1',
  showDemoControls: new URLSearchParams(location.search).get('demo') === '1',
  endpoints: Object.freeze({
    realtime: '/api/realtime/latest',
    groups: '/api/groups',
    rule1: '/api/rule1/latest',
    rule2: '/api/rule2/latest',
    stock: code => `/api/stocks/${encodeURIComponent(code)}`,
    system: '/api/admin/status'
  })
});
