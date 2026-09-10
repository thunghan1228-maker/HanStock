import { eq } from "drizzle-orm";
import { getDb } from "./index";
import { adminSettings } from "./schema";
import type { ManualObservationRecord } from "../lib/manual-observation-homework";

const SETTINGS_KEY = "manual_observation_homework_v1";
const MAX_RECORDS = 120;

function isRecord(value: unknown): value is ManualObservationRecord {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<ManualObservationRecord>;
  return (row.kind === "preopen-short" || row.kind === "close-observation")
    && /^\d{4}-\d{2}-\d{2}$/.test(row.tradeDate ?? "")
    && typeof row.title === "string"
    && Array.isArray(row.rules)
    && Array.isArray(row.releaseStocks)
    && Array.isArray(row.groups);
}

export async function readManualObservationHomework(): Promise<ManualObservationRecord[]> {
  const [row] = await getDb()
    .select({ value: adminSettings.value })
    .from(adminSettings)
    .where(eq(adminSettings.key, SETTINGS_KEY))
    .limit(1);
  if (!row) return [];
  try {
    const parsed = JSON.parse(row.value) as unknown;
    return Array.isArray(parsed) ? parsed.filter(isRecord) : [];
  } catch {
    return [];
  }
}

export async function saveManualObservationHomework(record: ManualObservationRecord, adminUsername: string) {
  const current = await readManualObservationHomework();
  const records = [record, ...current.filter((item) => item.kind !== record.kind || item.tradeDate !== record.tradeDate)]
    .sort((a, b) => b.tradeDate.localeCompare(a.tradeDate) || b.updatedAt - a.updatedAt)
    .slice(0, MAX_RECORDS);
  await persist(records, adminUsername);
  return records;
}

export async function deleteManualObservationHomework(kind: ManualObservationRecord["kind"], tradeDate: string, adminUsername: string) {
  const current = await readManualObservationHomework();
  const records = current.filter((item) => item.kind !== kind || item.tradeDate !== tradeDate);
  await persist(records, adminUsername);
  return records;
}

async function persist(records: ManualObservationRecord[], adminUsername: string) {
  const now = Date.now();
  await getDb().insert(adminSettings).values({
    key: SETTINGS_KEY,
    value: JSON.stringify(records),
    updatedAt: now,
    updatedBy: adminUsername,
  }).onConflictDoUpdate({
    target: adminSettings.key,
    set: { value: JSON.stringify(records), updatedAt: now, updatedBy: adminUsername },
  });
}
