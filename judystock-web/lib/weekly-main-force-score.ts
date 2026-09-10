/**
 * Weekly main-force confirmation deliberately stays separate from the existing
 * daily institutional score. Amounts, branch flows and TDCC ownership ratios
 * must first be normalized into comparable -100..100 market-relative scores.
 */
export type WeeklyMainForceComponents = {
  institutional: number | null;
  brokerBranch: number | null;
  tdccLargeHolder: number | null;
};

export type WeeklyMainForceAssessment = {
  score: number | null;
  availableComponentCount: number;
  positiveComponentCount: number;
  negativeComponentCount: number;
  label: "待接入" | "觀察" | "籌碼增強" | "主力同步集中" | "籌碼轉弱" | "主力同步鬆動";
};

export type WeeklyMainForceRow = WeeklyMainForceComponents & WeeklyMainForceAssessment & { ticker: string };

const weights = { institutional: 0.35, brokerBranch: 0.4, tdccLargeHolder: 0.25 } as const;

function rounded(value: number) { return Math.round(value * 10) / 10; }

export function assessWeeklyMainForce(components: WeeklyMainForceComponents): WeeklyMainForceAssessment {
  const entries = (Object.keys(weights) as Array<keyof typeof weights>)
    .flatMap((key) => components[key] === null ? [] : [{ value: components[key] as number, weight: weights[key] }]);
  if (entries.length < 3) {
    return { score: null, availableComponentCount: entries.length, positiveComponentCount: entries.filter((x) => x.value >= 20).length, negativeComponentCount: entries.filter((x) => x.value <= -20).length, label: "待接入" };
  }
  const score = rounded(entries.reduce((total, item) => total + item.value * item.weight, 0));
  const positive = entries.filter((item) => item.value >= 20).length;
  const negative = entries.filter((item) => item.value <= -20).length;
  const label = positive === 3 ? "主力同步集中"
    : negative === 3 ? "主力同步鬆動"
    : positive >= 2 && score >= 20 ? "籌碼增強"
    : negative >= 2 && score <= -20 ? "籌碼轉弱"
    : "觀察";
  return { score, availableComponentCount: 3, positiveComponentCount: positive, negativeComponentCount: negative, label };
}

/** Merge all available weekly source scales by ticker without ever substituting one source for another. */
export function buildWeeklyMainForceRows(
  institutionalByTicker: Map<string, number>,
  tdccByTicker: Map<string, number>,
  brokerByTicker = new Map<string, number>(),
) {
  const tickers = new Set([...institutionalByTicker.keys(), ...tdccByTicker.keys(), ...brokerByTicker.keys()]);
  return [...tickers].map((ticker) => {
    const components = {
      institutional: institutionalByTicker.get(ticker) ?? null,
      brokerBranch: brokerByTicker.get(ticker) ?? null,
      tdccLargeHolder: tdccByTicker.get(ticker) ?? null,
    };
    return { ticker, ...components, ...assessWeeklyMainForce(components) } satisfies WeeklyMainForceRow;
  });
}
