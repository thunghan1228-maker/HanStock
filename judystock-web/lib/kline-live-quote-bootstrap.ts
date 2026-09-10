/** The latest trade quote is independent of the delayed candle provider. */
export function klineLiveQuoteBootstrap() {
  return `<script id="hanstock-live-quote-status">
(()=>{
let quote=null,received=0,mode='',sourceAt='',lastText='';
const format=n=>Number(n).toLocaleString('zh-TW',{maximumFractionDigits:2}),clock=t=>new Date(t).toLocaleTimeString('zh-TW',{timeZone:'Asia/Taipei',hour12:false});
const place=strip=>{const anchor=document.querySelector?.('div[data-loc="client/src/components/CandleChartDialog.tsx:1622"]')||document.querySelector?.('.hanstock-battle-stock-quote')?.parentElement;if(anchor&&strip.previousElementSibling!==anchor)anchor.insertAdjacentElement('afterend',strip);else if(!strip.isConnected&&!strip.parentElement)document.body?.appendChild(strip)};
const draw=()=>{
 if(!document.body)return;
 let strip=document.getElementById('hanstock-live-quote-strip');
 if(!strip){strip=document.createElement('div');strip.id='hanstock-live-quote-strip';strip.setAttribute('role','status');strip.style.cssText='box-sizing:border-box;position:relative;z-index:39;display:flex;flex:0 0 auto;width:100%;min-height:30px;align-items:center;padding:5px 10px;background:#10191ff2;border-top:1px solid #34414c;border-bottom:1px solid #667580;color:#f1cf75;font:700 12px/1.45 system-ui;pointer-events:none'}
 place(strip);
 const latest=window.__hanstockAllCandles?.at(-1),date=String(latest?.date||'');
 const fullDate=/^\\d{2}\\/\\d{2} /.test(date)?new Date().getFullYear()+'/'+date:date;
 const candleAt=Date.parse(fullDate.replaceAll('/','-').replace(' ','T')+(fullDate.includes(' ')?':00+08:00':''));
 const delayed=!Number.isFinite(candleAt)||Date.now()-candleAt>7*60e3;
 const pct=Number(quote?.changePct),value=quote?'最新成交 '+format(quote.price)+'  '+(pct>0?'+':'')+format(pct)+'%':'最新成交讀取中';
 const text=value+'｜報價 '+(sourceAt?clock(sourceAt):'等待資料')+(received&&Date.now()-received>25000?'（重連中）':'')+'｜K 線截至 '+(date||'等待資料')+(delayed?'（分 K 來源延遲，等待回補）':'')+(mode&&mode!=='live'?'｜收盤備援':'');
 if(text!==lastText){strip.textContent=text;lastText=text}
};
window.addEventListener('hanstock-live-quote',event=>{const d=event.detail;if(!d?.quote||!Number.isFinite(d.quote.price))return;quote=d.quote;received=Date.now();sourceAt=d.fetchedAt||'';mode=d.mode||'';draw()});
if(typeof MutationObserver!=='undefined')new MutationObserver(()=>{const strip=document.getElementById('hanstock-live-quote-strip');if(strip)place(strip)}).observe(document.documentElement,{subtree:true,childList:true});
window.addEventListener('hanstock-candles-updated',draw);document.addEventListener('DOMContentLoaded',draw);window.addEventListener('load',draw);let pollTimer=null;const syncPoll=()=>{if(pollTimer!==null){clearInterval(pollTimer);pollTimer=null}if(!document.hidden)pollTimer=setInterval(draw,5000)};document.addEventListener('visibilitychange',syncPoll);syncPoll();
})();
</script>`;
}
