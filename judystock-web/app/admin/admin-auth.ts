import { headers } from "next/headers";
import { redirect } from "next/navigation";
import {
  createAdminSession,
  deleteAdminSession,
  findAdminBySession,
  findAdminByUsername,
  recordLoginFailure,
  touchAdminLogin,
} from "../../db/admin";
import { randomToken, sha256, verifyPassword } from "../../lib/admin-crypto";

export const ADMIN_COOKIE_NAME = "hanstock_battle_admin";
export const ADMIN_SESSION_SECONDS = 7 * 24 * 60 * 60;

export type BattleAdmin = {
  id: number;
  username: string;
  displayName: string;
  email: string;
  sessionHash: string;
};

function cookieValue(cookieHeader: string | null, name: string) {
  if (!cookieHeader) return null;
  for (const part of cookieHeader.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export async function getBattleAdmin(request?: Request): Promise<BattleAdmin | null> {
  const cookieHeader = request?.headers.get("cookie") ?? (await headers()).get("cookie");
  const token = cookieValue(cookieHeader, ADMIN_COOKIE_NAME);
  if (!token) return null;
  const sessionHash = await sha256(token);
  const admin = await findAdminBySession(sessionHash);
  if (!admin?.username) return null;
  return {
    id: admin.id,
    username: admin.username,
    displayName: admin.displayName ?? admin.username,
    email: admin.email,
    sessionHash,
  };
}

export async function requireBattleAdmin(returnTo = "/admin"): Promise<BattleAdmin> {
  const admin = await getBattleAdmin();
  if (admin) return admin;
  const safeReturnTo = returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : "/admin";
  redirect(`/admin/login?return_to=${encodeURIComponent(safeReturnTo)}`);
}

export async function authenticateAdmin(username: string, password: string) {
  const admin = await findAdminByUsername(username);
  if (!admin?.passwordHash || !admin.passwordSalt || !admin.passwordIterations) {
    await new Promise((resolve) => setTimeout(resolve, 450));
    return { ok: false as const, reason: "帳號或密碼錯誤" };
  }
  if (admin.lockedUntil && admin.lockedUntil > Date.now()) {
    return {
      ok: false as const,
      reason: `登入錯誤次數過多，請於 ${new Date(admin.lockedUntil).toISOString()} 後再試`,
    };
  }
  const valid = await verifyPassword(password, admin.passwordSalt, admin.passwordHash, admin.passwordIterations);
  if (!valid) {
    await recordLoginFailure(admin.id, admin.failedLoginCount);
    return { ok: false as const, reason: "帳號或密碼錯誤" };
  }
  await touchAdminLogin(admin.id);
  const session = await issueAdminSession(admin.id);
  return { ok: true as const, admin, ...session };
}

export async function issueAdminSession(adminUserId: number) {
  const token = randomToken();
  const tokenHash = await sha256(token);
  const expiresAt = Date.now() + ADMIN_SESSION_SECONDS * 1000;
  await createAdminSession(tokenHash, adminUserId, expiresAt);
  return { token, tokenHash, expiresAt };
}

export async function revokeAdminSession(request: Request) {
  const token = cookieValue(request.headers.get("cookie"), ADMIN_COOKIE_NAME);
  if (token) await deleteAdminSession(await sha256(token));
}

export function adminSessionCookie(token: string) {
  return `${ADMIN_COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${ADMIN_SESSION_SECONDS}`;
}

export function clearAdminSessionCookie() {
  return `${ADMIN_COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;
}

export function sameOriginRequest(request: Request) {
  const origin = request.headers.get("origin");
  return Boolean(origin && origin === new URL(request.url).origin);
}

export function forbiddenJson() {
  return Response.json(
    { ok: false, message: "管理員登入已失效，請重新登入" },
    { status: 401, headers: { "Cache-Control": "no-store" } },
  );
}
