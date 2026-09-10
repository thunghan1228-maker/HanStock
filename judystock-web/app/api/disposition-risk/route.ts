type JsonRecord = Record<string, unknown>;

type RiskRow = {
  code: string;
  name: string;
  market: "上市" | "上櫃";
  detail: string;
  source: string;
};

type DispositionRow = RiskRow & {
  announcedAt: string;
  period: string;
  reason: string;
  measures: string;
  releaseDate: string;
  status: "即將處置" | "處置中" | "已結束" | "處置公告";
};

type NoticeRow = RiskRow & { announcedAt: string };

const headers = {
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
  "User-Agent": "HanStock-Battle/4.0",
};

const sources = {
  twseRisk: "https://openapi.twse.com.tw/v1/announcement/notetrans",
  tpexRisk: "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_note",
  twseDisposition: "https://openapi.twse.com.tw/v1/announcement/punish",
  tpexDisposition: "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information",
  twseNotice: "https://openapi.twse.com.tw/v1/announcement/notice",
  tpexNotice: "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information",
};

function clean(value: unknown) {
  return String(value ?? "")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function byKey(row: JsonRecord, patterns: RegExp[]) {
  const entry = Object.entries(row).find(([key]) => patterns.some((pattern) => pattern.test(key)));
  return clean(entry?.[1]);
}

function isStockCode(code: string) {
  return /^\d{4}[A-Z]?$/.test(code) || /^91\d{4}$/.test(code);
}

function rocToWestern(text: string) {
  return text.replace(/(?<!\d)(\d{2,4})[/.](\d{1,2})[/.](\d{1,2})(?!\d)/g, (_, year, month, day) =>
    `${Number(year) + (year.length < 4 ? 1911 : 0)}/${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}`,
  );
}

function compactDate(text: string) {
  if (/^\d{7}$/.test(text)) {
    return `${Number(text.slice(0, 3)) + 1911}/${text.slice(3, 5)}/${text.slice(5, 7)}`;
  }
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}/${text.slice(4, 6)}/${text.slice(6, 8)}`;
  return rocToWestern(text);
}

function dateKey(value: string) {
  const match = value.match(/(20\d{2})\/(\d{2})\/(\d{2})/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
}

function releaseDate(period: string) {
  const dates = [...period.matchAll(/20\d{2}\/\d{2}\/\d{2}/g)].map((match) => match[0]);
  const end = dates.at(-1);
  if (!end) return "";
  const date = new Date(`${end.replaceAll("/", "-")}T12:00:00+08:00`);
  if (Number.isNaN(date.getTime())) return "";
  do date.setDate(date.getDate() + 1); while (date.getDay() === 0 || date.getDay() === 6);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(date).replaceAll("-", "/");
}

function dispositionStatus(period: string): DispositionRow["status"] {
  const dates = [...period.matchAll(/20\d{2}\/\d{2}\/\d{2}/g)].map((match) => match[0].replaceAll("/", "-"));
  if (dates.length < 2) return "處置公告";
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  if (today < dates[0]) return "即將處置";
  if (today <= dates[1]) return "處置中";
  return "已結束";
}

async function fetchRows(url: string) {
  const response = await fetch(url, { headers, cache: "no-store", signal: AbortSignal.timeout(9000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const payload = await response.json() as unknown;
  if (!Array.isArray(payload)) throw new Error("資料格式不符");
  return payload.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object");
}

function normalizeRisk(rows: JsonRecord[], market: RiskRow["market"], source: string): RiskRow[] {
  return rows.flatMap((row) => {
    const code = byKey(row, [/^Code$/i, /SecuritiesCompanyCode/i, /SecurityCode/i, /證券代號/, /代號/]).toUpperCase();
    if (!isStockCode(code)) return [];
    const name = byKey(row, [/^Name$/i, /CompanyName/i, /SecurityName/i, /證券名稱/, /名稱/]);
    const detail = byKey(row, [/RecentlyMetAttention/i, /AccumulationSituation/i, /TradingWarning/i, /TradingInformation/i, /Attention/i, /累計次數/, /注意交易資訊/, /情形/]);
    return [{ code, name, market, detail: rocToWestern(detail) || "已列入官方注意累計異常名單", source }];
  });
}

function normalizeNotice(rows: JsonRecord[], market: RiskRow["market"], source: string): NoticeRow[] {
  return rows.flatMap((row) => {
    const code = byKey(row, [/^Code$/i, /SecuritiesCompanyCode/i, /SecurityCode/i, /證券代號/]).toUpperCase();
    if (!isStockCode(code)) return [];
    const name = byKey(row, [/^Name$/i, /CompanyName/i, /SecurityName/i, /證券名稱/]);
    const announcedAt = compactDate(byKey(row, [/^Date$/i, /Announce.*Date/i, /公布日期/, /公告日期/]));
    const detail = byKey(row, [/^TradingInfoForAttention$/i, /^TradingInformation$/i, /注意交易資訊/]);
    return [{ code, name, market, announcedAt, detail: rocToWestern(detail), source }];
  });
}

function normalizeDisposition(rows: JsonRecord[], market: RiskRow["market"], source: string): DispositionRow[] {
  return rows.flatMap((row) => {
    const code = byKey(row, [/^Code$/i, /SecuritiesCompanyCode/i, /SecurityCode/i, /證券代號/, /代號/]).toUpperCase();
    if (!isStockCode(code)) return [];
    const name = byKey(row, [/^Name$/i, /CompanyName/i, /SecurityName/i, /證券名稱/, /名稱/]);
    const announcedAt = compactDate(byKey(row, [/^Date$/i, /Announce.*Date/i, /公布日期/, /公告日期/]));
    const rawPeriod = byKey(row, [/DispositionPeriod/i, /處置起訖/, /處置期間/]);
    const period = rocToWestern(rawPeriod.replace(/(?<!\d)\d{7,8}(?!\d)/g, (date) => compactDate(date)));
    const reason = byKey(row, [/ReasonsOfDisposition/i, /DispositionReason/i, /處置原因/, /處置條件/]);
    const measures = byKey(row, [/DispositionMeasures/i, /處置措施/, /處置內容/]);
    const detail = byKey(row, [/^Detail$/i, /DisposalCondition/i, /Content/i, /處置內容/]);
    return [{
      code, name, market, source, announcedAt, period, reason,
      measures: measures || detail.slice(0, 160),
      detail: detail || reason,
      releaseDate: releaseDate(period),
      status: dispositionStatus(period),
    }];
  });
}

function uniqueByCode<T extends RiskRow>(rows: T[]) {
  const map = new Map<string, T>();
  rows.forEach((row) => {
    const key = `${row.market}-${row.code}`;
    const current = map.get(key) as (T & { announcedAt?: string }) | undefined;
    const candidate = row as T & { announcedAt?: string };
    if (!current || dateKey(candidate.announcedAt ?? "") >= dateKey(current.announcedAt ?? "")) map.set(key, row);
  });
  return [...map.values()];
}

export async function GET() {
  const attempts = await Promise.allSettled(Object.values(sources).map(fetchRows));

  const twseRisk = attempts[0].status === "fulfilled" ? normalizeRisk(attempts[0].value, "上市", "臺灣證券交易所") : [];
  const tpexRisk = attempts[1].status === "fulfilled" ? normalizeRisk(attempts[1].value, "上櫃", "證券櫃檯買賣中心") : [];
  const twseDisposition = attempts[2].status === "fulfilled" ? normalizeDisposition(attempts[2].value, "上市", "臺灣證券交易所") : [];
  const tpexDisposition = attempts[3].status === "fulfilled" ? normalizeDisposition(attempts[3].value, "上櫃", "證券櫃檯買賣中心") : [];
  const twseNotice = attempts[4].status === "fulfilled" ? normalizeNotice(attempts[4].value, "上市", "臺灣證券交易所") : [];
  const tpexNotice = attempts[5].status === "fulfilled" ? normalizeNotice(attempts[5].value, "上櫃", "證券櫃檯買賣中心") : [];
  const warnings = attempts.flatMap((attempt, index) => attempt.status === "rejected" ? [Object.keys(sources)[index]] : []);
  attempts.forEach((attempt, index) => {
    if (attempt.status === "rejected") console.warn(`[disposition-risk] ${Object.keys(sources)[index]} unavailable`, String(attempt.reason));
  });
  const suspects = uniqueByCode([...twseRisk, ...tpexRisk]).sort((a, b) => a.market.localeCompare(b.market, "zh-TW") || a.code.localeCompare(b.code));
  const allDispositions = [...twseDisposition, ...tpexDisposition]
    .sort((a, b) => dateKey(b.announcedAt).localeCompare(dateKey(a.announcedAt)) || a.code.localeCompare(b.code));
  const dispositions = uniqueByCode([...twseDisposition, ...tpexDisposition]).sort((a, b) => {
    const order = { "處置中": 0, "即將處置": 1, "處置公告": 2, "已結束": 3 } as const;
    return order[a.status] - order[b.status] || dateKey(b.announcedAt).localeCompare(dateKey(a.announcedAt));
  });
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const suspectKeys = new Set(suspects.map((row) => `${row.market}-${row.code}`));
  const firstClauseToday = uniqueByCode([...twseNotice, ...tpexNotice].filter((row) =>
    dateKey(row.announcedAt) === today
    && /第\s*(?:一|1|１)\s*款/.test(row.detail)
    // Exclusion cannot be verified when that market's cumulative source failed.
    && attempts[row.market === "上市" ? 0 : 1].status === "fulfilled"
    && !suspectKeys.has(`${row.market}-${row.code}`),
  )).sort((a, b) => a.code.localeCompare(b.code));

  return Response.json({
    ok: attempts.some((attempt) => attempt.status === "fulfilled"),
    updatedAt: new Date().toISOString(),
    suspects,
    dispositions,
    allDispositions,
    firstClauseToday,
    today,
    warnings,
    sourceUrls: Object.values(sources),
  }, { headers: { "Cache-Control": warnings.length ? "no-store" : "public, max-age=60, s-maxage=300" } });
}
