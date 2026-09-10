/** Retain public candle observations across iframe mounts; React Query still refreshes normally. */
export function candleSnapshotBootstrap() {
  return `<script id="hanstock-candle-snapshots">
(()=>{
const prefix="hanstock-candle-snapshot-v2:",memory=new Map(),fetchOriginal=window.fetch.bind(window);
const read=(ticker,interval)=>{const key=prefix+ticker+":"+interval;if(memory.has(key))return memory.get(key);try{const saved=JSON.parse(localStorage.getItem(key)||"null");if(saved&&Date.now()-saved.savedAt<7*864e5&&saved.data?.candles?.length){memory.set(key,saved);return saved}}catch{}return null};
window.__hanstockReadCandleSnapshot=(ticker,interval)=>read(ticker,interval)?.data;
window.__hanstockCandleSnapshotTime=(ticker,interval)=>read(ticker,interval)?.savedAt||0;
const save=(ticker,interval,data)=>{if(!data?.candles?.length)return;const key=prefix+ticker+":"+interval,snapshot={data,savedAt:Date.now()},serialized=JSON.stringify(snapshot);if(serialized.length>500000)return;memory.delete(key);memory.set(key,snapshot);while(memory.size>5)memory.delete(memory.keys().next().value);try{const keys=[];for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k?.startsWith(prefix)&&k!==key){const value=localStorage.getItem(k)||"";let time=0;try{time=JSON.parse(value).savedAt||0}catch{}keys.push({key:k,time,size:value.length})}}keys.sort((a,b)=>a.time-b.time);let size=serialized.length+keys.reduce((n,k)=>n+k.size,0);while(keys.length>=5||size>1200000){const old=keys.shift();if(!old)break;localStorage.removeItem(old.key);size-=old.size}localStorage.setItem(key,serialized)}catch{}};
window.fetch=async(...args)=>{const response=await fetchOriginal(...args);try{const url=new URL(typeof args[0]==="string"?args[0]:args[0]?.url,location.origin);if(url.pathname!=="/api/trpc/stocks.candles"||!response.ok)return response;const input=JSON.parse(url.searchParams.get("input")||"null"),batch=url.searchParams.get("batch")==="1",query=(batch?input?.[0]:input)?.json;if(!query?.ticker||!["1d","1m","5m"].includes(query.interval))return response;const payload=await response.clone().json(),data=(batch?payload?.[0]:payload)?.result?.data?.json;save(query.ticker,query.interval,data)}catch{}return response};
})();
</script>`;
}
