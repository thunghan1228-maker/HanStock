/** API client with timeout, no-store semantics, and explicit mock fallback. */
(function () {
  const cfg = window.HanStockConfig;

  class HanStockAPIError extends Error {
    constructor(message, details = {}) {
      super(message);
      this.name = 'HanStockAPIError';
      Object.assign(this, details);
    }
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function mockValue(key, params = {}) {
    const data = window.HanStockMockData;
    if (!data) throw new HanStockAPIError('Mock data is unavailable', {kind: 'mock'});
    if (key === 'stock') {
      const item = data.stocks[String(params.code || '')];
      if (!item) throw new HanStockAPIError('Stock not found', {kind: 'not_found', status: 404});
      return item;
    }
    return data[key];
  }

  async function request(path, {method = 'GET', body, mockKey, mockParams} = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), cfg.requestTimeoutMs);
    try {
      const response = await fetch(`${cfg.apiBaseUrl}${path}`, {
        method,
        body: body ? JSON.stringify(body) : undefined,
        headers: {'Accept': 'application/json', ...(body ? {'Content-Type': 'application/json'} : {})},
        cache: 'no-store',
        signal: controller.signal
      });
      if (!response.ok) {
        throw new HanStockAPIError(`HTTP ${response.status}`, {kind: response.status === 404 ? 'not_found' : 'http', status: response.status});
      }
      const payload = await response.json();
      if (payload && typeof payload === 'object' && !payload._meta) {
        Object.defineProperty(payload, '_meta', {value: {source: 'api', is_mock: false}, enumerable: true});
      }
      return payload;
    } catch (error) {
      const isAbort = error && error.name === 'AbortError';
      const offline = !navigator.onLine;
      if (cfg.useMockFallback && mockKey) {
        const payload = clone(mockValue(mockKey, mockParams));
        payload._meta = {
          source: 'mock-fallback',
          is_mock: true,
          reason: offline ? 'offline' : (isAbort ? 'timeout' : 'api-unavailable')
        };
        return payload;
      }
      if (error instanceof HanStockAPIError) throw error;
      throw new HanStockAPIError(isAbort ? 'Request timed out' : 'Network request failed', {
        kind: offline ? 'offline' : (isAbort ? 'timeout' : 'network'), cause: error
      });
    } finally {
      clearTimeout(timer);
    }
  }

  window.HanStockAPI = Object.freeze({
    getRealtimeLatest: () => request(cfg.endpoints.realtime, {mockKey: 'realtime'}),
    getGroups: () => request(cfg.endpoints.groups, {mockKey: 'groups'}),
    getRule1Latest: () => request(cfg.endpoints.rule1, {mockKey: 'rule1'}),
    getRule2Latest: () => request(cfg.endpoints.rule2, {mockKey: 'rule2'}),
    getStock: code => request(cfg.endpoints.stock(code), {mockKey: 'stock', mockParams: {code}}),
    getSystemStatus: () => request(cfg.endpoints.system, {mockKey: 'system'}),
    HanStockAPIError
  });
})();
