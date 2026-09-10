import { readManualObservationHomework } from "../../../db/manual-observation-homework";
import { mergeManualObservationRecords } from "../../../lib/manual-observation-homework";

export async function GET() {
  const records = mergeManualObservationRecords(await readManualObservationHomework());
  return Response.json({ ok: true, records }, { headers: { "Cache-Control": "no-store" } });
}
