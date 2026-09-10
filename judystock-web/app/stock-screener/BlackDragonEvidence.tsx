export default function BlackDragonEvidence({ row }: { row: {
  date?: string; open: number; high: number; close: number;
  referenceHighs: Record<number, number | null>; newHighPeriods: number[];
} }) {
  const price = (value: number) => value.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
  return <span className="black-dragon-evidence">
    <b>符合日 {row.date ?? "—"}</b>
    <small>最高 {price(row.high)} ＞ 前五日高點 {price(row.referenceHighs[5]!)}</small>
    <small>開 {price(row.open)} → 收 {price(row.close)}（黑 K）</small>
    <small>創 {row.newHighPeriods.join("／")} 日新高</small>
  </span>;
}
