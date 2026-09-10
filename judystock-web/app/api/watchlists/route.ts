import { chatGPTSignInPath, getChatGPTUser } from "../../chatgpt-auth";
import { loadAfterHoursWatchlist } from "../../../db/after-hours-watchlist";
import { AFTER_HOURS_WATCHLIST_ID, visibleAfterHoursStocks } from "../../../lib/watchlists";
import {
  initializeWatchlistState,
  readWatchlistState,
  updateWatchlistState,
  type StoredWatchlistState,
} from "../../../db/watchlists";

const NO_STORE_HEADERS = { "Cache-Control": "private, no-store, max-age=0" };

async function publicState(state: StoredWatchlistState) {
  const afterHours = await loadAfterHoursWatchlist().catch(() => null);
  return {
    exists: true,
    watchlists: state.watchlists.map(folder => folder.id === AFTER_HOURS_WATCHLIST_ID && afterHours
      ? {...folder, stocks: visibleAfterHoursStocks(folder, afterHours.stocks)} : folder),
    revision: state.revision,
    primaryDeviceId: state.primaryDeviceId,
    updatedByDeviceId: state.updatedByDeviceId,
    updatedByDeviceKind: state.updatedByDeviceKind,
    createdAt: state.createdAt,
    updatedAt: state.updatedAt,
  };
}

function authenticationRequired() {
  return Response.json(
    {
      ok: false,
      authRequired: true,
      signInUrl: chatGPTSignInPath("/?section=watchlist"),
      message: "請在各裝置使用同一個 ChatGPT 帳號登入，才能同步所有自選股名單。",
    },
    { status: 401, headers: NO_STORE_HEADERS },
  );
}

export async function GET() {
  const user = await getChatGPTUser();
  if (!user) return authenticationRequired();
  try {
    const state = await readWatchlistState(user.email);
    return Response.json(
      state ? { ok: true, ...await publicState(state) } : { ok: true, exists: false },
      { headers: NO_STORE_HEADERS },
    );
  } catch {
    return Response.json(
      { ok: false, message: "自選股雲端資料暫時無法讀取。" },
      { status: 503, headers: NO_STORE_HEADERS },
    );
  }
}

export async function POST(request: Request) {
  const user = await getChatGPTUser();
  if (!user) return authenticationRequired();

  let body: Record<string, unknown>;
  try {
    body = await request.json() as Record<string, unknown>;
  } catch {
    return Response.json({ ok: false, message: "同步資料格式不正確。" }, { status: 400, headers: NO_STORE_HEADERS });
  }

  const deviceId = typeof body.deviceId === "string" ? body.deviceId : "";
  const deviceKind = typeof body.deviceKind === "string" ? body.deviceKind : "";
  try {
    const existing = await readWatchlistState(user.email);
    if (!existing) {
      const initialized = await initializeWatchlistState({
        email: user.email,
        watchlists: body.watchlists,
        deviceId,
        deviceKind,
      });
      return Response.json({ ok: true, initialized: true, ...await publicState(initialized) }, { headers: NO_STORE_HEADERS });
    }

    const updated = await updateWatchlistState({
      email: user.email,
      operations: body.operations,
      deviceId,
      deviceKind,
    });
    if (!updated) {
      return Response.json({ ok: false, message: "找不到自選股雲端名單。" }, { status: 409, headers: NO_STORE_HEADERS });
    }
    return Response.json({ ok: true, ...await publicState(updated) }, { headers: NO_STORE_HEADERS });
  } catch (error) {
    const message = error instanceof Error && error.message.startsWith("invalid_watchlist")
      ? "裝置同步資料格式不正確。"
      : "自選股同步暫時失敗，系統會自動重試。";
    return Response.json({ ok: false, message }, { status: 503, headers: NO_STORE_HEADERS });
  }
}
