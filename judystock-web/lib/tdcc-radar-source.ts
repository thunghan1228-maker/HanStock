import type { TdccSnapshotRecord } from "../db/tdcc-radar";

export const TDCC_CSV_SOURCE = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5";
export const TDCC_JSON_SOURCE = "https://openapi.tdcc.com.tw/v1/opendata/1-5";

type JsonRecord = Record<string, unknown>;

function clean(value: unknown) {
  return String(value ?? "").replace(/^\uFEFF/, "").trim();
}

function normalizeDate(value: string) {
  return /^\d{8}$/.test(value) ? `${value.slice(0, 4)}/${value.slice(4, 6)}/${value.slice(6, 8)}` : value;
}

function parseCsvFields(line: string) {
  const fields: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      fields.push(field);
      field = "";
    } else {
      field += character;
    }
  }
  fields.push(field);
  return fields;
}

export function parseTdccCsv(csv: string) {
  const lines = csv.replace(/^\uFEFF/, "").split(/\r?\n/).filter(Boolean);
  const headers = parseCsvFields(lines.shift() ?? "").map(clean);
  const dateIndex = headers.indexOf("資料日期");
  const tickerIndex = headers.indexOf("證券代號");
  const levelIndex = headers.indexOf("持股分級");
  const percentageIndex = headers.indexOf("占集保庫存數比例%");
  if ([dateIndex, tickerIndex, levelIndex, percentageIndex].some((index) => index < 0)) throw new Error("tdcc_csv_headers_invalid");

  return lines.flatMap((line): TdccSnapshotRecord[] => {
    const fields = parseCsvFields(line);
    if (clean(fields[levelIndex]) !== "15") return [];
    const ticker = clean(fields[tickerIndex]).toUpperCase();
    const rawDate = clean(fields[dateIndex]);
    const largeHolderPct = Number(clean(fields[percentageIndex]));
    if (!/^\d{4,6}[A-Z]?$/.test(ticker) || !rawDate || !Number.isFinite(largeHolderPct)) return [];
    return [{ ticker, dataDate: normalizeDate(rawDate), largeHolderPct }];
  });
}

export function parseTdccJson(payload: unknown) {
  if (!Array.isArray(payload)) throw new Error("tdcc_payload_invalid");
  return payload.flatMap((item): TdccSnapshotRecord[] => {
    const row = item as JsonRecord;
    const ticker = clean(row["證券代號"]).toUpperCase();
    const level = clean(row["持股分級"]);
    const rawDate = clean(row["資料日期"] ?? row["﻿資料日期"]);
    const largeHolderPct = Number(clean(row["占集保庫存數比例%"]));
    if (level !== "15" || !/^\d{4,6}[A-Z]?$/.test(ticker) || !rawDate || !Number.isFinite(largeHolderPct)) return [];
    return [{ ticker, dataDate: normalizeDate(rawDate), largeHolderPct }];
  });
}

async function fetchCsvSnapshot() {
  const url = new URL(TDCC_CSV_SOURCE);
  url.searchParams.set("refresh", String(Math.floor(Date.now() / (10 * 60 * 1_000))));
  const response = await fetch(url, {
    headers: { Accept: "text/csv, text/plain;q=0.9", "Cache-Control": "no-cache", "User-Agent": "HanStock-TDCC-Radar/2.0" },
    cache: "no-store",
    signal: AbortSignal.timeout(25_000),
  });
  if (!response.ok) throw new Error(`tdcc_csv_${response.status}`);
  const records = parseTdccCsv(await response.text());
  if (records.length < 1_000) throw new Error("tdcc_csv_incomplete");
  return records;
}

async function fetchJsonSnapshot() {
  const response = await fetch(TDCC_JSON_SOURCE, {
    headers: { Accept: "application/json", "Cache-Control": "no-cache", "User-Agent": "HanStock-TDCC-Radar/2.0" },
    cache: "no-store",
    signal: AbortSignal.timeout(18_000),
  });
  if (!response.ok) throw new Error(`tdcc_json_${response.status}`);
  const records = parseTdccJson(await response.json());
  if (records.length < 1_000) throw new Error("tdcc_json_incomplete");
  return records;
}

export async function fetchLatestTdccSnapshot() {
  try {
    return { records: await fetchCsvSnapshot(), source: TDCC_CSV_SOURCE, sourceFormat: "csv" as const };
  } catch (csvError) {
    try {
      return { records: await fetchJsonSnapshot(), source: TDCC_JSON_SOURCE, sourceFormat: "json" as const };
    } catch (jsonError) {
      const csvMessage = csvError instanceof Error ? csvError.message : "tdcc_csv_unavailable";
      const jsonMessage = jsonError instanceof Error ? jsonError.message : "tdcc_json_unavailable";
      throw new Error(`${csvMessage};${jsonMessage}`);
    }
  }
}
