import { getBattleAdmin, forbiddenJson, sameOriginRequest } from "../../../admin/admin-auth";
import { readBattleSettings, saveBattleSettings, writeAdminAudit } from "../../../../db/admin";

export async function GET() {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  const settings = await readBattleSettings();
  return Response.json({ ok: true, settings }, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  if (!sameOriginRequest(request)) {
    return Response.json({ ok: false, message: "設定儲存請求來源不正確" }, { status: 403 });
  }
  try {
    const input = await request.json();
    const settings = await saveBattleSettings(input, admin.username);
    await writeAdminAudit(admin.username, "settings.update", "battle-runtime", settings);
    return Response.json({ ok: true, settings }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json(
      { ok: false, message: error instanceof Error ? error.message : "設定儲存失敗" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
}
