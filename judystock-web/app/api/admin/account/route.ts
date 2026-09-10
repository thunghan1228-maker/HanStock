import {
  deleteAdminSessionsForUser,
  findAdminByUsername,
  updateAdminCredentials,
  writeAdminAudit,
} from "../../../../db/admin";
import { createPasswordRecord, validAdminPassword, validAdminUsername, verifyPassword } from "../../../../lib/admin-crypto";
import {
  adminSessionCookie,
  forbiddenJson,
  getBattleAdmin,
  issueAdminSession,
  sameOriginRequest,
} from "../../../admin/admin-auth";

export async function GET(request: Request) {
  const admin = await getBattleAdmin(request);
  if (!admin) return forbiddenJson();
  return Response.json(
    { ok: true, username: admin.username, displayName: admin.displayName },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: Request) {
  const admin = await getBattleAdmin(request);
  if (!admin) return forbiddenJson();
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "帳號修改請求來源不正確" }, { status: 403 });
  }
  const input = await request.json().catch(() => ({})) as Record<string, unknown>;
  const currentPassword = String(input.currentPassword ?? "");
  const newUsername = String(input.newUsername ?? admin.username).trim().toLowerCase();
  const newPassword = String(input.newPassword ?? "");
  const confirmPassword = String(input.confirmPassword ?? "");
  const record = await findAdminByUsername(admin.username);
  if (!record?.passwordHash || !record.passwordSalt || !record.passwordIterations) {
    return Response.json({ ok: false, message: "管理員帳號資料不完整" }, { status: 409 });
  }
  const validCurrent = await verifyPassword(currentPassword, record.passwordSalt, record.passwordHash, record.passwordIterations);
  if (!validCurrent) {
    return Response.json({ ok: false, message: "目前密碼錯誤" }, { status: 403 });
  }
  if (!validAdminUsername(newUsername)) {
    return Response.json({ ok: false, message: "帳號需為 3～32 個英文字母、數字、點、底線或連字號" }, { status: 400 });
  }
  if (newPassword && !validAdminPassword(newPassword)) {
    return Response.json({ ok: false, message: "新密碼需為 10～128 個字元" }, { status: 400 });
  }
  if (newPassword && newPassword !== confirmPassword) {
    return Response.json({ ok: false, message: "兩次輸入的新密碼不同" }, { status: 400 });
  }
  const passwordRecord = await createPasswordRecord(newPassword || currentPassword);
  try {
    await updateAdminCredentials(admin.id, {
      username: newUsername,
      passwordHash: passwordRecord.hash,
      passwordSalt: passwordRecord.salt,
      passwordIterations: passwordRecord.iterations,
    });
  } catch {
    return Response.json({ ok: false, message: "這個管理員帳號已被使用" }, { status: 409 });
  }
  await deleteAdminSessionsForUser(admin.id);
  const session = await issueAdminSession(admin.id);
  await writeAdminAudit(newUsername, "account.update", "admin", {
    usernameChanged: newUsername !== admin.username,
    passwordChanged: Boolean(newPassword),
  });
  return Response.json(
    { ok: true, username: newUsername },
    { headers: { "Cache-Control": "no-store", "Set-Cookie": adminSessionCookie(session.token) } },
  );
}
