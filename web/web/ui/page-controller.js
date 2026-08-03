(function () {
  const money = value => value == null || value === '' ? '—' : Number(value).toLocaleString('zh-TW', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const integer = value => value == null || value === '' ? '—' : Number(value).toLocaleString('zh-TW');
  const signed = (value, digits = 2) => value == null || value === '' ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
  const cls = value => value == null || value === '' ? '' : (Number(value) >= 0 ? 'text-up' : 'text-down');
  const formatTime = value => {
    if (!value) return '—';
    // 將字串或數字統一轉成 milliseconds
    let ms;
    const raw = String(value).trim();
    // 純數字字串（可能是 ns/us/ms/s timestamp）
    if (/^\d{10,19}$/.test(raw)) {
      // 用 BigInt 避免精度遺失（19 位數超過 Number.MAX_SAFE_INTEGER）
      const big = BigInt(raw);
      if (raw.length >= 19) {
        ms = Number(big / 1000000n); // nanoseconds → ms
      } else if (raw.length >= 16) {
        ms = Number(big / 1000n);    // microseconds → ms
      } else if (raw.length >= 13) {
        ms = Number(big);            // milliseconds
      } else {
        ms = Number(big) * 1000;     // seconds → ms
      }
    } else {
      // ISO 字串或其他格式，直接交給 Date 解析
      ms = new Date(value).getTime();
    }
    if (!ms || Number.isNaN(ms) || ms < 0) return '—';
    const d = new Date(ms);
    // 台灣時間 YYYY/MM/DD HH:mm:ss
    return d.toLocaleString('zh-TW', {
      timeZone: 'Asia/Taipei',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false
    });
  };
  const escape = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const byId = id => document.getElementById(id);
  const text = (id, value) => { const el = byId(id); if (el) el.textContent = value; };

  function bindRetry(load) {
    document.querySelectorAll('[data-action="retry"], [data-action="refresh"]').forEach(btn => {
      btn.addEventListener('click', load);
    });
  }

  function setSource(payload) { window.HanStockShell?.setSourceBadge(payload?._meta || payload?.meta); }

  async function initRealtime() {
    const stateId = 'mainContent';
    let timer = null;
    let firstLoad = true;

    async function load() {
      // 只在首次載入時顯示 skeleton，後續刷新不重設為 loading
      if (firstLoad) {
        UIStates.showLoading(stateId);
      }
      try {
        const data = await HanStockAPI.getRealtimeLatest();
        const quotes = data.quotes || [];
        const meta = data._meta || {};
        setSource(data);

        // 更新統計卡片
        text('summaryUp', data.summary?.up ?? 0);
        text('summaryDown', data.summary?.down ?? 0);
        text('summaryRule1', data.summary?.rule1_passed ?? 0);
        text('summaryGroups', data.summary?.group_count ?? 0);

        // 更新時間顯示
        const timeStr = formatTime(data.updated_at);
        const staleTag = meta.is_stale ? ' (收盤／資料已過期)' : '';
        text('updateTime', timeStr + staleTag);

        // 連線狀態
        const dot = byId('connDot');
        const conn = byId('connText');
        if (meta.is_mock) {
          if (dot) dot.className = 'dot dot-warn';
          if (conn) conn.textContent = '展示模式';
        } else if (meta.is_stale) {
          if (dot) dot.className = 'dot dot-warn';
          if (conn) conn.textContent = '資料已過期';
        } else {
          if (dot) dot.className = 'dot dot-ok';
          if (conn) conn.textContent = '已連線';
        }

        // 渲染行情表格
        if (quotes.length) {
          byId('quoteList').innerHTML = quotes.map(q => `<a class="quote-row quote-link" href="05-stock-detail.html?code=${encodeURIComponent(q.code)}"><span class="quote-code">${escape(q.code)}</span><span class="quote-name">${escape(q.name)}</span><span class="quote-price ${cls(q.change_rate)}">${money(q.price)}</span><span class="quote-change ${cls(q.change_rate)}">${signed(q.change_rate)}%</span><span class="${cls(q.price_change)}">${signed(q.price_change)}</span><span class="quote-vol">${integer(q.volume)}</span><span style="font-size:12px">${money(q.bid)}/${money(q.ask)}</span></a>`).join('');
          byId('mobileQuoteList').innerHTML = quotes.map(q => `<a class="m-card quote-link" href="05-stock-detail.html?code=${encodeURIComponent(q.code)}"><div class="m-card__info"><span class="m-card__name">${escape(q.name)}</span><span class="m-card__code">${escape(q.code)}</span></div><div><div class="m-card__price ${cls(q.change_rate)}">${money(q.price)}</div><div class="m-card__change ${cls(q.change_rate)}">${signed(q.change_rate)}%</div></div><div class="m-card__row"><span>買 <span class="val">${money(q.bid)}</span></span><span>賣 <span class="val">${money(q.ask)}</span></span><span>量 <span class="val">${integer(q.volume)}</span></span></div></a>`).join('');
          UIStates.showData(stateId);
        } else if (data.notice) {
          // 有 notice 但無 quotes：顯示 empty 狀態（不是 skeleton）
          UIStates.showEmpty(stateId);
        } else {
          UIStates.showEmpty(stateId);
        }
      } catch (error) {
        console.error('[HanStock] realtime load error:', error);
        // 錯誤時立即顯示錯誤狀態，不保持 skeleton
        UIStates.fromError(stateId, error);
      } finally {
        firstLoad = false;
      }
    }

    bindRetry(load); createStateControls(stateId); await load();
    timer = setInterval(() => { if (!document.hidden) load(); }, HanStockConfig.realtimeRefreshMs);
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  async function initGroups() {
    const stateId = 'groupContent';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getGroups();setSource(data);
        text('groupScanTime', `掃描 ${formatTime(data.generated_at)}`);text('groupTotal', data.summary?.total_groups ?? 0);text('groupStocks', data.summary?.total_stocks ?? 0);text('groupPassed', data.summary?.total_passed ?? 0);
        const groups = data.groups || [];
        byId('groupList').innerHTML = groups.map(g => `<div class="group-card"><div class="group-card__head"><span class="group-card__name">${escape(g.group_name)}</span><span class="group-card__count">${g.stock_count} 檔</span></div><div class="group-card__body">${g.passed_count ? `<span class="text-up">${g.passed_count} 檔通過</span>` : `<span style="color:var(--muted)">無通過</span>`}</div></div>`).join('');
        groups.length ? UIStates.showData(stateId) : UIStates.showEmpty(stateId);
      } catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initRule1() {
    const stateId = 'rule1Content';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getRule1Latest();setSource(data);
        text('rule1ScanTime', `掃描 ${formatTime(data.generated_at)}`);text('rule1Total', data.summary?.total_groups ?? 0);text('rule1Passed', data.summary?.groups_with_passed ?? 0);text('rule1Stocks', data.summary?.total_passed_records ?? 0);
        const results = (data.groups || []).filter(g => g.passed_count > 0);
        byId('rule1List').innerHTML = results.map(g => `<div class="rule1-group"><div class="rule1-group__head"><span class="rule1-group__name">${escape(g.group_name)}</span><span class="text-up">${g.passed_count} 檔通過</span></div>${(g.passed_stocks||[]).map(s => `<a class="rule1-stock result-link" href="05-stock-detail.html?code=${encodeURIComponent(s.stock_code)}"><span>${escape(s.stock_name)} <small style="color:var(--muted)">${escape(s.stock_code)}</small></span><span class="text-up">${money(s.today_close)}</span><span class="text-up">${signed(s.change_rate)}%</span></a>`).join('')}</div>`).join('');
        results.length ? UIStates.showData(stateId) : UIStates.showEmpty(stateId);
      } catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initRule2() {
    const stateId = 'rule2Content';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getRule2Latest();setSource(data);
        text('rule2ScanTime',`掃描 ${formatTime(data.generated_at)}`);text('rule2Scanned',data.summary?.scanned??0);text('rule2Signals',data.summary?.signals??0);text('rule2Long',data.summary?.long??0);text('rule2Short',data.summary?.short??0);text('rule2Version',data.rule?.version||'draft');text('rule2Enabled',data.rule?.enabled?'已啟用':'尚未啟用');text('rule2Notice',data.notice||'');
        const signals=data.signals||[];
        byId('rule2List').innerHTML=signals.map(s=>`<a class="signal-card result-link" href="05-stock-detail.html?code=${encodeURIComponent(s.code)}"><div class="signal-card__icon ${s.side==='short'?'signal-card__icon--sell':'signal-card__icon--buy'}">${s.side==='short'?'📉':'📈'}</div><div><div class="signal-card__name">${escape(s.name)} <span style="color:var(--muted);font-weight:400;font-size:12px">${escape(s.code)}</span></div><div class="signal-card__detail">${escape(s.group_name)} ｜ <span class="signal-card__signal ${s.side==='short'?'text-down':'text-up'}">${escape(s.signal)}</span></div></div><div><div class="signal-card__price ${s.side==='short'?'text-down':'text-up'}">${money(s.price)}</div><div class="signal-card__time">${escape(s.time)}</div></div></a>`).join('');
        signals.length?UIStates.showData(stateId):UIStates.showEmpty(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initStock() {
    const stateId = 'stockContent';
    const code = new URLSearchParams(location.search).get('code') || '2344';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const s = await HanStockAPI.getStock(code);setSource(s);document.title=`HanStock - ${s.name} ${s.code}`;
        text('stockName',s.name);text('stockCodeGroups',`${s.code} ｜ ${(s.groups||[]).join(' ｜ ')}`);text('stockPrice',money(s.price));text('stockChange',`${signed(s.price_change)} (${signed(s.change_rate)}%)`);
        const price=byId('stockPrice'),change=byId('stockChange'); if(price)price.className=`stock-hero__price ${cls(s.change_rate)}`;if(change)change.className=`stock-hero__change ${cls(s.change_rate)}`;
        [['stockOpen',s.open],['stockHigh',s.high],['stockLow',s.low],['stockPrevClose',s.previous_close],['stockBid',s.bid],['stockAsk',s.ask]].forEach(([id,v])=>text(id,money(v)));text('stockVolume',integer(s.volume));text('stockUpdated',formatTime(s.updated_at));
        byId('stockGroups').innerHTML=(s.groups||[]).map(g=>`<span class="group-tag">${escape(g)}</span>`).join('');
        text('stockSignals',(s.signals||[]).length?`${s.signals.length} 筆訊號`:'最近 7 日內無 Rule1/Rule2 觸發紀錄');UIStates.showData(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initSystem() {
    const stateId = 'sysContent';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getSystemStatus();setSource(data);text('systemUpdated',`更新 ${formatTime(data.updated_at)}`);
        byId('statusGrid').innerHTML=(data.services||[]).map(s=>{const iconClass=s.status==='ok'?'status-card__icon--ok':s.status==='warn'||s.status==='mock'?'status-card__icon--warn':'status-card__icon--err';const valueClass=s.status==='ok'?'text-good':s.status==='warn'||s.status==='mock'?'text-accent':'text-bad';return `<div class="status-card"><div class="status-card__icon ${iconClass}">${escape(s.icon)}</div><div class="status-card__body"><div class="status-card__title">${escape(s.title)}</div><div class="status-card__value ${valueClass}">${escape(s.value)}</div><div class="status-card__detail">${escape(s.detail)}</div></div></div>`}).join('');
        byId('logList').innerHTML=(data.logs||[]).map(l=>`<div class="log-item"><span style="color:var(--muted)">${escape(l.time)}</span><span class="log-level log-level--${String(l.level).toLowerCase()}">${escape(l.level)}</span><span>${escape(l.source)}</span><span>${escape(l.message)}</span></div>`).join('');
        UIStates.showData(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  document.addEventListener('DOMContentLoaded', () => {
    const map = {realtime: initRealtime, groups: initGroups, rule1: initRule1, rule2: initRule2, stock: initStock, system: initSystem};
    const init = map[document.body.dataset.page]; if (init) init();
  });
})();
