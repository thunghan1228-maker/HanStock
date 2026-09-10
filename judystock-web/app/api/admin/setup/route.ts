import {
  consumeAdminSetupToken,
  deleteAdminSessionsForUser,
  findFirstAdmin,
  updateAdminCredentials,
  validateAdminSetupToken,
  writeAdminAudit,
} from "../../../../db/admin";
import { createPasswordRecord, sha256, validAdminPassword, validAdminUsername } from "../../../../lib/admin-crypto";
import { adminSessionCookie, issueAdminSession, sameOriginRequest } from "../../../admin/admin-auth";

export async function GET(request: Request) {
  const token = new URL(request.url).searchParams.get("token") ?? "";
  if (!/^[a-f0-9]{64}$/u.test(token)) {
    return Response.json({ ok: false, message: "設定連結無效" }, { status: 403, headers: { "Cache-Control": "no-store" } });
  }
  const setup = await validateAdminSetupToken(await sha256(token));
  return setup
    ? Response.json({ ok: true, expiresAt: setup.expiresAt }, { headers: { "Cache-Control": "no-store" } })
    : Response.json({ ok: false, message: "設定連結已使用或已過期" }, { status: 403, headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "設定請求來源不正確" }, { status: 403 });
  }
  const input = await request.json().catch(() => ({})) as Record<string, unknown>;
  const token = String(input.token ?? "");
  const username = String(input.username ?? "").trim().toLowerCase();
  const password = String(input.password ?? "");
  const confirmPassword = String(input.confirmPassword ?? "");
  if (!/^[a-f0-9]{64}$/u.test(token)) {
    return Response.json({ ok: false, message: "設定連結無效" }, { status: 403 });
  }
  if (!validAdminUsername(username)) {
    return Response.json({ ok: false, message: "帳號需為 3～32 個英文字母、數字、點、底線或連字號" }, { status: 400 });
  }
  if (!validAdminPassword(password)) {
    return Response.json({ ok: false, message: "密碼需為 10～128 個字元" }, { status: 400 });
  }
  if (password !== confirmPassword) {
    return Response.json({ ok: false, message: "兩次輸入的密碼不同" }, { status: 400 });
  }
  const tokenHash = await sha256(token);
  const validToken = await validateAdminSetupToken(tokenHash);
  if (!validToken) {
    return Response.json({ ok: false, message: "設定連結已使用或已過期" }, { status: 403 });
  }
  const admin = await findFirstAdmin();
  if (!admin) {
    return Response.json({ ok: false, message: "管理員資料尚未建立" }, { status: 503 });
  }
  // Derive the password record before consuming the one-time token. If the
  // runtime rejects the crypto operation, the user can safely retry.
  let record;
  try {
    record = await createPasswordRecord(password);
  } catch {
    return Response.json({ ok: false, message: "密碼安全處理失敗，請稍後再試" }, { status: 500 });
  }
  const consumed = await consumeAdminSetupToken(tokenHash);
  if (!consumed) {
    return Response.json({ ok: false, message: "設定連結已使用或已過期" }, { status: 403 });
  }
  const updated = await updateAdminCredentials(admin.id, {
    username,
    passwordHash: record.hash,
    passwordSalt: record.salt,
    passwordIterations: record.iterations,
  });
  if (!updated) {
    return Response.json({ ok: false, message: "管理員帳號設定失敗" }, { status: 500 });
  }
  await deleteAdminSessionsForUser(admin.id);
  const session = await issueAdminSession(admin.id);
  await writeAdminAudit(username, "account.setup", "admin", { username });
  return Response.json(
    { ok: true, username },
    { headers: { "Cache-Control": "no-store", "Set-Cookie": adminSessionCookie(session.token) } },
  );
}
