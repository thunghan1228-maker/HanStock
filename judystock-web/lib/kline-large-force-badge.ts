// Use the same dated cumulative value as watchlists, independent of chart hover.
export function largeForceBadgeBootstrap(ticker: string) {
  return `<style>
#hanstock-kline-large-force{display:inline-flex;flex-direction:column;justify-content:center;min-width:150px;min-height:42px;padding:5px 12px;border:1px solid #667382;border-radius:9px;background:#25303a;color:#e0e7ee;font-size:16px;font-weight:800;white-space:nowrap;line-height:1.3;box-sizing:border-box}
#hanstock-kline-large-force[data-tone="positive"]{background:#943535;border-color:#de7777;color:#fff}
#hanstock-kline-large-force[data-tone="negative"]{background:#235d41;border-color:#6fb98a;color:#e1ffed}
#hanstock-kline-large-force small{font-size:11px;font-weight:500;opacity:.85}
#hanstock-fixed-search-actions{flex-wrap:wrap}
@media(max-width:820px){#hanstock-kline-large-force{min-width:130px;font-size:14px;padding:4px 8px}}
</style><script id="hanstock-large-force-badge-bootstrap">
(()=>{
const ticker=${JSON.stringify(ticker).replaceAll("<", "\\u003c")};
let date="",row=null,busy=false,lastAttempt=0,queued=false,state="loading",requestId=0,controller=null;
const validRow=(value,day)=>value?.ticker===ticker&&value?.tradeDate===day&&typeof value.forcePct==="number"&&Number.isFinite(value.forcePct);
// Disposable per-tab cache: same stock and trade date only, revalidated in background.
const cacheKey=day=>"hanstock:kline-force:v2:"+ticker+":"+day;
const readCached=day=>{try{const saved=JSON.parse(sessionStorage.getItem(cacheKey(day))||"null");return saved&&Date.now()-saved.savedAt>=0&&Date.now()-saved.savedAt<21600000&&validRow(saved.row,day)?saved.row:null}catch{return null}};
const saveCached=value=>{try{sessionStorage.setItem(cacheKey(value.tradeDate),JSON.stringify({savedAt:Date.now(),row:value}))}catch{}};
// Runtime candles use MM/DD HH:mm; prefer their epoch timestamp for the year.
const dateOf=bar=>{const ts=Number(bar?.ts);if(Number.isFinite(ts)&&ts>0){const stamp=new Date((ts<1e12?ts*1000:ts)+28800000);if(Number.isFinite(stamp.getTime()))return stamp.toISOString().slice(0,10)}const raw=String(bar?.date||"");if(/^\\d{4}-\\d{2}-\\d{2}/.test(raw))return raw.slice(0,10);const short=raw.match(/^(\\d{2})\\/(\\d{2})(?:\\s|$)/);if(!short)return "";const now=new Date(Date.now()+28800000);let year=now.getUTCFullYear(),key=year+"-"+short[1]+"-"+short[2];if(key>now.toISOString().slice(0,10))key=(year-1)+key.slice(4);return key};
const sessionDate=()=>{const all=window.__hanstockAllCandles,visible=window.__hanstockVisibleCandles,bars=Array.isArray(all)&&all.length?all:Array.isArray(visible)?visible:[];return bars.reduce((latest,bar)=>{const value=dateOf(bar);return value>latest?value:latest},"")};
const render=()=>{
const host=document.getElementById("hanstock-fixed-search-actions");if(!host)return;
let badge=document.getElementById("hanstock-kline-large-force");
if(!badge){badge=document.createElement("span");badge.id="hanstock-kline-large-force";badge.setAttribute("role","status");host.appendChild(badge)}
const value=row?.forcePct,valid=typeof value==="number"&&Number.isFinite(value),tone=valid?(value>0?"positive":value<0?"negative":"neutral"):"neutral";
const stamp=Number(row?.barTs),time=Number.isFinite(stamp)&&stamp>0?new Date(stamp+28800000).toISOString().slice(11,16)+" ":"";
const status=valid?(row.source==="historical-ticks"?"收盤累計":time+"累計")+(state==="cached"?" · 更新中":state==="error"?" · 更新暫緩":state==="empty"?" · 等待新資料":""):state==="empty"?"尚無大戶資料":state==="error"?"暫時無法更新":"資料讀取中";
const label="盤中大戶力 "+(valid?(value>0?"+":"")+value.toFixed(1)+"%":"—"),detail=date?date+" "+status:"等待交易日資料",key=label+detail;
if(badge.dataset.key===key)return;
badge.dataset.key=key;badge.dataset.tone=tone;
badge.textContent=label;const caption=document.createElement("small");caption.textContent=detail;badge.appendChild(caption);
};
const refresh=async()=>{
const next=sessionDate();if(next&&next!==date){controller?.abort();requestId++;busy=false;date=next;row=readCached(date);state=row?"cached":"loading";lastAttempt=0}render();
const interval=row?.source==="historical-ticks"?60000:5000;
if(!date||busy||document.hidden||Date.now()-lastAttempt<interval)return;
busy=true;lastAttempt=Date.now();const requestedDate=date,id=++requestId;controller=new AbortController();
try{const response=await fetch("/api/intraday-large-force-values?tradeDate="+requestedDate+"&tickers="+encodeURIComponent(ticker),{cache:"no-store",signal:AbortSignal.any([controller.signal,AbortSignal.timeout(10500)])});
if(!response.ok)throw new Error("Force update failed");
const payload=await response.json(),found=payload?.rows?.find(item=>item?.ticker===ticker&&item?.tradeDate===requestedDate);
if(id===requestId){if(validRow(found,requestedDate)){row=found;state="ready";saveCached(row)}else state="empty"}
}catch{if(id===requestId)state="error"}finally{if(id===requestId){busy=false;render()}}
};
const queue=()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;void refresh()})};
new MutationObserver(queue).observe(document.documentElement,{childList:true,subtree:true});
window.addEventListener("hanstock-candles-updated",queue);document.addEventListener("DOMContentLoaded",queue);document.addEventListener("visibilitychange",queue);
let pollTimer=null;const syncPoll=()=>{if(pollTimer!==null){clearInterval(pollTimer);pollTimer=null}if(!document.hidden)pollTimer=setInterval(queue,5000)};document.addEventListener("visibilitychange",syncPoll);syncPoll();queue();
})();</script>`;
}
