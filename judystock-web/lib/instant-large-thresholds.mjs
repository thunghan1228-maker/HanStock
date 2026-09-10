export const INSTANT_LARGE_MIN_LOTS = 100;
export const INSTANT_LARGE_MIN_AMOUNT = 30_000_000;
export const INSTANT_LARGE_EXTRA_LOTS = 300;
export const INSTANT_LARGE_EXTRA_AMOUNT = 50_000_000;

function numberFromText(value) {
  const parsed = Number(String(value ?? "").replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseInstantLargeOrderNote(note) {
  const text = String(note ?? "");
  const lotsMatch = text.match(/合計\s*([\d,.]+)\s*張/u);
  const amountMatch = text.match(/約\s*([\d,.]+)\s*(億|萬|元)/u);
  const lots = numberFromText(lotsMatch?.[1]);
  const amountValue = numberFromText(amountMatch?.[1]);
  if (lots === null || amountValue === null || !amountMatch) return null;
  const amount = amountValue * (amountMatch[2] === "億" ? 100_000_000 : amountMatch[2] === "萬" ? 10_000 : 1);
  return { lots, amount };
}

export function normalizeInstantLargeOrderSignal(signal) {
  if (!signal || (signal.kind !== "instantLargeBuy" && signal.kind !== "instantLargeSell")) return null;
  const facts = parseInstantLargeOrderNote(signal.note);
  if (!facts) return null;
  const passesGeneral = facts.lots >= INSTANT_LARGE_MIN_LOTS || facts.amount >= INSTANT_LARGE_MIN_AMOUNT;
  if (!passesGeneral) return null;
  const passesExtra = facts.lots >= INSTANT_LARGE_EXTRA_LOTS || facts.amount >= INSTANT_LARGE_EXTRA_AMOUNT;
  const isBuy = signal.kind === "instantLargeBuy";
  return {
    ...signal,
    label: passesExtra
      ? (isBuy ? "瞬間特大買單敲進" : "瞬間特大賣單倒出")
      : (isBuy ? "瞬間大單連續敲進" : "瞬間大單連續倒出"),
  };
}
