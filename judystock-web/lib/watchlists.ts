export const WATCHLIST_STORAGE_KEY = "hanstock-battle-watchlists-v1";
export const WATCHLIST_DEVICE_ID_KEY = "hanstock-watchlist-device-id-v1";
export const WATCHLIST_PENDING_OPERATIONS_KEY = "hanstock-watchlist-pending-operations-v1";
export const MANUAL_WATCHLIST_FOLDER_COUNT = 6;
export const WATCHLIST_FOLDER_COUNT = 7;
export const AFTER_HOURS_WATCHLIST_ID = "watchlist-7";
export const AFTER_HOURS_WATCHLIST_NAME = "盤後交易股票";

export type WatchlistDeviceKind = "desktop" | "iphone" | "ipad";

export type WatchlistStock = {
  ticker: string;
  name: string;
  group: string;
  price: string;
  change: string;
  forcePct?: number | null;
  signalTradeDate?: string;
  groupRank?: { group: string; rank: number; direction: "漲幅" | "跌幅"; change: string | null } | null;
};

export type WatchlistFolder = {
  id: string;
  name: string;
  stocks: WatchlistStock[];
  hiddenAfterHours?: { ticker: string; tradeDate: string }[];
};

export type WatchlistOperation =
  | { type: "addStock"; folderId: string; stock: WatchlistStock }
  | { type: "removeStock"; folderId: string; ticker: string; tradeDate?: string }
  | { type: "moveStock"; folderId: string; targetFolderId: string; ticker: string; stock?: WatchlistStock; tradeDate?: string }
  | { type: "renameFolder"; folderId: string; name: string };

export type WatchlistSyncState =
  | "connecting"
  | "syncing"
  | "synced"
  | "auth-required"
  | "offline"
  | "error";

export type WatchlistSyncStatus = {
  state: WatchlistSyncState;
  label: string;
  detail: string;
  updatedAt?: number;
};

export const DEFAULT_WATCHLISTS: WatchlistFolder[] = [
  {
    id: "watchlist-1",
    name: "自選股1",
    stocks: [
      { ticker: "2344", name: "華邦電", group: "記憶體", price: "169.50", change: "+7.91%" },
      { ticker: "2337", name: "旺宏", group: "記憶體", price: "42.10", change: "+5.78%" },
      { ticker: "2408", name: "南亞科", group: "記憶體", price: "186.00", change: "+6.45%" },
      { ticker: "3081", name: "聯亞", group: "光通訊", price: "392.50", change: "+5.21%" },
    ],
  },
  ...Array.from({ length: WATCHLIST_FOLDER_COUNT - 1 }, (_, index) => ({
    id: `watchlist-${index + 2}`,
    name: index + 2 === WATCHLIST_FOLDER_COUNT ? AFTER_HOURS_WATCHLIST_NAME : `自選股${index + 2}`,
    stocks: [],
  })),
];

function cleanText(value: unknown, fallback: string, maxLength: number) {
  if (typeof value !== "string") return fallback;
  const cleaned = value.replace(/\s+/g, " ").trim().slice(0, maxLength);
  return cleaned || fallback;
}

function normalizedFolderName(index: number, value: unknown) {
  const fallback = `自選股${index + 1}`;
  const name = cleanText(value, fallback, 16);
  if (index === 4 && name === "大戶力前20多") return fallback;
  if (index === 5 && name === "大戶力前20空") return fallback;
  return name;
}

export function normalizeWatchlistStock(value: unknown): WatchlistStock | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Partial<WatchlistStock>;
  const ticker = typeof raw.ticker === "string" ? raw.ticker.trim().toUpperCase() : "";
  if (!/^[0-9A-Z]{2,12}$/.test(ticker)) return null;
  return {
    ticker,
    name: cleanText(raw.name, ticker, 40),
    group: cleanText(raw.group, "—", 40),
    price: cleanText(raw.price, "—", 24),
    change: cleanText(raw.change, "—", 24),
    ...(typeof raw.forcePct === "number" && Number.isFinite(raw.forcePct) ? { forcePct: raw.forcePct } : {}),
    ...(typeof raw.signalTradeDate === "string" && /^\d{4}-\d{2}-\d{2}$/.test(raw.signalTradeDate) ? { signalTradeDate: raw.signalTradeDate } : {}),
    ...(raw.groupRank && Number.isInteger(raw.groupRank.rank) && raw.groupRank.rank > 0 &&
      ["漲幅", "跌幅"].includes(raw.groupRank.direction) ? { groupRank: raw.groupRank } : {}),
  };
}

export function cloneDefaultWatchlists(): WatchlistFolder[] {
  return DEFAULT_WATCHLISTS.map((folder) => ({
    ...folder,
    stocks: folder.stocks.map((stock) => ({ ...stock })),
  }));
}

export function normalizeWatchlists(value: unknown): WatchlistFolder[] {
  const source = Array.isArray(value) ? value : [];
  return Array.from({ length: WATCHLIST_FOLDER_COUNT }, (_, index) => {
    const raw = source[index] && typeof source[index] === "object"
      ? source[index] as Partial<WatchlistFolder>
      : null;
    const seen = new Set<string>();
    const stocks = (Array.isArray(raw?.stocks) ? raw.stocks : [])
      .map(normalizeWatchlistStock)
      .filter((stock): stock is WatchlistStock => {
        if (!stock || seen.has(stock.ticker)) return false;
        seen.add(stock.ticker);
        return true;
      })
      .slice(0, index === MANUAL_WATCHLIST_FOLDER_COUNT ? 10_000 : 300);
    return {
      id: `watchlist-${index + 1}`,
      name: index === MANUAL_WATCHLIST_FOLDER_COUNT ? AFTER_HOURS_WATCHLIST_NAME : normalizedFolderName(index, raw?.name),
      stocks,
      ...(index === MANUAL_WATCHLIST_FOLDER_COUNT && Array.isArray(raw?.hiddenAfterHours) ? {
        hiddenAfterHours: [...new Map(raw.hiddenAfterHours
          .filter(item => item && /^[0-9A-Z]{2,12}$/.test(item.ticker) && /^\d{4}-\d{2}-\d{2}$/.test(item.tradeDate))
          .sort((a, b) => a.tradeDate.localeCompare(b.tradeDate))
          .map(item => [item.ticker, {ticker: item.ticker, tradeDate: item.tradeDate}])).values()].slice(-10_000),
      } : {}),
    };
  });
}

export function visibleAfterHoursStocks(folder: WatchlistFolder, stocks: WatchlistStock[]) {
  const hidden = new Map(folder.hiddenAfterHours?.map(item => [item.ticker, item.tradeDate]));
  return stocks.filter(stock => !stock.signalTradeDate || hidden.get(stock.ticker) !== stock.signalTradeDate);
}

function removeFromFolder(folder: WatchlistFolder, ticker: string, tradeDate?: string): WatchlistFolder {
  return {...folder, stocks: folder.stocks.filter(stock => stock.ticker !== ticker),
    ...(folder.id === AFTER_HOURS_WATCHLIST_ID && tradeDate ? {
      hiddenAfterHours: [...(folder.hiddenAfterHours ?? []), {ticker, tradeDate}],
    } : {}),
  };
}

export function applyWatchlistOperations(
  current: WatchlistFolder[],
  operations: WatchlistOperation[],
): WatchlistFolder[] {
  let next = normalizeWatchlists(current);
  for (const operation of operations.slice(0, 500)) {
    const source = next.find(folder => folder.id === operation.folderId);
    if (!source) continue;
    const automatic = source.id === AFTER_HOURS_WATCHLIST_ID;
    const tradeDate = "tradeDate" in operation ? operation.tradeDate : undefined;
    if (automatic && (!tradeDate || !/^\d{4}-\d{2}-\d{2}$/.test(tradeDate))) continue;
    if (operation.type === "moveStock") {
      const target = next.find(folder => folder.id === operation.targetFolderId);
      if (!target || target.id === source.id || target.id === AFTER_HOURS_WATCHLIST_ID) continue;
      const ticker = operation.ticker.trim().toUpperCase();
      if (automatic && source.hiddenAfterHours?.some(item => item.ticker === ticker && item.tradeDate === tradeDate)) continue;
      const stock = automatic ? normalizeWatchlistStock(operation.stock) : source.stocks.find(item => item.ticker === ticker);
      if (!stock || stock.ticker !== ticker || (automatic && stock.signalTradeDate !== tradeDate)) continue;
      const alreadyInTarget = target.stocks.some(item => item.ticker === ticker);
      // Check the destination before removing the source, including concurrent edits.
      if (!alreadyInTarget && target.stocks.length >= 300) continue;
      next = next.map(folder => folder.id === source.id ? removeFromFolder(folder, ticker, tradeDate)
        : folder.id === target.id && !alreadyInTarget ? {...folder, stocks: [...folder.stocks, stock]} : folder);
      continue;
    }
    if (operation.type === "renameFolder") {
      if (automatic) continue;
      const name = cleanText(operation.name, "", 16);
      if (!name) continue;
      next = next.map((folder) => folder.id === operation.folderId ? { ...folder, name } : folder);
      continue;
    }
    if (operation.type === "removeStock") {
      const ticker = typeof operation.ticker === "string" ? operation.ticker.trim().toUpperCase() : "";
      next = next.map((folder) => folder.id === operation.folderId
        ? removeFromFolder(folder, ticker, tradeDate)
        : folder);
      continue;
    }
    if (automatic) continue;
    const stock = normalizeWatchlistStock(operation.stock);
    if (!stock) continue;
    next = next.map((folder) => folder.id === operation.folderId && !folder.stocks.some((item) => item.ticker === stock.ticker)
      ? { ...folder, stocks: [...folder.stocks, stock].slice(0, 300) }
      : folder);
  }
  return normalizeWatchlists(next);
}

export function diffWatchlists(
  previous: WatchlistFolder[],
  current: WatchlistFolder[],
): WatchlistOperation[] {
  const before = normalizeWatchlists(previous);
  const after = normalizeWatchlists(current);
  const operations: WatchlistOperation[] = [];
  for (let index = 0; index < MANUAL_WATCHLIST_FOLDER_COUNT; index += 1) {
    const oldFolder = before[index];
    const newFolder = after[index];
    if (oldFolder.name !== newFolder.name) {
      operations.push({ type: "renameFolder", folderId: newFolder.id, name: newFolder.name });
    }
    const oldTickers = new Set(oldFolder.stocks.map((stock) => stock.ticker));
    const newTickers = new Set(newFolder.stocks.map((stock) => stock.ticker));
    for (const stock of oldFolder.stocks) {
      if (!newTickers.has(stock.ticker)) {
        operations.push({ type: "removeStock", folderId: newFolder.id, ticker: stock.ticker });
      }
    }
    for (const stock of newFolder.stocks) {
      if (!oldTickers.has(stock.ticker)) {
        operations.push({ type: "addStock", folderId: newFolder.id, stock });
      }
    }
  }
  return operations;
}
