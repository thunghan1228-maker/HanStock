/**
 * HanStock 台股族群雷達即時行情連接器。
 *
 * 用法：
 *   HanStockRealtime.setApiBase('https://hanstock.xyz');
 *   const stop = HanStockRealtime.pollGroup('記憶體', {
 *     onData: payload => renderGroup(payload),
 *     onError: error => showError(error),
 *     intervalMs: 1500,
 *   });
 *   // stop();
 */
(function attachHanStockRealtime(global) {
  'use strict';

  let apiBase = 'https://hanstock.xyz';

  function normalizeBase(value) {
    return String(value || '').trim().replace(/\/$/, '');
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
      method: 'GET',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: options.signal,
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {
        // 保留 HTTP 狀態。
      }
      throw new Error(detail);
    }
    return response.json();
  }

  function queryString(params) {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        search.set(key, String(value));
      }
    });
    const encoded = search.toString();
    return encoded ? `?${encoded}` : '';
  }

  function loadGroup(keyword, options = {}) {
    const encoded = encodeURIComponent(keyword);
    return fetchJson(
      `/api/realtime/group/${encoded}${queryString({
        subscribe: options.subscribe !== false,
        sort: options.sort || 'change_desc',
      })}`,
      options,
    );
  }

  function loadStock(code, options = {}) {
    const encoded = encodeURIComponent(code);
    return fetchJson(
      `/api/realtime/${encoded}${queryString({ subscribe: options.subscribe !== false })}`,
      options,
    );
  }

  function loadLatest(codes, options = {}) {
    const codeValue = Array.isArray(codes) ? codes.join(',') : codes;
    return fetchJson(
      `/api/realtime/latest${queryString({
        codes: codeValue,
        subscribe: options.subscribe !== false,
        limit: options.limit || 100,
      })}`,
      options,
    );
  }

  function poll(loader, config = {}) {
    const intervalMs = Math.max(800, Number(config.intervalMs || 1500));
    let timer = null;
    let stopped = false;
    let activeController = null;

    const run = async () => {
      if (stopped) return;
      if (activeController) activeController.abort();
      activeController = new AbortController();
      try {
        const payload = await loader(activeController.signal);
        if (!stopped && typeof config.onData === 'function') config.onData(payload);
      } catch (error) {
        if (!stopped && error.name !== 'AbortError' && typeof config.onError === 'function') {
          config.onError(error);
        }
      } finally {
        if (!stopped) timer = global.setTimeout(run, intervalMs);
      }
    };

    run();
    return function stopPolling() {
      stopped = true;
      if (timer) global.clearTimeout(timer);
      if (activeController) activeController.abort();
    };
  }

  function pollGroup(keyword, config = {}) {
    return poll(
      signal => loadGroup(keyword, {
        signal,
        subscribe: config.subscribe,
        sort: config.sort,
      }),
      config,
    );
  }

  function pollStock(code, config = {}) {
    return poll(
      signal => loadStock(code, { signal, subscribe: config.subscribe }),
      config,
    );
  }

  global.HanStockRealtime = Object.freeze({
    setApiBase(value) {
      const normalized = normalizeBase(value);
      if (!normalized) throw new Error('API base 不可為空。');
      apiBase = normalized;
    },
    getApiBase() {
      return apiBase;
    },
    loadGroup,
    loadStock,
    loadLatest,
    pollGroup,
    pollStock,
  });
})(window);
