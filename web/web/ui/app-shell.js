(function () {
  const links = [
    ['realtime', '即時行情', '01-realtime.html'],
    ['groups', '族群強弱', '02-groups.html'],
    ['rule1', 'Rule1', '03-rule1.html'],
    ['rule2', 'Rule2', '04-rule2.html'],
    ['stock', '個股', '05-stock-detail.html?code=2344'],
    ['system', '系統', '06-system.html']
  ];

  function injectNavigation() {
    const active = document.body.dataset.page;
    const nav = document.createElement('nav');
    nav.className = 'app-nav';
    nav.innerHTML = `<a class="app-brand" href="../index.html"><span class="app-brand__mark">H</span><span>HanStock</span><small>v3.3 preview</small></a><div class="app-nav__links">${links.map(([key,label,href]) => `<a href="${href}" class="${key === active ? 'active' : ''}">${label}</a>`).join('')}</div>`;
    document.body.prepend(nav);
  }

  function setSourceBadge(meta) {
    const badge = document.querySelector('[data-source-badge]');
    if (!badge) return;
    const mock = Boolean(meta?.is_mock);
    badge.textContent = mock ? '展示資料' : '正式 API';
    badge.className = `pill source-badge ${mock ? 'source-badge--mock' : 'source-badge--live'}`;
    badge.title = mock ? `目前來源：${meta?.source || 'mock'}` : '目前來源：正式 API';
  }

  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator) || location.protocol === 'file:') return;
    try { await navigator.serviceWorker.register('../sw.js'); }
    catch (error) { console.warn('Service worker registration failed:', error); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    injectNavigation();
    registerServiceWorker();
  });

  window.HanStockShell = Object.freeze({setSourceBadge});
})();
