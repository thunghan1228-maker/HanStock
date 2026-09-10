import { getBattleAdmin, forbiddenJson, sameOriginRequest } from "../../../admin/admin-auth";
import { deleteManualObservationHomework, readManualObservationHomework, saveManualObservationHomework } from "../../../../db/manual-observation-homework";
import { writeAdminAudit } from "../../../../db/admin";
import { parseManualObservationText, type ManualObservationKind } from "../../../../lib/manual-observation-homework";

function validKind(value: unknown): value is ManualObservationKind {
  return value === "preopen-short" || value === "close-observation";
}

export async function GET() {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  return Response.json({ ok: true, records: await readManualObservationHomework() }, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  if (!sameOriginRequest(request)) return Response.json({ ok: false, message: "資料更新請求來源不正確" }, { status: 403 });
  try {
    const input = await request.json() as { kind?: unknown; tradeDate?: unknown; text?: unknown; save?: unknown };
    if (!validKind(input.kind)) throw new Error("請選擇盤前或盤後資料");
    const record = parseManualObservationText({
      kind: input.kind,
      tradeDate: String(input.tradeDate ?? ""),
      text: String(input.text ?? ""),
    });
    if (input.save !== true) return Response.json({ ok: true, record }, { headers: { "Cache-Control": "no-store" } });
    const records = await saveManualObservationHomework(record, admin.username);
    await writeAdminAudit(admin.username, "manual-homework.update", `${record.kind}:${record.tradeDate}`, {
      groups: record.groups.length,
      stocks: record.releaseStocks.length + record.groups.reduce((total, group) => total + group.stocks.length, 0),
    });
    return Response.json({ ok: true, record, records }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json({ ok: false, message: error instanceof Error ? error.message : "文章整理失敗" }, { status: 400, headers: { "Cache-Control": "no-store" } });
  }
}

export async function DELETE(request: Request) {
  const admin = await getBattleAdmin();
  if (!admin) return forbiddenJson();
  if (!sameOriginRequest(request)) return Response.json({ ok: false, message: "資料刪除請求來源不正確" }, { status: 403 });
  try {
    const input = await request.json() as { kind?: unknown; tradeDate?: unknown };
    if (!validKind(input.kind) || !/^\d{4}-\d{2}-\d{2}$/.test(String(input.tradeDate ?? ""))) throw new Error("刪除資料不正確");
    const tradeDate = String(input.tradeDate);
    const records = await deleteManualObservationHomework(input.kind, tradeDate, admin.username);
    await writeAdminAudit(admin.username, "manual-homework.delete", `${input.kind}:${tradeDate}`, {});
    return Response.json({ ok: true, records }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return Response.json({ ok: false, message: error instanceof Error ? error.message : "刪除失敗" }, { status: 400, headers: { "Cache-Control": "no-store" } });
  }
}
