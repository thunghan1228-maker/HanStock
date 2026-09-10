import { loadAfterHoursWatchlist } from "../../../../db/after-hours-watchlist";

// The seventh folder is a shared view of the permanently collected trade events.
// It updates even if a user's devices were closed; personal folders are untouched.
export async function GET() {
  try {
    return Response.json({ok: true, ...await loadAfterHoursWatchlist()},
      {headers: {"Cache-Control": "no-store"}});
  } catch {
    return Response.json({ok: false, message: "盤後成交名單暫時無法更新，稍後會自動重試。"},
      {status: 503, headers: {"Cache-Control": "no-store"}});
  }
}
