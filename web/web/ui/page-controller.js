(function () {
  const money = value => value == null || value === '' ? '—' : Number(value).toLocaleString('zh-TW', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const integer = value => value == null || value === '' ? '—' : Number(value).toLocaleString('zh-TW');
  const signed = (value, digits = 2) => value == null || value === '' ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
  const cls = value => value == null || value === '' ? '' : (Number(value) >= 0 ? 'text-up' : 'text-down');
  const formatTime = value => {
    if (!value) return '—';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-TW', {hour12: false});
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
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getRealtimeLatest();
        const quotes = data.quotes || [];
        setSource(data);
        text('summaryUp', data.summary?.up ?? 0);
        text('summaryDown', data.summary?.down ?? 0);
        text('summaryRule1', data.summary?.rule1_passed ?? 0);
        text('summaryGroups', data.summary?.group_count ?? 0);
        text('updateTime', formatTime(data.updated_at));
        const dot = byId('connDot'); const conn = byId('connText');
        if (dot) dot.className = 'dot dot-ok'; if (conn) conn.textContent = data._meta?.is_mock ? '展示模式' : '已連線';
        byId('quoteList').innerHTML = quotes.map(q => `<a class="quote-row quote-link" href="05-stock-detail.html?code=${encodeURIComponent(q.code)}"><span class="quote-code">${escape(q.code)}</span><span class="quote-name">${escape(q.name)}</span><span class="quote-price ${cls(q.change_rate)}">${money(q.price)}</span><span class="quote-change ${cls(q.change_rate)}">${signed(q.change_rate)}%</span><span class="${cls(q.price_change)}">${signed(q.price_change)}</span><span class="quote-vol">${integer(q.volume)}</span><span style="font-size:12px">${money(q.bid)}/${money(q.ask)}</span></a>`).join('');
        byId('mobileQuoteList').innerHTML = quotes.map(q => `<a class="m-card quote-link" href="05-stock-detail.html?code=${encodeURIComponent(q.code)}"><div class="m-card__info"><span class="m-card__name">${escape(q.name)}</span><span class="m-card__code">${escape(q.code)}</span></div><div><div class="m-card__price ${cls(q.change_rate)}">${money(q.price)}</div><div class="m-card__change ${cls(q.change_rate)}">${signed(q.change_rate)}%</div></div><div class="m-card__row"><span>買 <span class="val">${money(q.bid)}</span></span><span>賣 <span class="val">${money(q.ask)}</span></span><span>量 <span class="val">${integer(q.volume)}</span></span></div></a>`).join('');
        quotes.length ? UIStates.showData(stateId) : UIStates.showEmpty(stateId);
      } catch (error) { console.error(error); UIStates.fromError(stateId, error); }
    }
    bindRetry(load); createStateControls(stateId); await load();
    timer = setInterval(() => { if (!document.hidden) load(); }, HanStockConfig.realtimeRefreshMs);
    window.addEventListener('beforeunload', () => clearInterval(timer));
  }

  async function initGroups() {
    const stateId = 'groupContent';
    let items = [];
    const grid = byId('groupGrid');
    function render() {
      const mode = byId('sortSelect')?.value || 'change_desc';
      const sorted = [...items].sort((a,b) => mode === 'change_asc' ? a.change_rate-b.change_rate : mode === 'name' ? a.name.localeCompare(b.name,'zh-Hant') : mode === 'count' ? b.count-a.count : b.change_rate-a.change_rate);
      grid.innerHTML = sorted.map(g => {
        const upPct = g.count ? Math.round((g.up/g.count)*100) : 0;
        const hasMarket = g.market_data_available !== false && g.change_rate != null; return `<article class="group-card" tabindex="0"><div class="group-card__header"><span class="group-card__name">${escape(g.name)}</span><span class="group-card__change ${cls(g.change_rate)}">${hasMarket ? signed(g.change_rate)+'%' : '待接行情'}</span></div><div class="group-card__bar"><div class="group-card__bar-up" style="width:${hasMarket?upPct:0}%"></div><div class="group-card__bar-down" style="width:${hasMarket?100-upPct:0}%"></div></div><div class="group-card__stats">${hasMarket?`<span>🔺 ${g.up} 檔</span><span>🔽 ${g.down} 檔</span>`:'<span>尚未接雲端即時行情</span>'}<span>共 ${g.count} 檔</span></div></article>`;
      }).join('');
    }
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data = await HanStockAPI.getGroups();
        setSource(data); items = data.items || []; text('groupCount', `${data.count ?? items.length} 族群`); render();
        items.length ? UIStates.showData(stateId) : UIStates.showEmpty(stateId);
      } catch(error) { console.error(error); UIStates.fromError(stateId,error); }
    }
    byId('sortSelect')?.addEventListener('change', render); bindRetry(load); createStateControls(stateId); await load();
  }

  async function initRule1() {
    const stateId='ruleContent';
    async function load() {
      UIStates.showLoading(stateId);
      try {
        const data=await HanStockAPI.getRule1Latest(); setSource(data);
        text('rule1ScanTime', `掃描時間 ${formatTime(data.generated_at)}`);
        text('rule1TotalGroups', data.summary?.total_groups ?? 0); text('rule1GroupsWithResults', data.summary?.groups_with_results ?? 0); text('rule1Passed', data.summary?.total_passed ?? data.summary?.total_passed_records ?? 0); text('rule1Duration', `${((data.duration_ms||0)/1000).toFixed(1)} 秒`);
        const groups=data.groups||[];
        byId('rule1List').innerHTML=groups.map(group => {
          const stocks=group.stocks||group.passed_stocks||[];
          if (!stocks.length) return `<section class="result-group result-group--empty"><div class="result-group__header"><strong>${escape(group.group_name)}</strong><span class="pill">無符合股票</span></div></section>`;
          return `<section class="result-group"><div class="result-group__header"><strong>${escape(group.group_name)}</strong><span class="pill">${stocks.length} 檔</span></div><div class="result-list">${stocks.map(item=>{const s={code:item.code??item.stock_code,name:item.name??item.stock_name,price:item.price??item.today_close,change_rate:item.change_rate,price_change:item.price_change};return `<a class="result-card result-link" href="05-stock-detail.html?code=${encodeURIComponent(s.code)}"><div class="result-card__group">${escape(group.group_name)}</div><div><div class="result-card__name">${escape(s.name)}</div><div class="result-card__code">${escape(s.code)}</div></div><div><div class="result-card__price ${cls(s.change_rate)}">${money(s.price)}</div><div class="result-card__change ${cls(s.change_rate)}">${signed(s.change_rate)}% (${signed(s.price_change)})</div></div></a>`}).join('')}</div></section>`;
        }).join('');
        groups.length ? UIStates.showData(stateId) : UIStates.showEmpty(stateId);
      } catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initRule2(){
    const stateId='rule2Content';
    async function load(){
      UIStates.showLoading(stateId);
      try{
        const data=await HanStockAPI.getRule2Latest();setSource(data);
        text('rule2ScanTime',`掃描 ${formatTime(data.generated_at)}`);text('rule2Scanned',data.summary?.scanned??0);text('rule2Signals',data.summary?.signals??0);text('rule2Long',data.summary?.long??0);text('rule2Short',data.summary?.short??0);text('rule2Version',data.rule?.version||'draft');text('rule2Enabled',data.rule?.enabled?'已啟用':'尚未啟用');text('rule2Notice',data.notice||'');
        const signals=data.signals||[];
        byId('rule2List').innerHTML=signals.map(s=>`<a class="signal-card result-link" href="05-stock-detail.html?code=${encodeURIComponent(s.code)}"><div class="signal-card__icon ${s.side==='short'?'signal-card__icon--sell':'signal-card__icon--buy'}">${s.side==='short'?'📉':'📈'}</div><div><div class="signal-card__name">${escape(s.name)} <span style="color:var(--muted);font-weight:400;font-size:12px">${escape(s.code)}</span></div><div class="signal-card__detail">${escape(s.group_name)} ｜ <span class="signal-card__signal ${s.side==='short'?'text-down':'text-up'}">${escape(s.signal)}</span></div></div><div><div class="signal-card__price ${s.side==='short'?'text-down':'text-up'}">${money(s.price)}</div><div class="signal-card__time">${escape(s.time)}</div></div></a>`).join('');
        signals.length?UIStates.showData(stateId):UIStates.showEmpty(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initStock(){
    const stateId='stockContent';
    const code=new URLSearchParams(location.search).get('code')||'2344';
    async function load(){
      UIStates.showLoading(stateId);
      try{
        const s=await HanStockAPI.getStock(code);setSource(s);document.title=`HanStock - ${s.name} ${s.code}`;
        text('stockName',s.name);text('stockCodeGroups',`${s.code} ｜ ${(s.groups||[]).join(' ｜ ')}`);text('stockPrice',money(s.price));text('stockChange',`${signed(s.price_change)} (${signed(s.change_rate)}%)`);
        const price=byId('stockPrice'),change=byId('stockChange'); if(price)price.className=`stock-hero__price ${cls(s.change_rate)}`;if(change)change.className=`stock-hero__change ${cls(s.change_rate)}`;
        [['stockOpen',s.open],['stockHigh',s.high],['stockLow',s.low],['stockPrevClose',s.previous_close],['stockBid',s.bid],['stockAsk',s.ask]].forEach(([id,v])=>text(id,money(v)));text('stockVolume',integer(s.volume));text('stockUpdated',formatTime(s.updated_at));
        byId('stockGroups').innerHTML=(s.groups||[]).map(g=>`<span class="group-tag">${escape(g)}</span>`).join('');
        text('stockSignals',(s.signals||[]).length?`${s.signals.length} 筆訊號`:'最近 7 日內無 Rule1/Rule2 觸發紀錄');UIStates.showData(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  async function initSystem(){
    const stateId='sysContent';
    async function load(){
      UIStates.showLoading(stateId);
      try{
        const data=await HanStockAPI.getSystemStatus();setSource(data);text('systemUpdated',`更新 ${formatTime(data.updated_at)}`);
        byId('statusGrid').innerHTML=(data.services||[]).map(s=>{const iconClass=s.status==='ok'?'status-card__icon--ok':s.status==='warn'||s.status==='mock'?'status-card__icon--warn':'status-card__icon--err';const valueClass=s.status==='ok'?'text-good':s.status==='warn'||s.status==='mock'?'text-accent':'text-bad';return `<div class="status-card"><div class="status-card__icon ${iconClass}">${escape(s.icon)}</div><div class="status-card__body"><div class="status-card__title">${escape(s.title)}</div><div class="status-card__value ${valueClass}">${escape(s.value)}</div><div class="status-card__detail">${escape(s.detail)}</div></div></div>`}).join('');
        byId('logList').innerHTML=(data.logs||[]).map(l=>`<div class="log-item"><span style="color:var(--muted)">${escape(l.time)}</span><span class="log-level log-level--${String(l.level).toLowerCase()}">${escape(l.level)}</span><span>${escape(l.source)}</span><span>${escape(l.message)}</span></div>`).join('');
        UIStates.showData(stateId);
      }catch(error){console.error(error);UIStates.fromError(stateId,error);}
    }
    bindRetry(load);createStateControls(stateId);await load();
  }

  document.addEventListener('DOMContentLoaded',()=>{
    const map={realtime:initRealtime,groups:initGroups,rule1:initRule1,rule2:initRule2,stock:initStock,system:initSystem};
    const init=map[document.body.dataset.page];if(init)init();
  });
})();
