export type StockPrimaryGroupMap = Map<string, string>;

type StockGroupSource = Record<string, Array<[string, string]>>;

export const HANSTOCK_NON_OFFICIAL_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF"]);

export function parseStockPrimaryGroupMap(
  source: string,
  excludedGroups: ReadonlySet<string> = new Set(),
): StockPrimaryGroupMap {
  const normalized = source.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return new Map();

  try {
    const groups = JSON.parse(
      normalized
        .slice(start, end + 2)
        .replace(/\(/g, "[")
        .replace(/\)/g, "]")
        .replace(/'/g, '"')
        .replace(/,\s*([}\]])/g, "$1"),
    ) as StockGroupSource;
    const primaryGroups = new Map<string, string>();
    Object.entries(groups).forEach(([groupName, members]) => {
      if (excludedGroups.has(groupName)) return;
      members.forEach(([rawCode]) => {
        const code = String(rawCode ?? "").trim();
        if (/^\d{4}$/.test(code) && !primaryGroups.has(code)) primaryGroups.set(code, groupName);
      });
    });
    return primaryGroups;
  } catch {
    return new Map();
  }
}

/** HanStock 族群強弱排行共用的正式 67 族群股票白名單。 */
export function parseHanStockOfficialPrimaryGroupMap(source: string) {
  return parseStockPrimaryGroupMap(source, HANSTOCK_NON_OFFICIAL_GROUPS);
}
