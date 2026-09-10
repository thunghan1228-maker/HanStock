"use client";

import { useEffect, useMemo, useState } from 'react';
import { buildWeeklyInsights, normalizeWeek, weeklyPage, weeklyGroupBreadth, weeklyScreenerUrl, type ResearchArchive, type ResearchRow } from '../lib/weekly-chip-insights';

import WeeklyReportOverview from './WeeklyReportOverview';
import { WeeklyFundamentalComparison, WeeklyPagination } from './WeeklyReportSections';
const tabs = [['overview', '本週總覽'], ['groups', '族群共振'], ['solo', '單獨上榜'], ['fundamental', '基本面比較'], ['history', '歷史追蹤']] as const;
type ReportTab = typeof tabs[number][0];

type Filter = 'all' | 'newEntry' | 'dropped' | 'turnedPositive' | 'turnedNegative' | 'threeRising' | 'fiveOfSix';
const filters: Array<[Filter, string]> = [['all', '全部'], ['newEntry', '新進榜'], ['dropped', '掉出榜'], ['turnedPositive', '負轉正'], ['turnedNegative', '正轉負'], ['threeRising', '連三週升分'], ['fiveOfSix', '六週內五週升分']];
const signed = (value: number | null) => value === null ? '待資料' : `${value > 0 ? '+' : ''}${value.toFixed(1)}`;
const tone = (value: number | null) => value === null || value === 0 ? 'neutral' : value > 0 ? 'positive' : 'negative';

export default function WeeklyChipResearch({ archives, loading, error, onRetry, onOpenStock, onTabChange }: {
  onTabChange?: (tab: string) => void; archives: ResearchArchive[]; loading: boolean; error: boolean; onRetry: () => void; onOpenStock: (code: string, name: string) => void;
}) {
  const [requestedDate, setRequestedDate] = useState('');
  const [window, setWindow] = useState(12);
  const [topLimit, setTopLimit] = useState(20);
  const [tab, setTab] = useState<ReportTab>('overview');
  const [page, setPage] = useState(1);
  const chooseTab = (value: ReportTab) => { setTab(value); onTabChange?.(value); };
  const [limit, setLimit] = useState(20);
  const [filter, setFilter] = useState<Filter>('all');
  const [group, setGroup] = useState('');
  const [query, setQuery] = useState('');
  const dates = useMemo(() => [...new Set(archives.map(a => normalizeWeek(a.weekEndDate)))].sort().reverse(), [archives]);
  const selectedDate = dates.includes(requestedDate) ? requestedDate : dates[0] ?? '';
  const rows = useMemo(() => selectedDate ? buildWeeklyInsights(archives, selectedDate, window, topLimit) : [], [archives, selectedDate, window, topLimit]);
  const trajectories = useMemo(() => new Map((selectedDate ? buildWeeklyInsights(archives, selectedDate, 9, topLimit) : []).map(row => [row.code, row])), [archives, selectedDate, topLimit]);
  const breadth = useMemo(() => weeklyGroupBreadth(rows), [rows]);
  const selectedGroup = breadth.find(g => g.name === group);
  const filtered = rows.filter(row => (filter === 'all' || row[filter]) && (!selectedGroup || row.groupName === selectedGroup.name) && (!query.trim() || `${row.code} ${row.name}`.toLowerCase().includes(query.trim().toLowerCase())));
  const paged = weeklyPage(filtered, page, limit);
  const visible = paged.rows;
  useEffect(() => setPage(1), [selectedDate, filter, group, query, limit, window, topLimit]);
  const coverage = rows.filter(row => row.points[0]?.delta !== null).length;
  const selectionLabel = `${filters.find(([key]) => key === filter)?.[1]}${selectedGroup ? `・${selectedGroup.name}` : ''}・第${paged.page}頁`;
  const averageDelta = (row: ResearchRow, count: number) => (trajectories.get(row.code)?.points ?? []).slice(0, count).reduce((sum, point) => sum + (point.delta ?? 0), 0) / count;
  const streakRows = (key: 'threeRising' | 'fiveOfSix') => rows.filter(row => row[key]).sort((a, b) => averageDelta(b, key === 'threeRising' ? 3 : 6) - averageDelta(a, key === 'threeRising' ? 3 : 6) || a.code.localeCompare(b.code)).slice(0, 20);

  return <section className="weekly-research" aria-label="每週籌碼研究與選股">
    <header className="weekly-research-heading"><div><span className="eyebrow">WEEKLY RESEARCH</span><h2>每週籌碼研究報告</h2><p>沿用法人週分數；升分是本週減上週的分數差，單位為「分」。</p></div><label>研究週次<select value={selectedDate} disabled={!dates.length} onChange={e => setRequestedDate(e.target.value)}>{dates.length ? dates.map(date => <option key={date}>{date}</option>) : <option value="">等待週資料</option>}</select></label></header>
    {error && <p role="alert">歷史週資料暫時無法讀取，目前僅顯示已取得的週次。<button type="button" onClick={onRetry}>重試歷史資料</button></p>}
    {loading && <p role="status">正在讀取歷史週次，已取得的名單保持顯示…</p>}
    <nav className="weekly-report-tabs" aria-label="每週報告主題">{tabs.map(([key, label]) => <button type="button" key={key} aria-pressed={tab === key} onClick={() => chooseTab(key)}>{label}</button>)}</nav>
    <div className="weekly-research-controls"><label>上榜標準<select value={topLimit} onChange={e => setTopLimit(Number(e.target.value))}>{[10, 20].map(n => <option key={n} value={n}>法人正分前 {n} 名</option>)}</select></label><span>研究週次 {selectedDate || '待資料'} · 週增減單位：分</span></div>
    <div hidden={tab !== 'overview'}><WeeklyReportOverview key={selectedDate + topLimit + 'overview'} rows={rows} date={selectedDate} limit={topLimit} view="overview" onOpenStock={onOpenStock} /></div>
    <div hidden={tab !== 'groups'}><WeeklyReportOverview key={selectedDate + topLimit + 'groups'} rows={rows} date={selectedDate} limit={topLimit} view="groups" onOpenStock={onOpenStock} /></div>
    <div hidden={tab !== 'solo'}><WeeklyReportOverview key={selectedDate + topLimit + 'solo'} rows={rows} date={selectedDate} limit={topLimit} view="solo" onOpenStock={onOpenStock} /></div>
    {tab === 'fundamental' && <WeeklyFundamentalComparison rows={rows} selectedDate={selectedDate} onOpenStock={onOpenStock} />}
    <div hidden={tab !== 'history'}>
    <div className="weekly-research-summary">{filters.slice(1, 5).map(([key, label]) => <button type="button" key={key} aria-pressed={filter === key} onClick={() => { setFilter(key); setGroup(''); }}><span>{label}{key === 'newEntry' || key === 'dropped' ? `・前${topLimit}` : ''}</span><strong>{coverage ? rows.filter(row => row[key as Exclude<Filter, 'all'>]).length : '—'}</strong><small>{coverage ? `${coverage} 檔有相鄰兩週資料` : '等待相鄰兩週同權重資料'}</small></button>)}</div>
    <details className="weekly-research-method"><summary>資料口徑與缺值怎麼處理</summary><p>本區使用 HanStock 法人週分數，與集保大戶持股百分比不同。新進／掉出以所選前10或20名為界；負轉正與正轉負不包含0分。連三週升分需要4個相鄰週分數，六週內五週升分需要7週；缺週、缺股或權重不一致不補0。回看週次只使用該週以前的存檔。累積分數是描述統計，包含負分週，並非報酬率。</p><p>近四週欄位固定回看4週；族群同步僅統計本區有資料的主族群成員，不代表全市場覆蓋。點個股會開啟原有行情分析；帶到選股後使用最新行情，並非歷史回測。</p></details>
    <div className="weekly-research-streaks">{(['threeRising', 'fiveOfSix'] as const).map(key => {
      const items = streakRows(key);
      const label = key === 'threeRising' ? '連三週升分' : '六週內五週升分';
      return <article key={key}><header><h3>{label} 前20</h3><button type="button" onClick={() => { setFilter(key); setGroup(''); }}>篩選此條件</button></header><p>依{key === 'threeRising' ? '三' : '六'}週平均分數增量排序</p>{items.length ? <ol>{items.map(row => <li key={row.code}><button type="button" onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name}</button><strong className="positive">{signed(averageDelta(row, key === 'threeRising' ? 3 : 6))} 分／週</strong></li>)}</ol> : <p className="weekly-research-empty">無符合個股，或相鄰週資料尚不足{key === 'threeRising' ? '4' : '7'}週。</p>}</article>;
    })}</div>
    <section aria-label="族群同步程度"><header className="weekly-research-heading"><div><h3>族群同步升分</h3><p>點族群，篩選下方個股。分母只計入相鄰兩週同權重且有效的成員。</p></div>{group && <button type="button" onClick={() => setGroup('')}>清除族群</button>}</header><div className="weekly-research-groups">{breadth.slice(0, 10).map(item => <button type="button" aria-pressed={group === item.name} key={item.name} onClick={() => setGroup(group === item.name ? '' : item.name)}><b>{item.name}</b><span>{item.rising}／{item.valid} 檔升分</span><strong>{item.ratio === null ? '待資料' : `${Math.round(item.ratio * 100)}%`}</strong><small>本區涵蓋 {item.members.length} 檔</small></button>)}</div></section>
    <section aria-label="累積上榜與選股名單"><header className="weekly-research-heading"><div><h3>累積上榜與選股名單</h3><p>依上榜次數、全部有效週平均分數排序。負分週一起列入。</p></div><a aria-disabled={!visible.length} href={visible.length ? weeklyScreenerUrl(visible.map(row => row.code), selectedDate, selectionLabel) : undefined}>本頁帶入選股（{visible.length} 檔） →</a></header>
      <div className="weekly-research-controls"><label>回看<select value={window} onChange={e => setWindow(Number(e.target.value))}>{[4, 6, 12].map(n => <option value={n} key={n}>{n} 週</option>)}</select></label><label>上榜標準<select value={topLimit} onChange={e => setTopLimit(Number(e.target.value))}>{[10, 20].map(n => <option key={n} value={n}>正分前 {n} 名</option>)}</select></label><label>股票搜尋<input value={query} onChange={e => setQuery(e.target.value)} placeholder="代號或名稱" /></label></div>
      <div className="weekly-research-filters" aria-label="每週籌碼條件">{filters.map(([key, label]) => <button type="button" aria-pressed={filter === key} key={key} onClick={() => setFilter(key)}>{label}</button>)}</div>
      <p role="status">{selectedDate || '待資料'}｜{selectedGroup?.name ?? '全部族群'}｜符合 {filtered.length} 檔，本頁 {visible.length} 檔</p>
      <div className="weekly-research-table-scroll" tabIndex={0} role="region" aria-label="累積上榜表格，可水平捲動"><table><thead><tr>{['股票', '本週分數', '週增減', '名次變化', '上榜／有效週', '近四週上榜', '全部週平均', '正分合計', '負分合計', '族群'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{visible.map(row => {
        const now = row.points[0], old = row.points[1];
        const movement = now?.score === null || old?.score == null ? '待資料' : row.newEntry ? '新進' : row.dropped ? '掉出' : now?.rank && now.rank <= topLimit && old?.rank && old.rank <= topLimit ? `${old.rank - now.rank > 0 ? '↑' : old.rank - now.rank < 0 ? '↓' : '＝'}${Math.abs(old.rank - now.rank)}` : '未入榜';
        return <tr key={row.code}><th scope="row"><button type="button" onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name} ›</button></th><td className={tone(now?.score ?? null)}>{signed(now?.score ?? null)}</td><td className={tone(now?.delta ?? null)}>{signed(now?.delta ?? null)}</td><td>{movement}</td><td>{row.appearances}／{row.available}<small>窗口 {window} 週</small></td><td>{row.recentAppearances}／4</td><td>{signed(row.average)}</td><td className="positive">{signed(row.positiveTotal)}</td><td className="negative">{signed(row.negativeTotal)}</td><td>{row.groupName}</td></tr>;
      })}</tbody></table></div>{!visible.length && <p className="weekly-research-empty">{loading ? '正在整理資料…' : '沒有符合條件的股票；可調整週次、條件或搜尋。'}</p>}
    </section>
    <WeeklyPagination page={paged.page} pages={paged.pages} total={filtered.length} size={limit} onPage={setPage} onSize={setLimit} />
    <section aria-label="熱門股九週籌碼軌跡"><header className="weekly-research-heading"><div><h3>個股九週軌跡</h3><p>跟隨上方名單前6檔；固定 -100～+100 分刻度，紅色正分、綠色負分。</p></div></header><div className="weekly-research-trends">{visible.slice(0, 6).map(row => {
      const trajectory = trajectories.get(row.code);
      return <article key={row.code}><header><button type="button" onClick={() => onOpenStock(row.code, row.name)}>{row.code} {row.name} ›</button><strong className={tone(row.points[0]?.score ?? null)}>{signed(row.points[0]?.score ?? null)} 分</strong></header>{trajectory?.points.map(point => <div className="weekly-research-bar-row" key={point.date}><time>{point.date.slice(5)}</time><div className="weekly-research-bar" aria-hidden="true">{point.score !== null && <i style={{ left: `${point.score >= 0 ? 50 : 50 - Math.min(Math.abs(point.score), 100) / 2}%`, width: `${Math.min(Math.abs(point.score), 100) / 2}%`, background: point.score >= 0 ? 'var(--hot)' : 'var(--good)' }} />}</div><span className={tone(point.score)}>{signed(point.score)}</span><small>{point.rank && point.rank <= topLimit ? `#${point.rank}` : '—'}</small></div>)}</article>;
    })}</div></section>
    </div>
  </section>;
}
