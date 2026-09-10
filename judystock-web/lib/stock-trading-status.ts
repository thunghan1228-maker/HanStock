export type Availability = "available" | "unavailable" | "unknown";

export type StockTradingStatus = {
  code: string;
  margin: Availability;
  short: Availability;
  dayTrade: Availability;
  disposition: "處置中" | "即將處置" | "處置公告" | null;
  stockFuture: boolean;
  miniStockFuture: boolean;
  futuresReady?: boolean;
  dispositionReady?: boolean;
  note: string;
};

type JsonRow = Record<string, unknown>;

function clean(value: unknown) {
  return String(value ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim();
}

function numeric(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function statusFromMarginRow(row: JsonRow, market: "twse" | "tpex") {
  const code = clean(market === "twse" ? row["股票代號"] : row.SecuritiesCompanyCode).toUpperCase();
  const marginQuota = numeric(market === "twse" ? row["融資限額"] : row.MarginPurchaseQuota);
  const shortQuota = numeric(market === "twse" ? row["融券限額"] : row.ShortSaleQuota);
  const note = clean(market === "twse" ? row["註記"] : row.Note);
  // Official margin tables use O = stopped margin financing and X = stopped
  // short selling. Keep the wording fallback for any future verbose feed.
  const marginSuspended = note.includes("O") || /暫停.*融資|停止.*融資|不得.*融資/.test(note);
  const shortSuspended = note.includes("X") || /暫停.*融券|停止.*融券|停止.*券|不得.*融券/.test(note);
  if (!/^[0-9A-Z]{4,8}$/.test(code)) return null;
  return {
    code,
    margin: marginQuota !== null && marginQuota > 0 && !marginSuspended ? "available" : "unavailable",
    short: shortQuota !== null && shortQuota > 0 && !shortSuspended ? "available" : "unavailable",
    note,
  } as const;
}

export function parseTwseMarginRows(payload: unknown) {
  if (!Array.isArray(payload)) return new Map<string, Pick<StockTradingStatus, "margin" | "short" | "note">>();
  return new Map(payload.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const row = statusFromMarginRow(raw as JsonRow, "twse");
    return row ? [[row.code, { margin: row.margin, short: row.short, note: row.note }] as const] : [];
  }));
}

export function parseTpexMarginRows(payload: unknown) {
  if (!Array.isArray(payload)) return new Map<string, Pick<StockTradingStatus, "margin" | "short" | "note">>();
  return new Map(payload.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const row = statusFromMarginRow(raw as JsonRow, "tpex");
    return row ? [[row.code, { margin: row.margin, short: row.short, note: row.note }] as const] : [];
  }));
}

function splitCsvRow(row: string) {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < row.length; index += 1) {
    const character = row[index];
    if (character === '"') {
      if (quoted && row[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else quoted = !quoted;
    } else if (character === "," && !quoted) {
      cells.push(cell.trim());
      cell = "";
    } else cell += character;
  }
  cells.push(cell.trim());
  return cells;
}

export function parseTpexMarginCsv(payload: string) {
  const result = new Map<string, Pick<StockTradingStatus, "margin" | "short" | "note">>();
  for (const line of payload.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const cells = splitCsvRow(line);
    if (!/^[0-9A-Z]{4,8}$/.test(clean(cells[0]).toUpperCase()) || cells.length < 18) continue;
    const row = statusFromMarginRow({
      SecuritiesCompanyCode: cells[0],
      MarginPurchaseQuota: cells[9],
      ShortSaleQuota: cells[17],
      Note: cells[19] ?? "",
    }, "tpex");
    if (row) result.set(row.code, { margin: row.margin, short: row.short, note: row.note });
  }
  return result;
}

function dayTradeCode(row: JsonRow, market: "twse" | "tpex") {
  const value = market === "twse"
    ? row.Code ?? row["證券代號"] ?? row["股票代號"]
    : row.SecuritiesCompanyCode ?? row.SecuritiesCode ?? row.Code ?? row["證券代號"] ?? row["股票代號"];
  const code = clean(value).toUpperCase();
  return /^[0-9A-Z]{4,8}$/.test(code) ? code : null;
}

function parseDayTradeRows(payload: unknown, market: "twse" | "tpex") {
  if (!Array.isArray(payload)) return new Set<string>();
  return new Set(payload.flatMap((raw) => {
    if (!raw || typeof raw !== "object") return [];
    const code = dayTradeCode(raw as JsonRow, market);
    return code ? [code] : [];
  }));
}

export function parseTwseDayTradeRows(payload: unknown) {
  return parseDayTradeRows(payload, "twse");
}

export function parseTpexDayTradeRows(payload: unknown) {
  return parseDayTradeRows(payload, "tpex");
}

export function parseTpexDayTradeCsv(payload: string) {
  const codes = new Set<string>();
  for (const row of payload.split(/\r?\n/)) {
    for (const cell of row.split(",")) {
      const code = clean(cell.replace(/^"|"$/g, "")).toUpperCase();
      if (/^[1-9]\d{3}[A-Z]?$/.test(code)) codes.add(code);
    }
  }
  return codes;
}

export function parseTaifexStockFutures(html: string) {
  const result = new Map<string, { stockFuture: boolean; miniStockFuture: boolean }>();
  for (const match of html.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells = [...match[1].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/gi)].map((cell) => clean(cell[1]));
    const code = cells.find((cell, index) => index >= 2 && index <= 5 && /^\d{4,6}[A-Z]?$/.test(cell));
    if (!code) continue;
    const marker = cells[cells.indexOf(code) + 1] ?? "";
    const current = result.get(code) ?? { stockFuture: false, miniStockFuture: false };
    if (marker.includes("○")) current.stockFuture = true;
    if (marker.includes("◎")) current.miniStockFuture = true;
    result.set(code, current);
  }
  return result;
}

export function futureStatusLabel(status: Pick<StockTradingStatus, "stockFuture" | "miniStockFuture">) {
  if (status.stockFuture && status.miniStockFuture) return "（有股期、有小型期貨）";
  if (status.stockFuture) return "（有股期）";
  if (status.miniStockFuture) return "（有小型期貨）";
  return "";
}

export function isIndividualStockCode(code: string) {
  return /^[1-9]\d{3}[A-Z]?$/.test(code.trim().toUpperCase());
}
