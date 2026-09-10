// Strategy source: thunghan1228-maker/taiwan-stock-groups server/shortStrategy.ts
// Git blob: 30fc530c5741842b29e701a5b944831705c7ad74. Conditions preserved; only local import paths adapted.
/**
 * 盤中 5 分鐘放空策略訊號引擎（純函式，可測試）
 *
 * 基準 K = 當日 09:00–09:05 的第一根 5 分 K（收在 09:05）。
 * 條件（各自獨立觸發）：
 * 1. 破905D：成交價第一次低於基準 K 最低價（含開盤即在下方），當日一次【限 10:30 前】
 * 2. A8空：成交價第一次低於基準 K 中間值 (high+low)/2，當日一次【限 10:30 前】
 * 3. 注意12空：第一次進入「前高下方 5 檔內」，起算 10 分鐘未突破前高則成立；
 *    若 10 分鐘內突破則作廢並可重新偵測（直到成立）
 * 4. 12空：條件三成立後，再次回到「動態前高下方 5 檔內」，10 分鐘未突破則成立【限 10:30 前】；
 *    動態前高 = max(基準 K 高點, 條件三後反彈出現的新高)
 * 5. 20MA 方向：當前 20MA < 前一根 20MA → 下彎（↓）；> 前一根 → 上彎（↑）
 * 6. 跌破20MA：前一根在 20MA 之上，本根最低價點到或跌破 20MA（可重複觸發，標第 N 次；第一次另發「首次跌破20MA」提示，一天一次）
 * 7. 加強12空：條件三（注意12空）成立後，最低價點到或跌破 20MA 即觸發；
 *    可重複觸發、全日不限時（不受 10:30 限制）
 *
 * 以 5 分 K 序列回放近似「成交價」：每根 K 依序檢視，用 low/high/close 判斷
 * 觸價與突破。10 分鐘 = 2 根 5 分 K。
 */

export interface Bar5m {
  ts: number; // K 棒起始或結束時間 UTC ms（引擎只要求等距遞增）
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type ShortSignalKind =
  | "break905d"
  | "a8short"
  | "watch12short"
  | "short12"
  | "ma20turn"
  | "crossDown20ma"
  | "firstCrossDown20ma"
  | "enhanced12short";

export const SIGNAL_LABELS: Record<ShortSignalKind, string> = {
  break905d: "破905D",
  a8short: "A8空",
  watch12short: "注意12空",
  short12: "12空",
  ma20turn: "20MA轉向",
  crossDown20ma: "跌破20MA",
  firstCrossDown20ma: "首次跌破20MA",
  enhanced12short: "加強12空",
};

export interface ShortSignal {
  kind: ShortSignalKind;
  barIndex: number; // 觸發於第幾根 K（0-based，相對輸入序列）
  ts: number; // 觸發 K 棒的 ts
  price: number; // 觸發時參考價（該根 K 收盤）
  ma20Down: boolean | null; // 觸發當下 20MA 是否下彎（資料不足為 null）
  note?: string; // 附註（ma20turn: up/down；crossDown20ma: 第 N 次）
  seq?: number; // crossDown20ma 的第 N 次（1-based）
  label?: string; // 顯示名稱覆寫（如「第2次跌破20MA」）
}

/** 台股升降單位（tick）：依價位級距 */
export function tickSize(price: number): number {
  if (price < 10) return 0.01;
  if (price < 50) return 0.05;
  if (price < 100) return 0.1;
  if (price < 500) return 0.5;
  if (price < 1000) return 1;
  return 5;
}

/** n 檔的價差（以當前價位的 tick 計） */
export function ticksBelow(price: number, n: number): number {
  // 往下數 n 檔：每一步依「當前價位下方」的級距取 tick（處理跨級距邊界，如 100 → 99.9 用 0.1）
  let p = price;
  for (let i = 0; i < n; i++) {
    const t = tickSize(p - 1e-9 <= 0 ? p : p - 1e-9); // 用略低於 p 的價位決定 tick
    p = Math.round((p - t) * 10000) / 10000;
  }
  return p;
}

/** 計算序列每根 K 的 20MA（不足 20 根為 null） */
export function ma20Series(bars: Bar5m[]): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= 20) sum -= bars[i - 20].close;
    out.push(i >= 19 ? Math.round((sum / 20) * 10000) / 10000 : null);
  }
  return out;
}

/** 20MA 是否下彎：當前 20MA < 前一根 20MA（任一為 null 回傳 null） */
export function isMa20Down(ma: (number | null)[], i: number): boolean | null {
  if (i < 1) return null;
  const cur = ma[i];
  const prev = ma[i - 1];
  if (cur === null || prev === null) return null;
  if (cur === prev) return null; // 走平不視為上彎或下彎
  return cur < prev;
}

/** 從 5 分 K 序列中找出「今天」的 K 棒起點索引（依台北日期分組取最後一天） */
export function todayStartIndex(bars: Bar5m[]): number {
  if (bars.length === 0) return 0;
  const dayOf = (ts: number) =>
    new Date(ts).toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
  const lastDay = dayOf(bars[bars.length - 1].ts);
  for (let i = bars.length - 1; i >= 0; i--) {
    if (dayOf(bars[i].ts) !== lastDay) return i + 1;
  }
  return 0;
}

const TEN_MIN_BARS = 2; // 10 分鐘 = 2 根 5 分 K

/** 台北時間 10:30 的分鐘數（含）之前才允許 break905d / a8short / short12 */
const LATE_CUTOFF_MIN = 10 * 60 + 30;

/** 該 K 棒起始時間必須早於台北 10:30；10:25~10:30 為最後可觸發的完整 5 分 K */
export function isBefore1030(ts: number): boolean {
  const tw = new Date(new Date(ts).toLocaleString("en-US", { timeZone: "Asia/Taipei" }));
  return tw.getHours() * 60 + tw.getMinutes() < LATE_CUTOFF_MIN;
}

/**
 * 回放整段序列，輸出當日所有觸發的訊號。
 * bars 需含跨日資料（供 20MA 計算），只對「今天」的 K 棒偵測訊號。
 */
export function detectShortSignals(bars: Bar5m[]): ShortSignal[] {
  const signals: ShortSignal[] = [];
  if (bars.length === 0) return signals;
  const ma = ma20Series(bars);
  const start = todayStartIndex(bars);
  const todayBars = bars.slice(start);
  if (todayBars.length === 0) return signals;

  // 基準 K：今天 09:00–09:05 的 K 棒（跳過盤前試撮 K 棒）
  const dayDateStr = new Date(todayBars[0].ts).toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
  const [baseY, baseM, baseD] = dayDateStr.split("-").map(Number);
  const dayStart0900 = Date.UTC(baseY, baseM - 1, baseD, 1, 0); // 台北 09:00 = UTC 01:00
  const baseIdx = todayBars.findIndex(b => b.ts >= dayStart0900);
  if (baseIdx < 0) return signals; // 今天沒有 09:00 以後的 K 棒，無法判斷
  const base = todayBars[baseIdx];
  const baseLow = base.low;
  const baseMid = Math.round(((base.high + base.low) / 2) * 10000) / 10000;
  const baseHigh = base.high;

  let firedBreak905d = false;
  let firedA8 = false;

  // 條件三/四 狀態機
  // phase: "watch" = 等待條件三成立; "await4" = 條件三成立，等待離開區域;
  // "watch4" = 已離開，等待再測; "done" = 條件四成立
  let phase: "watch" | "await4" | "watch4" | "done" = "watch";
  let inZoneSince = -1; // 進入 5 檔區域的 K 棒索引（相對 todayBars），-1 = 不在區域
  let refHigh = baseHigh; // 動態前高（條件三成立後隨反彈新高更新）

  const pushSignal = (
    kind: ShortSignalKind,
    ti: number,
    note?: string,
    extra?: { seq?: number; label?: string },
  ) => {
    const gi = start + ti; // 全域索引
    signals.push({
      kind,
      barIndex: gi,
      ts: bars[gi].ts,
      price: bars[gi].close,
      ma20Down: isMa20Down(ma, gi),
      note,
      ...(extra ?? {}),
    });
  };

  // 20MA 轉向提示：偵測今天內方向改變（含第一次可判定）
  let lastDir: boolean | null = null;
  for (let ti = 0; ti < todayBars.length; ti++) {
    const gi = start + ti;
    const dir = isMa20Down(ma, gi);
    if (dir !== null && dir !== lastDir) {
      if (lastDir !== null) {
        pushSignal("ma20turn", ti, dir ? "down" : "up");
      }
      lastDir = dir;
    }
  }

  // 主回放（從基準 K 的下一根開始；盤中當根 K 的 high/low 點到即觸發）
  let crossDownCount = 0; // 跌破20MA 次數
  let watch12Fired = false; // 注意12空是否已成立（加強12空的前置條件）
  for (let ti = baseIdx + 1; ti < todayBars.length; ti++) {
    const bar = todayBars[ti];
    const before1030 = isBefore1030(bar.ts);

    // 條件一：低於基準 K 最低價
    if (!firedBreak905d && bar.low <= baseLow && before1030) {
      firedBreak905d = true;
      pushSignal("break905d", ti);
    }
    // 條件二：低於基準 K 中間值
    if (!firedA8 && bar.low <= baseMid && before1030) {
      firedA8 = true;
      pushSignal("a8short", ti);
    }

    // 條件六：跌破 20MA（當根最低價點到即成立；可重複觸發並標第 N 次；首次另發一天一次的獨立提示）
    {
      const gi = start + ti;
      const curMa = ma[gi];
      const prevMa = ma[gi - 1];
      if (curMa !== null && prevMa !== null) {
        const prevBarClose = bars[gi - 1].close;
        if (prevBarClose > prevMa && bar.low <= curMa) {
          crossDownCount += 1;
          pushSignal("crossDown20ma", ti, String(crossDownCount), {
            seq: crossDownCount,
            label: `第${crossDownCount}次跌破20MA`,
          });
          if (crossDownCount === 1) {
            pushSignal("firstCrossDown20ma", ti, undefined, { label: "首次跌破20MA" });
          }
          // 條件七：加強12空 = 注意12空成立後，當根 low 點到 20MA（可重複、全日不限時）
          if (watch12Fired) {
            pushSignal("enhanced12short", ti, undefined, { label: "加強12空" });
          }
        }
      }
    }

    // 條件三/四 狀態機
    if (phase !== "done") {
      const zoneLow = ticksBelow(refHigh, 5);
      const inZone = bar.high >= zoneLow && bar.high <= refHigh; // 觸及前高下方 5 檔內且未過前高
      const brokeHigh = bar.high >= refHigh;

      if (phase === "watch") {
        // 等待條件三：進入區域起算 10 分鐘
        if (brokeHigh) {
          // 突破 → 作廢並更新前高（09:05 高點被突破後以新高為準），重新偵測
          inZoneSince = -1;
          refHigh = bar.high;
        } else if (inZoneSince === -1) {
          if (inZone) inZoneSince = ti;
        } else if (ti - inZoneSince >= TEN_MIN_BARS) {
          phase = "await4";
          pushSignal("watch12short", ti);
          watch12Fired = true;
          inZoneSince = -1;
        }
      } else if (phase === "await4") {
        // 條件三剛成立：等待離開區域（或突破更新前高後視為離開）
        if (brokeHigh) {
          refHigh = bar.high;
          phase = "watch4";
        } else if (!inZone) {
          phase = "watch4";
        }
      } else if (phase === "watch4") {
        // 等待條件四：再次進入（動態前高）區域起算 10 分鐘
        if (brokeHigh) {
          refHigh = bar.high; // 動態前高更新，重新偵測
          inZoneSince = -1;
        } else if (inZoneSince === -1) {
          if (inZone) inZoneSince = ti;
        } else if (ti - inZoneSince >= TEN_MIN_BARS && before1030) {
          phase = "done";
          pushSignal("short12", ti);
        }
      }
    }
  }

  return signals;
}
