// Strategy source: thunghan1228-maker/taiwan-stock-groups server/longStrategy.ts
// Git blob: 63c7407793809179dc8b201c13f9170fd93e78e5. Conditions preserved; only local import paths adapted.
/**
 * 盤中 5 分鐘做多策略訊號引擎（純函式，可測試）
 *
 * 基準 K = 當日 09:00–09:05 的第一根 5 分 K（收在 09:05）。
 *
 * 條件一（前置過濾）：905 收盤價必須上漲且漲幅 < 6%（相對昨日收盤價），
 *   即 prevClose < 905close < prevClose * 1.06。不符則整檔當日不偵測做多訊號。
 *
 * ⑨ / ⑨N：盤中當根 5 分 K 最高價點到 905 高即觸發；跌回後可再計下一次。
 * 【5】：905 多方 gate 成立後，當根 high 同時點到 905 高與 5MA 即觸發；跌回後可再計下一次。
 * 兩者為獨立訊號，同一根 K 同時符合時可同時出現。
 *
 * 條件四（站上20MA）：前一根 5 分 K 收盤 < 20MA，本根最高價點到 20MA → 觸發「站上20MA」（紅色），
 *   當天可重複觸發並標示第 N 次；第一次另發「首次站上20MA」獨立提示（一天一次）。
 */

import { type Bar5m, isMa20Down, ma20Series, todayStartIndex } from "./shortStrategy.ts";

/** 台北時間 09:05 的 UTC ms（當日） */
function taipeiToday0905Ms(refTs: number): number {
  const dateStr = new Date(refTs).toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
  const [y, m, d] = dateStr.split("-").map(Number);
  // 台北 09:05 = UTC 01:05
  return Date.UTC(y, m - 1, d, 1, 5);
}

/** K 棒是否為 09:05 以後的已完成 K 棒（ts >= 台北 09:05 的 bar 起點） */
function isBarAfter0905(barTs: number, refTs: number): boolean {
  const cutoff = taipeiToday0905Ms(refTs);
  // 09:00~09:05 的 K 棒 ts 為台北 09:00（UTC 01:00），其完成時間為 09:05
  // 第二根 K 棒 ts 為台北 09:05（UTC 01:05），其完成時間為 09:10
  // 我們要求 barTs >= 09:05 的 bar 起點，代表該 bar 是 09:05~09:10 之後的 K 棒
  // 但「第一次過905高」的判斷基準是 09:00~09:05 K 棒（base），
  // 最早可觸發的是 09:05~09:10 這根 K 棒（ti=1 且 ts >= 台北 09:05）
  return barTs >= cutoff;
}

export type LongSignalKind =
  | "crossUp905"
  | "crossUp20ma"
  | "firstCrossUp20ma"
  | "crossUpPrevHigh"
  | "firstCross905High"
  | "ma520Up"
  | "ma520Down";

export interface LongSignal {
  kind: LongSignalKind;
  barIndex: number; // 觸發於第幾根 K（0-based，相對輸入序列）
  ts: number;
  price: number; // 觸發 K 棒收盤
  ma20Down: boolean | null;
  seq?: number; // crossUp905 / crossUp20ma 的第 N 次（1-based）
  label: string; // 顯示名稱（如「第一次站上5MA過905高」「站上20MA」）
}

const CN_ORD = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"];

/** 第 N 次的中文序數（超過十用數字） */
export function ordinalCn(n: number): string {
  return n >= 1 && n <= 10 ? CN_ORD[n - 1] : String(n);
}

/** 計算序列每根 K 的 5MA（不足 5 根為 null） */
export function ma5Series(bars: Bar5m[]): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= 5) sum -= bars[i - 5].close;
    out.push(i >= 4 ? Math.round((sum / 5) * 10000) / 10000 : null);
  }
  return out;
}

/** 條件一前置過濾：905 收盤上漲且漲幅 < 6%（相對昨收） */
export function passLongGate(baseClose: number, prevClose: number | null): boolean {
  if (prevClose === null || prevClose <= 0) return false;
  return baseClose > prevClose && baseClose < prevClose * 1.06;
}

/**
 * 回放整段序列，輸出當日所有做多訊號。
 * bars 需含跨日資料（供 5MA/20MA 計算與取得昨收），只對「今天」的 K 棒偵測。
 * prevDayClose：昨日收盤價（優先由呼叫端提供，如證交所日線；未提供則以序列中昨日最後一根收盤近似）。
 */
export function detectLongSignals(bars: Bar5m[], prevDayClose?: number | null): LongSignal[] {
  const signals: LongSignal[] = [];
  if (bars.length === 0) return signals;
  const start = todayStartIndex(bars);
  const todayBars = bars.slice(start);
  if (todayBars.length === 0) return signals;

  // 昨收：呼叫端提供，否則取今天之前的最後一根收盤
  const prevClose =
    prevDayClose !== undefined && prevDayClose !== null
      ? prevDayClose
      : start > 0
        ? bars[start - 1].close
        : null;

  // 昨日全天最高價：取序列中「今天以前」所有 K 棒中最後一個交易日的最高價
  let prevDayHigh: number | null = null;
  if (start > 0) {
    const prevDayStr = new Date(bars[start - 1].ts).toDateString();
    let h = -Infinity;
    for (let i = start - 1; i >= 0; i--) {
      if (new Date(bars[i].ts).toDateString() !== prevDayStr) break;
      if (bars[i].high > h) h = bars[i].high;
    }
    prevDayHigh = h > 0 ? h : null;
  }

  // 基準 K：今天 09:00–09:05 的 K 棒（跳過盤前試撮 K 棒）
  // 台北 09:00 = UTC 01:00，其 bar ts 應等於該日 UTC 01:00
  const dayDateStr = new Date(todayBars[0].ts).toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
  const [baseY, baseM, baseD] = dayDateStr.split("-").map(Number);
  const dayStart0900 = Date.UTC(baseY, baseM - 1, baseD, 1, 0); // 台北 09:00 = UTC 01:00
  // 找到第一根 ts >= 09:00 的 K 棒作為基準
  const baseIdx = todayBars.findIndex(b => b.ts >= dayStart0900);
  if (baseIdx < 0) return signals; // 今天沒有 09:00 以後的 K 棒，無法判斷
  const base = todayBars[baseIdx];
  const gateOk = passLongGate(base.close, prevClose); // 905 多方 gate：限制【5】與既有 20MA 系；純⑨不受此 gate 限制

  const baseHigh = base.high;
  const ma5 = ma5Series(bars);
  const ma20 = ma20Series(bars);

  const push = (kind: LongSignalKind, ti: number, label: string, seq?: number) => {
    const gi = start + ti;
    signals.push({
      kind,
      barIndex: gi,
      ts: bars[gi].ts,
      price: bars[gi].close,
      ma20Down: isMa20Down(ma20, gi),
      seq,
      label,
    });
  };

  // 條件二/三：第 N 次站上5MA過905高
  // above = 目前是否處於「已突破 905 高」的狀態（須跌回 905 高之下才能再次觸發）
  let above = false;
  let crossCount = 0; // ⑨：純 905 高收盤突破次數
  let crossUp20Count = 0; // 站上20MA 次數
  let crossPrevHighCount = 0; // 站上昨日高 次數
  // 【5】：站上 5MA 過 905 高。與⑨獨立計數；同一根 K 可同時成立。
  let five905Above = false;
  let five905FiredInCycle = false;
  let five905Count = 0;
  // 站上昨日高：盤中 high 點到即成立、low 跌回才重新武裝（首根若已點到視為已站上，不觸發）
  let abovePrevHigh =
    prevDayHigh !== null ? base.high >= prevDayHigh : false;
  // 五二零上/下（獨立訊號、不受條件一限制、一天可多次）：
  // 以當根開盤價作為穿越起點，最高／最低價盤中點到兩條均線即觸發。
  // 這可保留即時 high/low 語意，也避免價格整根維持在線上方／下方時重複發報。
  let ma520UpCount = 0;
  let ma520DownCount = 0;

  // 條件四：站上20MA（前一根收盤在下，本根 high 點到即成立）
  // 從 baseIdx+1 開始（跳過基準 K 及其前的盤前 K 棒）
  for (let ti = baseIdx + 1; ti < todayBars.length; ti++) {
    const gi = start + ti;
    const bar = todayBars[ti];

    // ── 第 N 次站上昨日高（獨立訊號，不受條件一限制，全日偵測） ──
    if (prevDayHigh !== null) {
      if (!abovePrevHigh && bar.high >= prevDayHigh) {
        abovePrevHigh = true;
        crossPrevHighCount += 1;
        push(
          "crossUpPrevHigh",
          ti,
          `第${crossPrevHighCount}次站上昨日高`,
          crossPrevHighCount,
        );
      } else if (abovePrevHigh && bar.low < prevDayHigh) {
        abovePrevHigh = false; // 收盤跌回昨日高之下，可再次觸發
      }
    }

    // ── ⑨ / ⑨N：第 N 次盤中 high 點到 905 高（獨立訊號，不要求 5MA） ──
    // 09:05 以後，當根 K 盤中即可判斷；low 跌回 905 高下方後重新武裝。
    if (isBarAfter0905(bar.ts, base.ts)) {
      if (!above && bar.high >= baseHigh) {
        above = true;
        crossCount += 1;
        push("crossUp905", ti, `第${ordinalCn(crossCount)}次過905高`, crossCount);
      } else if (above && bar.low < baseHigh) {
        above = false;
      }
    }

    // ── 五二零上/下（獨立訊號，不受條件一限制，全日偵測） ──
    {
      const m5 = ma5[gi];
      const m20 = ma20[gi];
      if (m5 !== null && m20 !== null) {
        const upLevel = Math.max(m5, m20);
        const downLevel = Math.min(m5, m20);
        const crossedUp = bar.open < upLevel && bar.high >= upLevel;
        const crossedDown = bar.open > downLevel && bar.low <= downLevel;
        if (crossedUp) {
          ma520UpCount += 1;
          push("ma520Up", ti, ma520UpCount === 1 ? "五二零上" : `第${ma520UpCount}次五二零上`, ma520UpCount);
        }
        if (crossedDown) {
          ma520DownCount += 1;
          push("ma520Down", ti, ma520DownCount === 1 ? "五二零下" : `第${ma520DownCount}次五二零下`, ma520DownCount);
        }
      }
    }

    if (!gateOk) continue; // gate 不符 → 不偵測【5】與既有 20MA 系；純⑨已在上方獨立完成判斷

    // ── 【5】：第 N 次「站上5MA過905高」 ──
    // 905 多方 gate 已由上方 gateOk 保證。
    // 新一輪由盤中 high 點到 905 高開始；同一根或後續 K 的 high 點到 5MA 即成立。
    // low 跌回 905 高下方後，才可重新計算下一次【5】。
    if (isBarAfter0905(bar.ts, base.ts)) {
      const touched905High = bar.high >= baseHigh;
      if (!five905Above && touched905High) {
        five905Above = true;
        five905FiredInCycle = false;
        const curMa5 = ma5[gi];
        if (!five905FiredInCycle && curMa5 !== null && bar.high >= curMa5) {
          five905FiredInCycle = true;
          five905Count += 1;
          push(
            "firstCross905High",
            ti,
            five905Count === 1
              ? "第1次站上5MA過905高"
              : `第${five905Count}次站上5MA過905高`,
            five905Count,
          );
        }
      } else if (five905Above && bar.low < baseHigh) {
        five905Above = false;
        five905FiredInCycle = false;
      }
    }

    // ── 站上20MA（可重複觸發） ──
    const curMa = ma20[gi];
    const prevMa = ma20[gi - 1];
    if (curMa !== null && prevMa !== null) {
      const prevBarClose = bars[gi - 1].close;
      if (prevBarClose < prevMa && bar.high >= curMa) {
        crossUp20Count += 1;
        push("crossUp20ma", ti, `第${crossUp20Count}次站上20MA`, crossUp20Count);
        if (crossUp20Count === 1) {
          push("firstCrossUp20ma", ti, "首次站上20MA");
        }
      }
    }
  }

  return signals;
}
