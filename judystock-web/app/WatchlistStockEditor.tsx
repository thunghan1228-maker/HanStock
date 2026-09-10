"use client";

import { useState } from "react";
import { AFTER_HOURS_WATCHLIST_ID, type WatchlistFolder, type WatchlistStock } from "../lib/watchlists";

export function WatchlistStockEditor({ stock, folderId, folders, onMove, onRemove }: {
  stock: WatchlistStock;
  folderId: string;
  folders: WatchlistFolder[];
  onMove: (stock: WatchlistStock, targetFolderId: string) => boolean;
  onRemove: (stock: WatchlistStock) => void;
}) {
  const [open, setOpen] = useState(false);
  const [targetId, setTargetId] = useState("");
  const targets = folders.filter(folder => folder.id !== folderId && folder.id !== AFTER_HOURS_WATCHLIST_ID);
  return <div className="watchlist-editor">
    <button type="button" className="watchlist-edit-button" aria-expanded={open}
      aria-controls={`watchlist-editor-${stock.ticker}`} onClick={() => setOpen(!open)}
      aria-label={`編輯 ${stock.ticker} ${stock.name}`}>編輯 ▾</button>
    {open && <div id={`watchlist-editor-${stock.ticker}`} className="watchlist-edit-panel">
      <label htmlFor={`watchlist-target-${stock.ticker}`}>變更自選名單</label>
      <select id={`watchlist-target-${stock.ticker}`} value={targetId} onChange={event => setTargetId(event.target.value)}>
        <option value="">選擇要移入的名單</option>
        {targets.map(folder => <option key={folder.id} value={folder.id}>{folder.name}（{folder.stocks.length} 檔）</option>)}
      </select>
      <div className="watchlist-edit-actions">
        <button type="button" disabled={!targetId} onClick={() => { if (onMove(stock, targetId)) setOpen(false); }}>移至名單</button>
        <button type="button" className="watchlist-remove" onClick={() => onRemove(stock)}>從本名單移除</button>
        <button type="button" onClick={() => setOpen(false)}>取消</button>
      </div>
    </div>}
  </div>;
}
