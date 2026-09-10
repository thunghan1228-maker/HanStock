import stockGroupsSource from "../../../data/stock_groups.py?raw";

type StockMember = [string, string];
type GroupMap = Record<string, StockMember[]>;

const EXCLUDED_GROUPS = new Set(["股期標的", "小型股票期貨", "ETF", "其他"]);

function parseStockGroups(source: string): GroupMap {
  const normalized = source.replace(/\r\n?/g, "\n");
  const assignment = normalized.indexOf("STOCK_GROUPS");
  const start = normalized.indexOf("{", assignment);
  const end = normalized.indexOf("\n}\n", start);
  if (assignment < 0 || start < 0 || end < 0) return {};

  const dictionary = normalized
    .slice(start, end + 2)
    .replace(/\(/g, "[")
    .replace(/\)/g, "]")
    .replace(/'/g, '"')
    .replace(/,\s*([}\]])/g, "$1");

  try {
    return JSON.parse(dictionary) as GroupMap;
  } catch {
    return {};
  }
}

export async function GET() {
  const groups = Object.entries(parseStockGroups(stockGroupsSource))
    .filter(([name]) => !EXCLUDED_GROUPS.has(name))
    .map(([name, members]) => {
      const uniqueMembers = new Map(members.flatMap(([rawCode, rawName]) => {
        const code = rawCode.trim();
        const stockName = rawName.trim().replace(/\*$/, "");
        return /^\d{4}$/.test(code) && !code.startsWith("00") && stockName ? [[code, stockName] as const] : [];
      }));
      return {
        name,
        codes: [...uniqueMembers.keys()],
        members: [...uniqueMembers].map(([code, stockName]) => ({ code, name: stockName })),
      };
    })
    // 一般族群即使只有 2 檔成分股也必須納入；目前共有 67 個一般族群。
    // 舊版用「至少 3 檔」誤刪了宇宙、黃金、鋰電池，因此畫面只剩 65 組。
    .filter((group) => group.codes.length > 0);

  return Response.json(
    { ok: groups.length >= 60, groups, groupCount: groups.length },
    { headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400" } },
  );
}
