"use client";

import { useEffect, useRef, useState } from 'react';
import { weeklyPage, type ResearchRow } from '../lib/weekly-chip-insights';

type OpenStock = (code: string, name: string) => void;
type Financial = { ticker: string; dataDate?: string; pe: number | null; currentPrice: number | null; ttmEps: number | null; quarters: Array<{ label: string; eps: number; ttmEps?: number | null; operatingMargin: number | null }>; revenues: { recent: Array<{ period: string; yoyPct: number | null }> } };
const num = (n: number | null | undefined, suffix = '') => typeof n === 'number' && Number.isFinite(n) ? `${n.toFixed(2)}${suffix}` : '—';
const mean = (values: Array<number | null | undefined>) => { const valid = values.filter((n): n is number => typeof n === 'number' && Number.isFinite(n)); return valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null; };

export function WeeklyFundamentalComparison({ rows, selectedDate, onOpenStock }: { rows: ResearchRow[]; selectedDate: string; onOpenStock: OpenStock }) {
  const current = rows.filter(row => row.points[0]?.score != null && row.market !== 'etf');
  const groups = [...new Set(current.map(row => row.groupName).filter(name => name && name !== '未分類'))].sort();
  const [left, setLeft] = useState('');
  const [right, setRight] = useState('');
  const a = groups.includes(left) ? left : groups[0] ?? '';
  const b = groups.includes(right) && right !== a ? right : groups.find(name => name !== a) ?? '';
  const members = current.filter(row => row.groupName === a || row.groupName === b);
  const memberKey = members.map(row => `${row.code}:${row.market}`).sort().join(',');
  const [requestKey, setRequestKey] = useState('');
  const [data, setData] = useState<Record<string, Financial>>({});
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState<string[]>([]);
  const [retry, setRetry] = useState(0);
  const cache = useRef<Record<string, Financial>>({});
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  useEffect(() => { setRequestKey(''); setPending(false); setFailed([]); setPage(1); }, [memberKey, selectedDate]);
  useEffect(() => {
    if (!requestKey || requestKey !== memberKey) return;
    const controller = new AbortController();
    let cursor = 0;
    const targets = requestKey.split(',').map(value => value.split(':'));
    setPending(true); setFailed([]);
    const load = async () => {
      while (cursor < targets.length && !controller.signal.aborted) {
        const [code, market] = targets[cursor++];
        if (cache.current[code]) continue;
        try {
          const response = await fetch(`/api/fundamental-river?ticker=${encodeURIComponent(code)}&market=${market === 'tpex' || market === '上櫃' ? 'tpex' : 'twse'}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]), cache: 'no-store' });
          const payload = await response.json();
          if (!response.ok || !payload.ok || payload.ticker !== code) throw new Error('unavailable');
          if (!controller.signal.aborted) { cache.current[code] = payload; setData({ ...cache.current }); }
        } catch { if (!controller.signal.aborted) setFailed(value => [...value, code]); }
      }
    };
    void Promise.all(Array.from({ length: Math.min(3, targets.length) }, load)).finally(() => { if (!controller.signal.aborted) setPending(false); });
    return () => controller.abort();
  }, [requestKey, memberKey, retry]);
  const loaded = requestKey === memberKey;
  const summary = (name: string) => {
    const stocks = current.filter(row => row.groupName === name);
    const available = loaded ? stocks.flatMap(row => data[row.code] ? [data[row.code]] : []) : [];
    const positivePE = available.map(row => row.pe).filter((n): n is number => typeof n === 'number' && n > 0);
    const completeQuarters = available.filter(row => row.quarters?.length >= 2);
    return <article><h3>{name || '尚無第二族群'}</h3><p>本週名單涵蓋 {stocks.length} 檔 · 已讀取 {available.length} 檔</p><dl>
      <dt>正本益比平均</dt><dd>{num(mean(positivePE), ' 倍')}<small>有效 {positivePE.length} 檔；非預估 PE</small></dd>
      <dt>最新季營益率平均</dt><dd>{num(mean(available.map(row => row.quarters?.at(-1)?.operatingMargin)), '%')}</dd>
      <dt>最新月營收年增率平均</dt><dd>{num(mean(available.map(row => row.revenues?.recent?.[0]?.yoyPct)), '%')}</dd>
      <dt>最近兩季均虧損</dt><dd>{completeQuarters.filter(row => row.quarters.slice(-2).every(q => q.eps < 0)).length}／{completeQuarters.length} 檔<small>只計財報兩季齊全者</small></dd>
    </dl></article>;
  };
  const paged = weeklyPage(members, page, size);
  return <section><h3>兩族基本面比較</h3><p>以 {selectedDate} 週榜涵蓋的主族群成員，比較目前可取得的最新財報與行情；不代表該週當時的財報，也不代表全族覆蓋。</p>
    <div className="weekly-research-controls"><label>族群一<select value={a} onChange={e => setLeft(e.target.value)}>{groups.map(name => <option key={name}>{name}</option>)}</select></label><label>族群二<select value={b} onChange={e => setRight(e.target.value)}>{groups.filter(name => name !== a).map(name => <option key={name}>{name}</option>)}</select></label><button disabled={!members.length || pending} onClick={() => { setRequestKey(memberKey); setRetry(n => n + 1); }}>{pending ? '資料讀取中…' : failed.length ? '重試缺漏資料' : '載入兩族資料'}</button></div>
    {failed.length > 0 && <p role="status">{failed.length} 檔暫時無法讀取，可重試；缺值不補零。</p>}
    <div className="weekly-report-pair">{summary(a)}{summary(b)}</div>
    <div className="weekly-research-table-scroll" tabIndex={0}><table><thead><tr>{['股票', '族群', '估值資料日期', '收盤參考', '本益比', '近四季 EPS', '最新財季', '營益率', '營收月份', '營收年增'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{paged.rows.map(row => { const detail = loaded ? data[row.code] : null; const quarter = detail?.quarters?.at(-1); const revenue = detail?.revenues?.recent?.[0]; return <tr key={row.code}><th><button onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name}</button></th><td>{row.groupName}</td><td>{detail?.dataDate || '—'}</td><td>{num(detail?.currentPrice)}</td><td>{num(detail?.pe)}</td><td>{num(quarter?.ttmEps)}</td><td>{quarter?.label || '—'}</td><td>{num(quarter?.operatingMargin, '%')}</td><td>{revenue?.period || '—'}</td><td>{num(revenue?.yoyPct, '%')}</td></tr>; })}</tbody></table></div>
    <WeeklyPagination page={paged.page} pages={paged.pages} total={members.length} size={size} onPage={setPage} onSize={n => { setSize(n); setPage(1); }} />
  </section>;
}

export function WeeklyPagination({ page, pages, total, size, onPage, onSize }: { page: number; pages: number; total: number; size: number; onPage: (n: number) => void; onSize: (n: number) => void }) {
  return <nav className="weekly-pagination" aria-label="名單分頁"><label>每頁<select value={size} onChange={e => onSize(Number(e.target.value))}><option value={20}>20 檔</option><option value={50}>50 檔</option></select></label><span role="status">共 {total} 檔 · 第 {page}／{pages} 頁</span><button disabled={page <= 1} onClick={() => onPage(page - 1)}>上一頁</button><button disabled={page >= pages} onClick={() => onPage(page + 1)}>下一頁</button></nav>;
}
