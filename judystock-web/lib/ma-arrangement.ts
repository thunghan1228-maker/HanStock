export const MA_ARRANGEMENT_MODEL_VERSION = "ma-confirm-v2";

export type MaValues = Record<number, number | null>;

export type MaArrangementResult = {
  score: number;
  bullConfirmed: boolean;
  bearConfirmed: boolean;
  label: string;
};

export function labelMaArrangement(score: number, bullConfirmed: boolean, bearConfirmed: boolean) {
  if (score === 15 && bullConfirmed) return "完整多頭";
  if (score >= 12 && bullConfirmed) return "強多頭";
  if (score >= 12) return "多頭排列／短線回檔";
  if (score >= 10) return "多頭排列";
  if (score <= 5 && !bearConfirmed) return "空頭排列／短線反彈";
  if (score === 0) return "完整空頭";
  if (score <= 2) return "強空頭";
  if (score <= 5) return "空頭";
  return "均線整理";
}

export function calculateMaArrangement({
  close,
  maValues,
  previousMaValues,
}: {
  close: number;
  maValues: MaValues;
  previousMaValues: MaValues;
}): MaArrangementResult | null {
  const periods = [5, 10, 20, 60, 120, 240] as const;
  const values = periods.map((period) => maValues[period]);
  if (values.some((value) => value === null || !Number.isFinite(value))) return null;

  let score = 0;
  for (let fast = 0; fast < values.length; fast += 1) {
    for (let slow = fast + 1; slow < values.length; slow += 1) {
      if (values[fast]! > values[slow]!) score += 1;
    }
  }

  const shortPeriods = [5, 10, 20] as const;
  const shortTrendReady = shortPeriods.every((period) => {
    const current = maValues[period];
    const previous = previousMaValues[period];
    return current !== null && previous !== null && Number.isFinite(current) && Number.isFinite(previous);
  });
  const shortMAsRising = shortTrendReady && shortPeriods.every((period) => maValues[period]! >= previousMaValues[period]!);
  const shortMAsFalling = shortTrendReady && shortPeriods.every((period) => maValues[period]! <= previousMaValues[period]!);
  const bullConfirmed = score >= 12 && close >= maValues[5]! && shortMAsRising;
  const bearConfirmed = score <= 5 && close <= maValues[5]! && shortMAsFalling;

  return { score, bullConfirmed, bearConfirmed, label: labelMaArrangement(score, bullConfirmed, bearConfirmed) };
}
