import {
  adminSessionCookie,
  authenticateAdmin,
  clearAdminSessionCookie,
  revokeAdminSession,
  sameOriginRequest,
} from "../../../admin/admin-auth";
import { writeAdminAudit } from "../../../../db/admin";

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "登入請求來源不正確" }, { status: 403 });
  }
  const input = await request.json().catch(() => ({})) as { username?: unknown; password?: unknown };
  const username = String(input.username ?? "").trim().toLowerCase();
  const password = String(input.password ?? "");
  if (!username || !password) {
    return Response.json({ ok: false, message: "請輸入管理員帳號與密碼" }, { status: 400 });
  }
  const result = await authenticateAdmin(username, password);
  if (!result.ok) {
    return Response.json({ ok: false, message: result.reason }, { status: 401, headers: { "Cache-Control": "no-store" } });
  }
  await writeAdminAudit(result.admin.username ?? username, "session.login", "admin", { success: true });
  return Response.json(
    { ok: true, username: result.admin.username },
    {
      headers: {
        "Cache-Control": "no-store",
        "Set-Cookie": adminSessionCookie(result.token),
      },
    },
  );
}

export async function DELETE(request: Request) {
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "登出請求來源不正確" }, { status: 403 });
  }
  await revokeAdminSession(request);
  return Response.json(
    { ok: true },
    { headers: { "Cache-Control": "no-store", "Set-Cookie": clearAdminSessionCookie() } },
  );
}
