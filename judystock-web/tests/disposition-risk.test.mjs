import assert from 'node:assert/strict';
import test from 'node:test';
import { GET } from '../app/api/disposition-risk/route.ts';

function mockSources(t, data = {}, failures = []) {
  t.mock.timers.enable({ apis: ['Date'], now: Date.parse('2026-09-08T12:00:00Z') });
  t.mock.method(console, 'warn', () => {});
  t.mock.method(globalThis, 'fetch', async (url) => {
    const name = String(url).split('/').at(-1);
    if (failures.includes(name)) return new Response('unavailable', { status: 503 });
    return Response.json(data[name] ?? []);
  });
}

test('first-clause list uses today in Taipei, excludes cumulative stocks and does not match clause eleven', async (t) => {
  mockSources(t, {
    notetrans: [{ Code: '2330', Name: '台積電', RecentlyMetAttentionSecuritiesCriteria: '連續二次' }],
    tpex_trading_warning_note: [{ SecuritiesCompanyCode: '4304', CompanyName: '勝昱', AccumulationSituation: '連續二次' }],
    notice: [
      { Date: '1150908', Code: '2330', Name: '台積電', TradingInfoForAttention: '價格異常(第一款)' },
      { Date: '1150908', Code: '2454', Name: '聯發科', TradingInfoForAttention: '價格異常(第一款)' },
      { Date: '', Code: '', Name: '', TradingInfoForAttention: '', NumberOfAnnouncement: '0' },
    ],
    tpex_trading_warning_information: [
      { Date: '1150908', SecuritiesCompanyCode: '4304', CompanyName: '勝昱', TradingInformation: '價格異常(第一款)' },
      { Date: '1150908', SecuritiesCompanyCode: '6187', CompanyName: '萬潤', TradingInformation: '價格異常(第一款)' },
      { Date: '1150908', SecuritiesCompanyCode: '6187', CompanyName: '萬潤', TradingInformation: '另一公告(第一款)' },
      { Date: '1150908', SecuritiesCompanyCode: '3081', CompanyName: '聯亞', TradingInformation: '價差異常(第十一款)' },
      { Date: '1150907', SecuritiesCompanyCode: '5347', CompanyName: '世界', TradingInformation: '價格異常(第一款)' },
      { Date: '1150908', SecuritiesCompanyCode: '736736', CompanyName: '權證', TradingInformation: '價格異常(第一款)' },
    ],
  });
  const data = await (await GET()).json();
  assert.equal(data.today, '2026-09-08');
  assert.deepEqual(data.firstClauseToday.map(row => row.code), ['2454', '6187']);
  assert.equal(data.suspects.find(row => row.code === '4304').detail, '連續二次');
  assert.deepEqual(data.warnings, []);
});

test('history retains multiple dispositions while existing list still keeps the latest announcement', async (t) => {
  mockSources(t, {
    tpex_disposal_information: [
      { Date: '1150908', SecuritiesCompanyCode: '6620', CompanyName: '漢達', DispositionPeriod: '1150909~1150915', DispositionReasons: '連續三次', DisposalCondition: '每兩分鐘撮合一次' },
      { Date: '1150813', SecuritiesCompanyCode: '6620', CompanyName: '漢達', DispositionPeriod: '1150814~1150820', DispositionReasons: '前次處置' },
    ],
    punish: [{ Date: '2026/09/08', Code: '2455', Name: '全新', DispositionPeriod: '2026/09/09～2026/09/15', ReasonsOfDisposition: '連續五次' }],
  });
  const data = await (await GET()).json();
  assert.equal(data.allDispositions.length, 3);
  assert.equal(data.dispositions.length, 2);
  const current = data.dispositions.find(row => row.code === '6620');
  assert.equal(current.announcedAt, '2026/09/08');
  assert.equal(current.period, '2026/09/09~2026/09/15');
  assert.equal(current.reason, '連續三次');
  assert.equal(current.measures, '每兩分鐘撮合一次');
  assert.equal(current.status, '即將處置');
  assert.equal(current.releaseDate, '2026/09/16');
  assert.equal(data.allDispositions.find(row => row.announcedAt === '2026/08/13').status, '已結束');
  assert.equal(data.dispositions.find(row => row.code === '2455').announcedAt, '2026/09/08');
  assert.equal(data.dispositions.find(row => row.code === '2455').period, '2026/09/09～2026/09/15');
});

test('failed cumulative source cannot falsely qualify a stock as first clause outside the cumulative list', async (t) => {
  mockSources(t, {
    tpex_trading_warning_information: [{ Date: '1150908', SecuritiesCompanyCode: '4304', CompanyName: '勝昱', TradingInformation: '(第一款)' }],
    notice: [{ Date: '1150908', Code: '2454', Name: '聯發科', TradingInfoForAttention: '(第一款)' }],
  }, ['tpex_trading_warning_note']);
  const data = await (await GET()).json();
  assert.deepEqual(data.firstClauseToday.map(row => row.code), ['2454']);
  assert.deepEqual(data.warnings, ['tpexRisk']);
  assert.equal(data.ok, true);
});

test('new notice-source failure preserves existing suspects and disposition history', async (t) => {
  mockSources(t, {
    notetrans: [{ Code: '2426', Name: '鼎元', RecentlyMetAttentionSecuritiesCriteria: '五次' }],
    punish: [{ Date: '1150908', Code: '2455', Name: '全新', DispositionPeriod: '115/09/09～115/09/15' }],
  }, ['notice', 'tpex_trading_warning_information']);
  const response = await GET();
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const data = await response.json();
  assert.equal(data.suspects.length, 1);
  assert.equal(data.dispositions.length, 1);
  assert.equal(data.allDispositions.length, 1);
  assert.deepEqual(data.firstClauseToday, []);
  assert.deepEqual(data.warnings, ['twseNotice', 'tpexNotice']);
});
