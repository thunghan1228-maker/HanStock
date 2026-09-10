export type ObservationStock = { ticker: string; name: string; note?: string };
export type ObservationGroup = { name: string; stocks: ObservationStock[] };
export type ManualObservationKind = "preopen-short" | "close-observation";
export type ManualObservationRecord = {
  kind: ManualObservationKind;
  tradeDate: string;
  title: string;
  rules: string[];
  releaseStocks: ObservationStock[];
  groups: ObservationGroup[];
  sourceText: string;
  sourceLabel: string;
  updatedAt: number;
};

/** All public views share the original lists; a saved edit wins for its kind/date. */
export function mergeManualObservationRecords(saved: ManualObservationRecord[]): ManualObservationRecord[] {
  const records = new Map<string, ManualObservationRecord>(
    [CLOSE_OBSERVATION_HOMEWORK, PREOPEN_SHORT_HOMEWORK].map(record => [`${record.kind}:${record.tradeDate}`, record]),
  );
  for (const record of [...saved].sort((a, b) => a.updatedAt - b.updatedAt)) {
    records.set(`${record.kind}:${record.tradeDate}`, record);
  }
  return [...records.values()].sort((a, b) => b.tradeDate.localeCompare(a.tradeDate) || b.updatedAt - a.updatedAt);
}

export function weeklyObservationSelection(records: ManualObservationRecord[], kind: ManualObservationKind, selectedWeek: string, requestedDate = '') {
  const available = records.filter(record => record.kind === kind).sort((a, b) => b.tradeDate.localeCompare(a.tradeDate));
  const dates = [...new Set(available.map(record => record.tradeDate))];
  const weekDate = selectedWeek.replaceAll('/', '-');
  const cutoff = new Date(`${weekDate}T00:00:00Z`);
  // A Friday report includes its weekend notes and Monday preopen list. These
  // retain their actual list dates and never participate in weekly score/history calculations.
  cutoff.setUTCDate(cutoff.getUTCDate() + 3);
  const endDate = Number.isFinite(cutoff.getTime()) ? cutoff.toISOString().slice(0, 10) : '';
  const date = dates.includes(requestedDate) ? requestedDate : dates.find(value => value <= endDate) ?? '';
  return { dates, date, record: available.find(record => record.tradeDate === date), afterWeek: Boolean(date && weekDate && date > weekDate) };
}

const GROUP_NAMES: Record<string, string> = {
  被動: "被動元件", 被動元件: "被動元件", 小電組: "小電組", 記憶體: "記憶體",
  鏡頭: "鏡頭", 矽光子: "矽光子", 散熱: "散熱", D電腦: "工業電腦", 工業電腦: "工業電腦",
  低軌: "低軌衛星", 低軌衛星: "低軌衛星", 矽晶圓: "矽晶圓", 航運: "航運", 四寶: "四寶",
  其他: "其他", 出獄股: "出獄股",
};

function cleanLine(value: string) {
  return value.replace(/^\s*(?:[*#•●○🔴🔵⚠️]+|\d+[.)、])\s*/u, "").replace(/\*\*/g, "").trim();
}

function validTradeDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function recordTitle(kind: ManualObservationKind, tradeDate: string) {
  const shortDate = `${Number(tradeDate.slice(5, 7))}/${Number(tradeDate.slice(8, 10))}`;
  return kind === "preopen-short" ? `${shortDate} 盤前空觀察` : `${shortDate} 收盤觀察股`;
}

function rewriteRule(kind: ManualObservationKind, value: string) {
  const line = value.trim();
  if (kind === "preopen-short") {
    if (/等\s*12.*反彈|均.*下彎/u.test(line)) return "12 空僅在反彈受壓、均線轉為下彎時列入；標的先從弱勢末段族群尋找。";
    if (/策略|905\s*低|損抓\s*2.?3/u.test(line)) return "執行可參考 09:05 首根低點，不必先等跌破昨日低；風險控制約 2～3%。";
    if (/開.*6%|第一根大紅/u.test(line)) return "跳空幅度超過 6%，或第一根出現明顯長紅時，暫不追空。";
    if (/族群空|刀劍|199\s*空方/u.test(line)) return "同為弱勢時，優先留意具刀劍標記者；位於 199 空方區者提高觀察順位。";
  } else {
    if (/小量基本單|905|1\+2|等黑/u.test(line)) return "先以小部位建立觀察單；隔日可參考 09:05 或 1+2 條件分批調整，急漲後等待轉黑再處理。";
    if (/停損|三\s*K\s*低|五日線/u.test(line)) return "風險防守以三 K 低點或五日均線作為參考。";
    if (/鏡頭|矽光子|被動|頸線突破/u.test(line)) return "鏡頭、矽光子與被動元件若快速拉升，先等轉黑；突破頸線時採分批方式布局。";
    if (/PCB|軍工|石英|打底/u.test(line)) return "PCB、軍工與石英相關標的跌幅較深，先確認底部成形，再逐步觀察。";
  }
  return `本站重點整理：${line.replace(/挑/gu, "優先選擇").replace(/不打/gu, "暫不納入").replace(/等黑/gu, "等待轉黑").replace(/慢慢來/gu, "分批觀察")}`;
}

export function parseManualObservationText(input: { kind: ManualObservationKind; tradeDate: string; text: string }): ManualObservationRecord {
  if (!validTradeDate(input.tradeDate)) throw new Error("請選擇正確資料日期");
  const sourceText = input.text.trim().slice(0, 60_000);
  if (!sourceText) throw new Error("請先貼上 HiStock 文章內容");
  const knownNames = new Map<string, string>();
  for (const source of [PREOPEN_SHORT_HOMEWORK, CLOSE_OBSERVATION_HOMEWORK]) {
    for (const stock of source.releaseStocks ?? []) knownNames.set(stock.ticker, stock.name);
    for (const group of source.groups) for (const stock of group.stocks) knownNames.set(stock.ticker, stock.name);
  }
  const rules: string[] = [];
  const releaseStocks: ObservationStock[] = [];
  const groups: ObservationGroup[] = [];
  let currentGroup = "";
  const ensureGroup = (name: string) => {
    const normalized = GROUP_NAMES[name.replace(/[：:]$/u, "").trim()] ?? name.replace(/[：:]$/u, "").trim();
    if (normalized === "出獄股") { currentGroup = normalized; return; }
    if (!groups.some((group) => group.name === normalized)) groups.push({ name: normalized, stocks: [] });
    currentGroup = normalized;
  };
  for (const raw of sourceText.split(/\r?\n/u)) {
    const line = cleanLine(raw);
    if (!line || /^(主題|回應|瀏覽|作者|條件篩選)$/u.test(line)) continue;
    if (/^\d{1,2}[/-]\d{1,2}\s*(?:盤前空觀察|收盤觀察股|盤後觀察)/u.test(line)) continue;
    const stockMatch = line.match(/^(\d{4})\s*([^（(]*?)(?:[（(]([^）)]+)[）)])?\s*$/u);
    if (stockMatch) {
      const ticker = stockMatch[1];
      const typedName = stockMatch[2].trim().replace(/^[-－:：]+|[-－:：]+$/gu, "");
      const stock: ObservationStock = { ticker, name: typedName || knownNames.get(ticker) || ticker };
      if (stockMatch[3]?.trim()) stock.note = stockMatch[3].trim();
      if (currentGroup === "出獄股") {
        if (!releaseStocks.some((item) => item.ticker === ticker)) releaseStocks.push(stock);
      } else {
        if (!currentGroup) ensureGroup("其他");
        const group = groups.find((item) => item.name === currentGroup)!;
        if (!group.stocks.some((item) => item.ticker === ticker)) group.stocks.push(stock);
      }
      continue;
    }
    const candidate = line.replace(/[：:]$/u, "").trim();
    const isKnownGroup = Boolean(GROUP_NAMES[candidate]);
    const looksLikeGroup = candidate.length <= 10 && !/[，。；！？%、／/]/u.test(candidate) && !/^(盤前|盤後|收盤|策略|等|族群請|小量|停損|頸線)/u.test(candidate);
    if (isKnownGroup || looksLikeGroup && (groups.length > 0 || /股$|體$|子$|組$|圓$|熱$|航運|四寶/u.test(candidate))) {
      ensureGroup(candidate);
      continue;
    }
    if (!/^\d{4}[/-]\d{1,2}[/-]\d{1,2}/u.test(candidate) && !/密碼更新|新手導讀|學院文章|置頂/u.test(candidate)) {
      const rewritten = rewriteRule(input.kind, candidate);
      if (!rules.includes(rewritten)) rules.push(rewritten);
    }
  }
  const stockCount = releaseStocks.length + groups.reduce((total, group) => total + group.stocks.length, 0);
  if (!stockCount) throw new Error("沒有辨識到股票代號，請確認貼入的是文章正文");
  return {
    kind: input.kind,
    tradeDate: input.tradeDate,
    title: recordTitle(input.kind, input.tradeDate),
    rules: rules.slice(0, 30),
    releaseStocks,
    groups: groups.filter((group) => group.stocks.length > 0).slice(0, 30),
    sourceText,
    sourceLabel: "HiStock 學院文章・手動貼入",
    updatedAt: Date.now(),
  };
}

export const PREOPEN_SHORT_HOMEWORK = {
  kind: "preopen-short" as const,
  tradeDate: "2026-09-07",
  title: "9/7 盤前空觀察",
  rules: [
    "12 空僅在反彈受壓、均線轉為下彎時列入；標的先從弱勢末段族群尋找。",
    "執行可參考 09:05 首根低點，不必先等跌破昨日低；風險控制約 2～3%。",
    "跳空幅度超過 6%，或第一根出現明顯長紅時，暫不追空。",
    "同為弱勢時，優先留意具刀劍標記者；位於 199 空方區者提高觀察順位。",
  ],
  releaseStocks: [
    { ticker: "2455", name: "全新", note: "8/31" }, { ticker: "3498", name: "陽程", note: "8/31" },
    { ticker: "5321", name: "美而快", note: "8/31" }, { ticker: "4971", name: "IET-KY", note: "9/1" },
    { ticker: "3234", name: "光環", note: "9/2" }, { ticker: "2486", name: "一詮", note: "9/3" },
    { ticker: "4979", name: "華星光", note: "9/4" }, { ticker: "3163", name: "波若威", note: "9/7" },
    { ticker: "3362", name: "先進光", note: "9/7" }, { ticker: "3441", name: "聯一光", note: "9/7" },
  ],
  groups: [
    { name: "鏡頭", stocks: [{ ticker: "3441", name: "聯一光" }, { ticker: "3362", name: "先進光" }, { ticker: "6278", name: "台表科" }, { ticker: "3504", name: "揚明光" }] },
    { name: "矽光子", stocks: [{ ticker: "3163", name: "波若威" }, { ticker: "4979", name: "華星光" }, { ticker: "4908", name: "前鼎" }, { ticker: "8111", name: "立碁" }, { ticker: "3081", name: "聯亞" }, { ticker: "3363", name: "上詮" }, { ticker: "3450", name: "聯鈞" }, { ticker: "4971", name: "IET-KY" }, { ticker: "6830", name: "汎銓" }, { ticker: "6530", name: "創威" }, { ticker: "6715", name: "嘉基" }, { ticker: "6451", name: "訊芯-KY" }] },
    { name: "散熱", stocks: [{ ticker: "8996", name: "高力" }, { ticker: "3017", name: "奇鋐" }, { ticker: "3653", name: "健策" }] },
    { name: "被動元件", stocks: [{ ticker: "2327", name: "國巨" }, { ticker: "8042", name: "金山電" }, { ticker: "2492", name: "華新科" }, { ticker: "3624", name: "光頡" }, { ticker: "3026", name: "禾伸堂" }, { ticker: "6449", name: "鈺邦" }, { ticker: "2478", name: "大毅" }, { ticker: "2472", name: "立隆電" }, { ticker: "3090", name: "日電貿" }, { ticker: "6173", name: "信昌電" }, { ticker: "6834", name: "天二科技" }] },
    { name: "小電組", stocks: [{ ticker: "3167", name: "大量" }, { ticker: "5475", name: "德宏" }, { ticker: "6213", name: "聯茂" }] },
    { name: "記憶體", stocks: [{ ticker: "2408", name: "南亞科" }, { ticker: "2344", name: "華邦電" }, { ticker: "2337", name: "旺宏" }, { ticker: "6770", name: "力積電" }, { ticker: "3006", name: "晶豪科" }, { ticker: "5351", name: "鈺創" }] },
    { name: "工業電腦", stocks: [{ ticker: "3022", name: "威強電" }, { ticker: "6166", name: "凌華" }, { ticker: "2395", name: "研華" }] },
    { name: "低軌衛星", stocks: [{ ticker: "6271", name: "同欣電" }, { ticker: "2413", name: "環科" }, { ticker: "7717", name: "萊德光電" }, { ticker: "2313", name: "華通" }] },
  ] satisfies ObservationGroup[],
  sourceText: "",
  sourceLabel: "初始人工整理",
  updatedAt: 0,
};

export const CLOSE_OBSERVATION_HOMEWORK = {
  kind: "close-observation" as const,
  tradeDate: "2026-09-06",
  title: "9/6 收盤觀察股",
  rules: [
    "先以小部位建立觀察單；隔日可參考 09:05 或 1+2 條件分批調整，急漲後等待轉黑再處理。",
    "風險防守以三 K 低點或五日均線作為參考。",
    "鏡頭、矽光子與被動元件若快速拉升，先等轉黑；突破頸線時採分批方式布局。",
    "PCB、軍工與石英相關標的跌幅較深，先確認底部成形，再逐步觀察。",
  ],
  releaseStocks: [],
  groups: [
    { name: "被動元件", stocks: [{ ticker: "2492", name: "華新科" }, { ticker: "6173", name: "信昌電" }, { ticker: "2327", name: "國巨" }, { ticker: "3090", name: "日電貿" }, { ticker: "3026", name: "禾伸堂" }, { ticker: "2478", name: "大毅" }, { ticker: "3624", name: "光頡" }, { ticker: "6834", name: "天二科技" }] },
    { name: "矽晶圓", stocks: [{ ticker: "3532", name: "台勝科" }, { ticker: "6182", name: "合晶" }, { ticker: "6488", name: "環球晶" }, { ticker: "3016", name: "嘉晶" }, { ticker: "2342", name: "茂矽" }, { ticker: "5483", name: "中美晶" }] },
    { name: "矽光子", stocks: [{ ticker: "4991", name: "環宇-KY" }, { ticker: "4979", name: "華星光" }, { ticker: "3163", name: "波若威" }, { ticker: "6451", name: "訊芯-KY" }, { ticker: "3450", name: "聯鈞" }, { ticker: "3081", name: "聯亞" }, { ticker: "4908", name: "前鼎" }, { ticker: "8111", name: "立碁" }] },
    { name: "鏡頭", stocks: [{ ticker: "3008", name: "大立光" }, { ticker: "3441", name: "聯一光" }, { ticker: "3362", name: "先進光" }, { ticker: "3406", name: "玉晶光" }, { ticker: "4976", name: "佳凌" }, { ticker: "3504", name: "揚明光" }] },
    { name: "記憶體", stocks: [{ ticker: "2344", name: "華邦電" }, { ticker: "2408", name: "南亞科" }, { ticker: "5351", name: "鈺創" }, { ticker: "3006", name: "晶豪科" }, { ticker: "6265", name: "方土昶" }, { ticker: "6770", name: "力積電" }, { ticker: "8271", name: "宇瞻" }, { ticker: "2451", name: "創見" }, { ticker: "3260", name: "威剛" }] },
    { name: "航運", stocks: [{ ticker: "2615", name: "萬海" }, { ticker: "2606", name: "裕民" }, { ticker: "2637", name: "慧洋-KY" }, { ticker: "2603", name: "長榮" }] },
    { name: "四寶", stocks: [{ ticker: "6505", name: "台塑化" }, { ticker: "1303", name: "南亞" }, { ticker: "1326", name: "台化" }, { ticker: "1301", name: "台塑" }] },
    { name: "其他", stocks: [{ ticker: "3374", name: "精材" }, { ticker: "8039", name: "台虹" }, { ticker: "2301", name: "光寶科" }, { ticker: "4939", name: "亞電" }, { ticker: "6547", name: "高端疫苗" }, { ticker: "6426", name: "統新" }, { ticker: "2426", name: "鼎元" }, { ticker: "7750", name: "新代" }, { ticker: "2464", name: "盟立" }, { ticker: "1303", name: "南亞" }, { ticker: "3455", name: "由田" }, { ticker: "4931", name: "新盛力" }, { ticker: "7788", name: "松川精密" }, { ticker: "2465", name: "麗臺" }, { ticker: "2454", name: "聯發科" }, { ticker: "3231", name: "緯創" }] },
  ] satisfies ObservationGroup[],
  sourceText: "",
  sourceLabel: "初始人工整理",
  updatedAt: 0,
};
