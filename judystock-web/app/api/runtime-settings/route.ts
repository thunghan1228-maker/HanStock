import { readBattleSettings } from "../../../db/admin";
import { DEFAULT_BATTLE_SETTINGS } from "../../../lib/battle-settings";

export async function GET() {
  try {
    const settings = await readBattleSettings();
    return Response.json(
      { ok: true, settings },
      { headers: { "Cache-Control": "public, max-age=15, s-maxage=30" } },
    );
  } catch {
    return Response.json(
      { ok: true, settings: DEFAULT_BATTLE_SETTINGS, fallback: true },
      { headers: { "Cache-Control": "public, max-age=5" } },
    );
  }
}
