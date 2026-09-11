import { readLatestBrokerBranchDaily, type BrokerBranchDailyRecord } from "../../../../db/broker-branch-daily";

export async function GET() {
  try {
    const rows = await readLatestBrokerBranchDaily();
    return Response.json({
      status: "ok",
      complete: rows.length > 0,
      rows: rows as BrokerBranchDailyRecord[],
      updatedAt: new Date().toISOString(),
    }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ status: "ok", complete: false, rows: [], message: "本機尚無已保存的正式券商分點日資料" }, { headers: { "Cache-Control": "no-store" } });
  }
}
