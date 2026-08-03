/** HanStock UI state manager. */
const UIStates = {
  setState(containerId, state) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.querySelectorAll(':scope > [data-state]').forEach(el => {
      el.classList.toggle('hidden', el.dataset.state !== state);
    });
    container.dataset.currentState = state;
  },
  showLoading(id) { this.setState(id, 'loading'); },
  showData(id) { this.setState(id, 'data'); },
  showEmpty(id) { this.setState(id, 'empty'); },
  showOffline(id) { this.setState(id, 'offline'); },
  showError(id) { this.setState(id, 'error'); },
  fromError(id, error) {
    if (!navigator.onLine || error?.kind === 'offline') this.showOffline(id);
    else if (error?.kind === 'not_found') this.showEmpty(id);
    else this.showError(id);
  }
};

function createStateControls(containerId) {
  if (!window.HanStockConfig?.showDemoControls) return;
  const controls = document.createElement('div');
  controls.className = 'state-controls';
  controls.setAttribute('aria-label', 'UI 狀態展示控制');
  controls.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999;display:flex;gap:6px;flex-wrap:wrap;max-width:300px';
  ['data', 'loading', 'empty', 'offline', 'error'].forEach(state => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = state;
    btn.className = 'btn btn-secondary';
    btn.style.cssText = 'font-size:11px;padding:6px 10px';
    btn.onclick = () => UIStates.setState(containerId, state);
    controls.appendChild(btn);
  });
  document.body.appendChild(controls);
}
