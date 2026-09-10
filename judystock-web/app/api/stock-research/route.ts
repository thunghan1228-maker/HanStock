import { NextRequest, NextResponse } from "next/server";

type JsonRow = Record<string, unknown>;
type ResearchMarket = "twse" | "tpex";

const noStore = { "Cache-Control": "private, no-store, max-age=0" };

function rowsFrom(payload: unknown): JsonRow[] {
  if (Array.isArray(payload)) return payload.filter((row): row is JsonRow => Boolean(row) && typeof row === "object" && !Array.isArray(row));
  if (!payload || typeof payload !== "object") return [];
  const record = payload as JsonRow;
  for (const key of ["data", "rows", "aaData", "result"]) {
    const rows = rowsFrom(record[key]);
    if (rows.length) return rows;
  }
  return [];
}

function compactKey(value: string) {
  return value.replace(/[\s_()（）\/\-]/g, "").toLowerCase();
}

function pick(row: JsonRow, aliases: string[], contains: string[] = []) {
  const entries = Object.entries(row);
  const exact = new Set(aliases.map(compactKey));
  const exactMatch = entries.find(([key]) => exact.has(compactKey(key)));
  if (exactMatch) return String(exactMatch[1] ?? "").trim();
  const needles = contains.map(compactKey);
  const partial = entries.find(([key]) => needles.some((needle) => compactKey(key).includes(needle)));
  return partial ? String(partial[1] ?? "").trim() : "";
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").replaceAll("%", "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function rocDate(value: string) {
  const digits = value.replace(/\D/g, "");
  if (digits.length === 7) return `${Number(digits.slice(0, 3)) + 1911}/${digits.slice(3, 5)}/${digits.slice(5, 7)}`;
  if (digits.length === 8) return `${digits.slice(0, 4)}/${digits.slice(4, 6)}/${digits.slice(6, 8)}`;
  return value;
}

function rocMonth(value: string) {
  const digits = value.replace(/\D/g, "");
  if (digits.length === 5) return `${Number(digits.slice(0, 3)) + 1911}/${digits.slice(3, 5)}`;
  if (digits.length === 6) return `${digits.slice(0, 4)}/${digits.slice(4, 6)}`;
  return value;
}

function tickerOf(row: JsonRow) {
  return pick(row, ["公司代號", "證券代號", "股票代號", "Code", "SecuritiesCompanyCode"], ["公司代號", "證券代號"]);
}

async function fetchRows(url: string) {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Research/1.0" },
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`official_${response.status}`);
  return rowsFrom(await response.json());
}

async function firstAvailable(urls: string[]) {
  for (const url of urls) {
    try {
      const rows = await fetchRows(url);
      if (rows.length) return rows;
    } catch {
      // Continue to the alternate official endpoint.
    }
  }
  return [];
}

async function loadDailyCandles(ticker: string) {
  const input = encodeURIComponent(JSON.stringify({ json: { ticker, interval: "1d" } }));
  const response = await fetch(`https://www.hanstock.xyz/api/trpc/stocks.candles?input=${input}`, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "HanStock-Research/1.0" },
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`candles_${response.status}`);
  const payload = await response.json() as { result?: { data?: { json?: { candles?: Array<Record<string, unknown>> } } } };
  const raw = payload.result?.data?.json?.candles ?? [];
  return raw.map((row) => ({
    date: String(row.date ?? row.time ?? "").slice(0, 10).replaceAll("-", "/"),
    close: numberValue(row.close),
  })).filter((row): row is { date: string; close: number } => Boolean(row.date) && row.close !== null && row.close > 0)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(-260);
}

function officialUrls(market: ResearchMarket, dataset: "valuation" | "profitability" | "transfers" | "directors") {
  if (market === "twse") {
    return {
      valuation: ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"],
      profitability: ["https://openapi.twse.com.tw/v1/opendata/t187ap17_L"],
      transfers: ["https://openapi.twse.com.tw/v1/opendata/t187ap12_L"],
      directors: ["https://openapi.twse.com.tw/v1/opendata/t187ap11_L"],
    }[dataset];
  }
  return {
    valuation: ["https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"],
    profitability: ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap17_O"],
    transfers: ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O"],
    directors: ["https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O"],
  }[dataset];
}

export async function GET(request: NextRequest) {
  const ticker = (request.nextUrl.searchParams.get("ticker") ?? "").trim().toUpperCase();
  const market: ResearchMarket = request.nextUrl.searchParams.get("market") === "tpex" ? "tpex" : "twse";
  if (!/^[0-9A-Z]{4,7}$/.test(ticker)) {
    return NextResponse.json({ ok: false, error: "ticker_required" }, { status: 400, headers: noStore });
  }

  const [candlesResult, valuationRows, profitabilityRows, transferRows, directorRows] = await Promise.all([
    loadDailyCandles(ticker).catch(() => []),
    firstAvailable(officialUrls(market, "valuation")),
    firstAvailable(officialUrls(market, "profitability")),
    firstAvailable(officialUrls(market, "transfers")),
    firstAvailable(officialUrls(market, "directors")),
  ]);

  const valuationRow = valuationRows.find((row) => tickerOf(row) === ticker) ?? null;
  const profitabilityRow = profitabilityRows.find((row) => tickerOf(row) === ticker) ?? null;
  const transfers = transferRows.filter((row) => tickerOf(row) === ticker).slice(0, 30).map((row, index) => ({
    id: `${ticker}-transfer-${index}`,
    identity: pick(row, ["申報人身分", "身份別", "職稱"], ["身分", "身份"]),
    name: pick(row, ["申報人姓名", "姓名", "申報人"], ["姓名"]),
    method: pick(row, ["轉讓方式", "申報轉讓方式"], ["轉讓方式"]),
    plannedShares: numberValue(pick(row, ["預定轉讓總股數", "申報轉讓股數", "預定轉讓股數"], ["預定轉讓總股數", "轉讓股數"])),
    currentShares: numberValue(pick(row, ["目前持有股數", "申報時持有股數"], ["目前持有股數", "持有股數"])),
    afterShares: numberValue(pick(row, ["轉讓後持有股數", "預定轉讓後持股"], ["轉讓後持有"])),
    period: pick(row, ["預定轉讓期間", "轉讓期間", "有效期間"], ["轉讓期間", "有效期間"]),
    reportDate: pick(row, ["申報日期", "資料日期", "出表日期"], ["申報日期", "資料日期"]),
  }));
  const directors = directorRows.filter((row) => tickerOf(row) === ticker).slice(0, 40).map((row, index) => ({
    id: `${ticker}-director-${index}`,
    identity: pick(row, ["身分別", "身份別", "職稱"], ["身分", "身份"]),
    name: pick(row, ["姓名", "法人代表人姓名"], ["姓名"]),
    shares: numberValue(pick(row, ["目前持股", "目前持有股數", "持有股數", "本人持股"], ["目前持股", "持有股數", "本人持股"])),
    relatedShares: numberValue(pick(row, ["內部人關係人目前持股合計", "關係人目前持股合計", "關係人持股"], ["關係人目前持股合計", "關係人持股"])),
    pledgeRatio: numberValue(pick(row, ["設質股數佔持股比例", "設質股數占持股比例", "設質比例"], ["設質股數佔持股比例", "設質股數占持股比例"])),
    pledgedShares: numberValue(pick(row, ["設質股數", "質權設定股數"], ["設質股數", "質權設定"])),
    reportDate: rocDate(pick(row, ["出表日期", "資料日期"], ["出表日期", "資料日期"])),
    dataMonth: rocMonth(pick(row, ["資料年月"], ["資料年月"])),
  }));

  const pe = valuationRow ? numberValue(pick(valuationRow, ["本益比", "PEratio", "P/E"], ["本益比"])) : null;
  const pb = valuationRow ? numberValue(pick(valuationRow, ["股價淨值比", "PBratio", "P/B"], ["股價淨值比"])) : null;
  const dividendYield = valuationRow ? numberValue(pick(valuationRow, ["殖利率(%)", "殖利率", "DividendYield"], ["殖利率"])) : null;
  const eps = profitabilityRow ? numberValue(pick(profitabilityRow, ["基本每股盈餘(元)", "基本每股盈餘", "EPS"], ["每股盈餘", "eps"])) : null;
  const companyName = pick(valuationRow ?? profitabilityRow ?? transferRows.find((row) => tickerOf(row) === ticker) ?? {}, ["公司名稱", "證券名稱", "Name", "CompanyName"], ["公司名稱", "證券名稱"]);

  return NextResponse.json({
    ok: candlesResult.length > 0 || Boolean(valuationRow) || transfers.length > 0 || directors.length > 0,
    ticker,
    companyName,
    market,
    updatedAt: new Date().toISOString(),
    sources: { candles: candlesResult.length > 0, valuation: Boolean(valuationRow), profitability: Boolean(profitabilityRow), transfers: transferRows.length > 0, directors: directorRows.length > 0 },
    candles: candlesResult,
    valuation: { pe, pb, dividendYield, eps, dataDate: valuationRow ? pick(valuationRow, ["日期", "資料日期", "Date"], ["日期"]) : "" },
    transfers,
    directors,
  }, { headers: noStore });
}
