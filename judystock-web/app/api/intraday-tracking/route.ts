import { chatGPTSignInPath, getChatGPTUser } from "../../chatgpt-auth";
import {
  initializeIntradayTrackingState,
  readIntradayTrackingState,
  updateIntradayTrackingState,
} from "../../../db/intraday-tracking";
import type { StoredIntradayTrackingState } from "../../../lib/intraday-tracking";

const NO_STORE_HEADERS = { "Cache-Control": "private, no-store, max-age=0" };

function publicState(state: StoredIntradayTrackingState) {
  return {
    exists: true,
    settings: state.settings,
    revision: state.revision,
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
      signInUrl: chatGPTSignInPath("/?section=intraday-tracking"),
      message: "請用同一個 ChatGPT 帳號登入，才能同步所有裝置的盤中訊號追蹤。",
    },
    { status: 401, headers: NO_STORE_HEADERS },
  );
}

export async function GET() {
  const user = await getChatGPTUser();
  if (!user) return authenticationRequired();
  try {
    const state = await readIntradayTrackingState(user.email);
    return Response.json(
      state ? { ok: true, ...publicState(state) } : { ok: true, exists: false },
      { headers: NO_STORE_HEADERS },
    );
  } catch {
    return Response.json(
      { ok: false, message: "盤中追蹤雲端資料暫時無法讀取。" },
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
    const existing = await readIntradayTrackingState(user.email);
    const state = existing
      ? await updateIntradayTrackingState({
          email: user.email,
          settings: body.settings,
          deviceId,
          deviceKind,
        })
      : await initializeIntradayTrackingState({
          email: user.email,
          settings: body.settings,
          deviceId,
          deviceKind,
        });
    if (!state) {
      return Response.json({ ok: false, message: "找不到盤中追蹤雲端設定。" }, { status: 409, headers: NO_STORE_HEADERS });
    }
    return Response.json(
      { ok: true, initialized: !existing, ...publicState(state) },
      { headers: NO_STORE_HEADERS },
    );
  } catch (error) {
    const message = error instanceof Error && error.message.startsWith("invalid_intraday_tracking")
      ? "裝置同步資料格式不正確。"
      : "盤中追蹤同步暫時失敗，系統會自動重試。";
    return Response.json({ ok: false, message }, { status: 503, headers: NO_STORE_HEADERS });
  }
}
