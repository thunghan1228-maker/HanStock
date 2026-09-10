"use client";
import { useState } from 'react';
import { weeklyReportGroups, weeklyPage, weeklyScreenerUrl, type ResearchRow, type WeeklyEntryKind } from '../lib/weekly-chip-insights';
import { WeeklyPagination } from './WeeklyReportSections';

const labels: Record<WeeklyEntryKind, string> = { resonant: '同族多檔上榜', single: '前十族群僅一檔', outside: '族群未入前十', unclassified: '未分類', pending: '族群待比較資料' };
const signed = (n: number | null | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(1)}`;
export default function WeeklyReportOverview({ rows, date, limit, view, onOpenStock }: { rows: ResearchRow[]; date: string; limit: number; view: 'overview' | 'groups' | 'solo'; onOpenStock: (code: string, name: string) => void }) {
  const report = weeklyReportGroups(rows, limit);
  const [kind, setKind] = useState<WeeklyEntryKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [groupFilter, setGroupFilter] = useState('');
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  const [sort, setSort] = useState('rank');
  const entries = report.entries.filter(entry => (view !== 'solo' || entry.kind !== 'resonant') && (kind === 'all' || entry.kind === kind) && (!groupFilter || entry.row.groupName === groupFilter) && `${entry.row.code} ${entry.row.name}`.toLowerCase().includes(query.trim().toLowerCase()));
  entries.sort((a, b) => sort === 'delta' ? (b.row.points[0].delta ?? -Infinity) - (a.row.points[0].delta ?? -Infinity) || a.row.code.localeCompare(b.row.code) : a.row.points[0].rank! - b.row.points[0].rank!);
  const paged = weeklyPage(entries, page, size);
  const groups = view === 'overview' ? report.groups.filter(group => group.rank !== null && group.rank <= 10) : report.groups;
  return <section>
    <h3>{view === 'overview' ? '本週榜單一覽' : view === 'groups' ? '族群共振與上榜成員' : '單獨上榜與其他分類'}</h3>
    <p>個股採法人正分前 {limit} 名；族群依本區成員平均週增減（分）排序。前十族群有兩檔以上入榜，才標示同族多檔上榜。</p>
    <div className="weekly-report-counts">{(Object.keys(labels) as WeeklyEntryKind[]).map(key => <button key={key} aria-pressed={kind === key} onClick={() => { setKind(kind === key ? 'all' : key); setPage(1); }}><span>{labels[key]}</span><strong>{report.entries.filter(entry => entry.kind === key).length}</strong></button>)}</div>
    {view !== 'solo' && <div className="weekly-research-table-scroll" tabIndex={0} role="region" aria-label="族群週榜"><table><thead><tr><th>族群排名</th><th>族群</th><th>平均週增減</th><th>升分／可比較</th><th>入榜／本區涵蓋</th><th>本週上榜個股</th></tr></thead><tbody>{groups.map(group => <tr key={group.name} className={group.rank !== null && group.rank <= 10 && group.entries.length >= 2 ? 'weekly-report-resonant' : ''}><td>{group.rank ?? '待資料'}</td><th><button aria-pressed={groupFilter === group.name} onClick={() => { setGroupFilter(groupFilter === group.name ? '' : group.name); setPage(1); }}>{group.name}</button></th><td>{signed(group.delta)} 分</td><td>{group.rising}／{group.valid}</td><td>{group.entries.length}／{group.members.length}</td><td><div className="weekly-report-stocks">{group.entries.length ? group.entries.map(row => <button key={row.code} onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name}</button>) : '本週無個股上榜'}</div></td></tr>)}</tbody></table>{!groups.length && <p>尚無可比較的族群週資料。</p>}</div>}
    <header className="weekly-research-heading"><h3>{view === 'solo' ? '分類名單' : '上榜個股'}</h3><a aria-disabled={!paged.rows.length} href={paged.rows.length ? weeklyScreenerUrl(paged.rows.map(entry => entry.row.code), date, `${view === 'solo' ? '單獨上榜' : '本週上榜'}・第${paged.page}頁`) : undefined}>本頁帶入選股（{paged.rows.length} 檔） →</a></header>
    <div className="weekly-research-controls"><label>分類<select value={kind} onChange={e => { setKind(e.target.value as WeeklyEntryKind | 'all'); setPage(1); }}><option value="all">全部</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>族群<select value={groupFilter} onChange={e => { setGroupFilter(e.target.value); setPage(1); }}><option value="">全部族群</option>{[...new Set(report.entries.map(entry => entry.row.groupName))].map(name => <option key={name}>{name}</option>)}</select></label><label>股票搜尋<input value={query} onChange={e => { setQuery(e.target.value); setPage(1); }} placeholder="代號或名稱" /></label><label>排序<select value={sort} onChange={e => { setSort(e.target.value); setPage(1); }}><option value="rank">本週名次</option><option value="delta">週增減由高至低</option></select></label></div>
    <div className="weekly-research-table-scroll" tabIndex={0}><table><thead><tr>{['名次', '股票', '族群', '族群名次', '本週分數', '週增減', '分類'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{paged.rows.map(({ row, kind, groupRank }) => <tr key={row.code}><td>{row.points[0].rank}</td><th><button onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name}</button></th><td>{row.groupName || '未分類'}</td><td>{groupRank ?? '—'}</td><td>{signed(row.points[0].score)}</td><td>{signed(row.points[0].delta)}</td><td>{labels[kind]}</td></tr>)}</tbody></table>{!entries.length && <p>目前沒有符合條件的股票。</p>}</div>
    <WeeklyPagination page={paged.page} pages={paged.pages} total={entries.length} size={size} onPage={setPage} onSize={n => { setSize(n); setPage(1); }} />
    <p>單獨上榜表示同族上榜佐證較少；未分類與缺資料另列。此分類不等於買賣建議。</p>
  </section>;
}
