import type { RankingForce } from "../lib/ranking-intraday-force";

export default function RankingForceCell({ value, group = false }: { value?: RankingForce; group?: boolean }) {
  const pct = value?.forcePct;
  const available = typeof pct === "number" && Number.isFinite(pct);
  const time = value?.barTs ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value.barTs)) : "";
  const coverage = value ? `${value.availableCount}/${value.totalCount} 檔` : "讀取中";
  const sampled = value ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value.sampledAt)) : "";
  const detail = `${group ? "全部成員盤中大戶力的算術平均" : "當次篩選時的盤中累計大戶力"}${value ? `｜篩選時間 ${sampled}｜資料日 ${value.tradeDate}${time ? ` ${time}` : ""}｜${coverage}` : ""}`;
  return <span className={`ranking-intraday-force ${available ? pct > 0 ? "positive" : pct < 0 ? "negative" : "neutral" : "pending"}`} title={detail} aria-label={`盤中大戶力 ${available ? `${pct.toFixed(2)}%` : `待補 ${coverage}`}`}>
    <strong>{available ? `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%` : "待補"}</strong>
    {group && <small>{coverage}</small>}
  </span>;
}
