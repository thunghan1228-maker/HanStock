import { and, count, desc, eq, gt, isNull, max } from "drizzle-orm";
import { getDb } from "./index";
import {
  adminAuditLogs,
  adminSessions,
  adminSettings,
  adminSetupTokens,
  adminUsers,
  dailyForceTotals,
  earlySellSignals,
  intradayForceBars,
} from "./schema";
import {
  DEFAULT_BATTLE_SETTINGS,
  normalizeBattleSettings,
  type BattleRuntimeSettings,
} from "../lib/battle-settings";

const SETTINGS_KEY = "battle_runtime";

export async function findAdminByUsername(username: string) {
  const normalized = username.trim().toLowerCase();
  const [admin] = await getDb()
    .select()
    .from(adminUsers)
    .where(eq(adminUsers.username, normalized))
    .limit(1);
  return admin?.isActive ? admin : null;
}

export async function findFirstAdmin() {
  const [admin] = await getDb().select().from(adminUsers).orderBy(adminUsers.id).limit(1);
  return admin ?? null;
}

export async function touchAdminLogin(adminUserId: number) {
  await getDb()
    .update(adminUsers)
    .set({ lastLoginAt: Date.now(), failedLoginCount: 0, lockedUntil: null })
    .where(eq(adminUsers.id, adminUserId));
}

export async function recordLoginFailure(adminUserId: number, currentFailures: number) {
  const failures = currentFailures + 1;
  await getDb()
    .update(adminUsers)
    .set({
      failedLoginCount: failures >= 5 ? 0 : failures,
      lockedUntil: failures >= 5 ? Date.now() + 15 * 60 * 1000 : null,
    })
    .where(eq(adminUsers.id, adminUserId));
}

export async function createAdminSession(tokenHash: string, adminUserId: number, expiresAt: number) {
  const now = Date.now();
  await getDb().insert(adminSessions).values({
    tokenHash,
    adminUserId,
    createdAt: now,
    expiresAt,
    lastSeenAt: now,
  });
}

export async function findAdminBySession(tokenHash: string) {
  const [row] = await getDb()
    .select({
      id: adminUsers.id,
      username: adminUsers.username,
      displayName: adminUsers.displayName,
      email: adminUsers.email,
      isActive: adminUsers.isActive,
      sessionHash: adminSessions.tokenHash,
      expiresAt: adminSessions.expiresAt,
      lastSeenAt: adminSessions.lastSeenAt,
    })
    .from(adminSessions)
    .innerJoin(adminUsers, eq(adminSessions.adminUserId, adminUsers.id))
    .where(and(
      eq(adminSessions.tokenHash, tokenHash),
      eq(adminUsers.isActive, true),
      gt(adminSessions.expiresAt, Date.now()),
    ))
    .limit(1);
  if (!row?.username) return null;
  if (Date.now() - row.lastSeenAt > 60 * 60 * 1000) {
    await getDb().update(adminSessions).set({ lastSeenAt: Date.now() }).where(eq(adminSessions.tokenHash, tokenHash));
  }
  return row;
}

export async function deleteAdminSession(tokenHash: string) {
  await getDb().delete(adminSessions).where(eq(adminSessions.tokenHash, tokenHash));
}

export async function deleteAdminSessionsForUser(adminUserId: number) {
  await getDb().delete(adminSessions).where(eq(adminSessions.adminUserId, adminUserId));
}

export async function consumeAdminSetupToken(tokenHash: string) {
  const [row] = await getDb()
    .update(adminSetupTokens)
    .set({ usedAt: Date.now() })
    .where(and(
      eq(adminSetupTokens.tokenHash, tokenHash),
      isNull(adminSetupTokens.usedAt),
      gt(adminSetupTokens.expiresAt, Date.now()),
    ))
    .returning();
  return row ?? null;
}

export async function validateAdminSetupToken(tokenHash: string) {
  const [row] = await getDb()
    .select({ tokenHash: adminSetupTokens.tokenHash, expiresAt: adminSetupTokens.expiresAt })
    .from(adminSetupTokens)
    .where(and(
      eq(adminSetupTokens.tokenHash, tokenHash),
      isNull(adminSetupTokens.usedAt),
      gt(adminSetupTokens.expiresAt, Date.now()),
    ))
    .limit(1);
  return row ?? null;
}

export async function updateAdminCredentials(
  adminUserId: number,
  credentials: {
    username: string;
    passwordHash: string;
    passwordSalt: string;
    passwordIterations: number;
  },
) {
  const [updated] = await getDb()
    .update(adminUsers)
    .set({
      username: credentials.username.trim().toLowerCase(),
      passwordHash: credentials.passwordHash,
      passwordSalt: credentials.passwordSalt,
      passwordIterations: credentials.passwordIterations,
      failedLoginCount: 0,
      lockedUntil: null,
      passwordUpdatedAt: Date.now(),
    })
    .where(eq(adminUsers.id, adminUserId))
    .returning();
  return updated ?? null;
}

export async function readBattleSettings(): Promise<BattleRuntimeSettings> {
  const [row] = await getDb()
    .select({ value: adminSettings.value })
    .from(adminSettings)
    .where(eq(adminSettings.key, SETTINGS_KEY))
    .limit(1);
  if (!row) return DEFAULT_BATTLE_SETTINGS;
  try {
    return normalizeBattleSettings(JSON.parse(row.value));
  } catch {
    return DEFAULT_BATTLE_SETTINGS;
  }
}

export async function saveBattleSettings(input: unknown, adminEmail: string) {
  const settings = normalizeBattleSettings(input);
  await getDb()
    .insert(adminSettings)
    .values({
      key: SETTINGS_KEY,
      value: JSON.stringify(settings),
      updatedAt: Date.now(),
      updatedBy: adminEmail,
    })
    .onConflictDoUpdate({
      target: adminSettings.key,
      set: {
        value: JSON.stringify(settings),
        updatedAt: Date.now(),
        updatedBy: adminEmail,
      },
    });
  return settings;
}

export async function writeAdminAudit(
  adminEmail: string,
  action: string,
  target: string,
  details: unknown,
) {
  await getDb().insert(adminAuditLogs).values({
    adminEmail,
    action,
    target,
    details: JSON.stringify(details ?? {}),
    createdAt: Date.now(),
  });
}

export async function readRecentAdminAudit(limit = 20) {
  return getDb()
    .select()
    .from(adminAuditLogs)
    .orderBy(desc(adminAuditLogs.createdAt))
    .limit(Math.max(1, Math.min(50, limit)));
}

export async function readStorageSummary() {
  const db = getDb();
  const [daily, bars, signals] = await Promise.all([
    db.select({ count: count(), latestDate: max(dailyForceTotals.tradeDate) }).from(dailyForceTotals),
    db.select({ count: count(), latestDate: max(intradayForceBars.tradeDate) }).from(intradayForceBars),
    db.select({ count: count(), latestDate: max(earlySellSignals.tradeDate) }).from(earlySellSignals),
  ]);
  return {
    dailyForceTotals: daily[0] ?? { count: 0, latestDate: null },
    intradayForceBars: bars[0] ?? { count: 0, latestDate: null },
    earlySellSignals: signals[0] ?? { count: 0, latestDate: null },
  };
}
