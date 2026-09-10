/** Shared by full, grid, OTC and watchlist charts; price studies are never hidden. */
export function signalVisibilityBootstrap() {
  return `<style id="hanstock-five-minute-signal-visibility-style">
html[data-hanstock-five-minute-signals="hidden"] g[data-loc="client/src/components/CandleChartDialog.tsx:2288"],
html[data-hanstock-five-minute-signals="hidden"] #hanstock-signal-overlay,
html[data-hanstock-five-minute-signals="hidden"] #hanstock-signal-leaders,
html[data-hanstock-five-minute-signals="hidden"] #hanstock-signal-tooltip {display:none!important}
#hanstock-five-minute-signal-controls {display:flex!important;flex:0 0 auto!important;align-items:center;justify-content:flex-end;min-height:36px;gap:8px;padding:3px 10px;background:#10151e;color:#e2e8f0;font:600 14px/1.4 system-ui;box-sizing:border-box}
#hanstock-five-minute-signal-toggle {appearance:none;border:1px solid #dce5ee;border-radius:6px;background:#253346;color:#fff;min-height:30px;padding:3px 12px;cursor:pointer;font:inherit;white-space:normal}
#hanstock-five-minute-signal-toggle[aria-pressed="false"] {background:#10151e;color:#cbd5e1}
#hanstock-five-minute-signal-toggle:focus-visible {outline:2px solid #facc15;outline-offset:2px}
#hanstock-five-minute-signal-toggle:disabled {opacity:.6;cursor:default}
</style><script id="hanstock-five-minute-signal-visibility">
(()=>{
const key='hanstock.fiveMinuteSignals.visible.v2',root=document.documentElement;
let visible=false,queued=false;
try{visible=localStorage.getItem(key)==='true'}catch{}
const period=()=>window.__hanstockActiveInterval||new URLSearchParams(location.search).get('interval')||'5m';
const set=(node,name,value)=>{if(node.getAttribute(name)!==value)node.setAttribute(name,value)};
const apply=()=>{
  queued=false;const enabled=period()==='5m',hidden=enabled&&!visible;
  set(root,'data-hanstock-five-minute-signals',hidden?'hidden':'shown');
  window.__hanstockSignalMarkersVisible=!hidden;
  const chart=document.querySelector('[data-loc="client/src/components/CandleChartDialog.tsx:1986"]');
  if(!chart)return;
  let controls=document.getElementById('hanstock-five-minute-signal-controls');
  if(!controls){
    controls=document.createElement('div');controls.id='hanstock-five-minute-signal-controls';
    const button=document.createElement('button');button.id='hanstock-five-minute-signal-toggle';button.type='button';
    button.addEventListener('click',event=>{event.stopPropagation();if(period()!=='5m')return;visible=!visible;try{localStorage.setItem(key,String(visible))}catch{}apply();window.dispatchEvent(new CustomEvent('hanstock-signal-visibility-change'))});
    controls.appendChild(button);chart.prepend(controls);
  }
  const button=document.getElementById('hanstock-five-minute-signal-toggle');
  button.disabled=!enabled;
  const label=enabled?'五分 K 訊號：'+(visible?'顯示中 · 方框數字＝同根合併訊號數，滑鼠移入查看、點選展開明細 · 按此隱藏':'已隱藏 · 按此顯示'):'五分 K 訊號（切至五分 K 可用）';
  if(button.textContent!==label)button.textContent=label;
  set(button,'aria-pressed',String(visible));
  set(button,'title','僅切換五分 K 訊號；VWAP、均線與 K 棒保持顯示');
};
const queue=()=>{if(!queued){queued=true;requestAnimationFrame(apply)}};
window.addEventListener('storage',event=>{if(event.key!==key&&event.key!==null)return;try{visible=localStorage.getItem(key)==='true'}catch{}apply();window.dispatchEvent(new CustomEvent('hanstock-signal-visibility-change'))});
window.addEventListener('hanstock-period-change',queue);
new MutationObserver(queue).observe(root,{childList:true,subtree:true});
document.addEventListener('DOMContentLoaded',queue);queue();
})();
</script>`;
}
