"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import StockTradingBadges from "../StockTradingBadges";

type GridStock = { ticker: string; name: string };
type SearchStock = GridStock & { group?: string };
type PanelState = "open" | "minimized" | "closed";

const GRID_STORAGE_KEY = "hanstock-four-kline-grid-v1";
const DEFAULT_STOCKS: GridStock[] = [
  { ticker: "2330", name: "台積電" },
  { ticker: "2317", name: "鴻海" },
  { ticker: "2454", name: "聯發科" },
  { ticker: "2382", name: "廣達" },
];

function validStock(value: unknown): value is GridStock {
  const stock = value as GridStock | null;
  return Boolean(stock && /^[0-9A-Z]{2,12}$/.test(stock.ticker) && stock.name?.trim());
}

function GridKlineCard({
  index,
  stock,
  frameRef,
  onFrameLoad,
  onSelect,
  onActivate,
  keyboardActive,
  panelState,
  maximized,
  onMinimize,
  onMaximize,
  onClose,
}: {
  index: number;
  stock: GridStock;
  frameRef: (node: HTMLIFrameElement | null) => void;
  onFrameLoad: () => void;
  onSelect: (stock: GridStock) => void;
  onActivate: () => void;
  keyboardActive: boolean;
  panelState: PanelState;
  maximized: boolean;
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(`${stock.ticker} ${stock.name}`);
  const [results, setResults] = useState<SearchStock[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => setQuery(`${stock.ticker} ${stock.name}`), [stock]);
  useEffect(() => {
    const keyword = query.trim();
    if (!keyword || keyword === `${stock.ticker} ${stock.name}`) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      fetch(`/api/stock-search?q=${encodeURIComponent(keyword)}`, { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() : Promise.reject(new Error("search-failed")))
        .then((payload: { stocks?: SearchStock[] }) => setResults(Array.isArray(payload.stocks) ? payload.stocks : []))
        .catch((error: Error) => { if (error.name !== "AbortError") setResults([]); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 160);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, stock]);

  const choose = (next: GridStock) => {
    onSelect(next);
    setResults([]);
    setQuery(`${next.ticker} ${next.name}`);
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const keyword = query.trim();
    if (!keyword) return;
    const normalized = keyword.toLowerCase();
    const exact = results.find((item) => item.ticker.toLowerCase() === normalized || item.name.toLowerCase() === normalized);
    if (exact ?? results[0]) {
      choose(exact ?? results[0]);
      return;
    }
    try {
      const response = await fetch(`/api/stock-search?q=${encodeURIComponent(keyword)}`, { cache: "no-store" });
      const payload = await response.json() as { stocks?: SearchStock[] };
      const candidate = Array.isArray(payload.stocks) ? payload.stocks[0] : undefined;
      if (candidate) choose(candidate);
      else if (/^[0-9A-Z]{2,12}$/i.test(keyword)) choose({ ticker: keyword.toUpperCase(), name: keyword.toUpperCase() });
    } catch {}
  };

  const frameUrl = `/api/kline-embed/${encodeURIComponent(stock.ticker)}?interval=5m&name=${encodeURIComponent(stock.name)}&view=grid&uiRev=20260905-kline-signal-toggle-v35`;
  return <article className={`four-kline-card${panelState !== "open" ? ` is-${panelState}` : ""}${maximized ? " is-maximized" : ""}${keyboardActive ? " is-keyboard-active" : ""}`} aria-hidden={panelState !== "open" || undefined} onPointerDown={onActivate}>
    <header>
      <span className="four-kline-number">{index + 1}</span>
      <strong>{stock.ticker} {stock.name}</strong>
      <StockTradingBadges ticker={stock.ticker} compact />
      <em>五分 K</em>
      <form onSubmit={submit} role="search" aria-label={`第 ${index + 1} 格股票搜尋`}>
        <input value={query} onChange={(event) => setQuery(event.target.value)} onFocus={(event) => event.currentTarget.select()} placeholder="輸入代號或名稱" autoComplete="off" />
        <button type="submit">更換</button>
        {(loading || results.length > 0) && <div className="four-kline-search-results">
          {loading && results.length === 0 ? <p>搜尋中…</p> : results.map((item) => <button type="button" key={item.ticker} onClick={() => choose(item)}><b>{item.ticker}</b><span>{item.name}</span><small>{item.group ?? ""}</small><StockTradingBadges ticker={item.ticker} compact /></button>)}
        </div>}
      </form>
      <nav className="four-kline-window-controls" aria-label={`第 ${index + 1} 格視窗控制`}>
        <button type="button" className="minimize" onClick={onMinimize} title="隱藏到上方" aria-label={`隱藏第 ${index + 1} 格`}>—</button>
        <button type="button" className="maximize" onClick={onMaximize} title={maximized ? "還原四格" : "放大這一格"} aria-label={maximized ? `還原第 ${index + 1} 格` : `放大第 ${index + 1} 格`}>{maximized ? "❐" : "□"}</button>
        <button type="button" className="close" onClick={onClose} title="關閉這一格" aria-label={`關閉第 ${index + 1} 格`}>×</button>
      </nav>
    </header>
    <iframe ref={frameRef} src={frameUrl} title={`第 ${index + 1} 格 ${stock.ticker} ${stock.name} 五分鐘 K 線`} loading="eager" onLoad={onFrameLoad} />
  </article>;
}

export default function FourKlineGridPage() {
  const [stocks, setStocks] = useState<GridStock[]>(DEFAULT_STOCKS);
  const [ready, setReady] = useState(false);
  const [panelStates, setPanelStates] = useState<PanelState[]>(["open", "open", "open", "open"]);
  const [maximizedIndex, setMaximizedIndex] = useState<number | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [keyboardActive, setKeyboardActive] = useState(false);
  const [quickCode, setQuickCode] = useState("");
  const [quickStatus, setQuickStatus] = useState<"idle" | "loading" | "error">("idle");
  const framesRef = useRef<Array<HTMLIFrameElement | null>>([null, null, null, null]);
  const quickKeyHandlerRef = useRef<(event: KeyboardEvent) => void>(() => undefined);
  const boundFrameDocumentsRef = useRef(new WeakSet<Document>());

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let initial = DEFAULT_STOCKS;
    try {
      const stored = JSON.parse(window.localStorage.getItem(GRID_STORAGE_KEY) ?? "null") as unknown;
      if (Array.isArray(stored) && stored.length === 4 && stored.every(validStock)) initial = stored;
    } catch {}
    const ticker = (params.get("ticker") ?? "").trim().toUpperCase();
    const name = (params.get("name") ?? "").trim();
    if (/^[0-9A-Z]{2,12}$/.test(ticker)) initial = [{ ticker, name: name || ticker }, ...initial.slice(1)];
    setStocks(initial);
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    try { window.localStorage.setItem(GRID_STORAGE_KEY, JSON.stringify(stocks)); } catch {}
  }, [ready, stocks]);

  const updateStock = useCallback((index: number, stock: GridStock) => {
    setStocks((current) => current.map((item, itemIndex) => itemIndex === index ? stock : item));
  }, []);

  const setPanelState = useCallback((index: number, state: PanelState) => {
    setPanelStates((current) => current.map((item, itemIndex) => itemIndex === index ? state : item));
    setMaximizedIndex((current) => current === index && state !== "open" ? null : current);
  }, []);

  const toggleMaximize = useCallback((index: number) => {
    setPanelStates((current) => current.map((item, itemIndex) => itemIndex === index ? "open" : item));
    setMaximizedIndex((current) => current === index ? null : index);
  }, []);

  const restoreAll = useCallback(() => {
    setPanelStates(["open", "open", "open", "open"]);
    setMaximizedIndex(null);
  }, []);

  const resolveQuickCode = useCallback(async (code: string, index: number) => {
    setQuickStatus("loading");
    try {
      const response = await fetch(`/api/stock-search?q=${encodeURIComponent(code)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("search-failed");
      const payload = await response.json() as { stocks?: SearchStock[] };
      const results = Array.isArray(payload.stocks) ? payload.stocks : [];
      const normalized = code.toUpperCase();
      const candidate = results.find((item) => item.ticker.toUpperCase() === normalized) ?? results[0]
        ?? (/^[0-9A-Z]{2,12}$/.test(normalized) ? { ticker: normalized, name: normalized } : undefined);
      if (!candidate) throw new Error("stock-not-found");
      updateStock(index, { ticker: candidate.ticker.toUpperCase(), name: candidate.name });
      setPanelState(index, "open");
      setQuickCode("");
      setQuickStatus("idle");
    } catch {
      setQuickStatus("error");
    }
  }, [setPanelState, updateStock]);

  const handleQuickKey = useCallback((event: KeyboardEvent) => {
    if (!keyboardActive || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const target = event.target as HTMLElement | null;
    if (target?.matches("input, textarea, select, [contenteditable=true]")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setQuickCode("");
      setQuickStatus("idle");
      setKeyboardActive(false);
      return;
    }
    if (event.key === "Backspace") {
      if (!quickCode) return;
      event.preventDefault();
      setQuickCode((current) => current.slice(0, -1));
      setQuickStatus("idle");
      return;
    }
    if (event.key === "Enter") {
      if (!quickCode || quickStatus === "loading") return;
      event.preventDefault();
      void resolveQuickCode(quickCode, activeIndex);
      return;
    }
    if (event.key.length !== 1 || !/[0-9a-z]/i.test(event.key)) return;
    event.preventDefault();
    setQuickCode((current) => `${current}${event.key.toUpperCase()}`.slice(0, 12));
    setQuickStatus("idle");
  }, [activeIndex, keyboardActive, quickCode, quickStatus, resolveQuickCode]);

  useEffect(() => {
    quickKeyHandlerRef.current = handleQuickKey;
  }, [handleQuickKey]);

  useEffect(() => {
    const forwardKey = (event: KeyboardEvent) => quickKeyHandlerRef.current(event);
    window.addEventListener("keydown", forwardKey);
    return () => window.removeEventListener("keydown", forwardKey);
  }, []);

  const bindFrameKeyboard = useCallback((index: number) => {
    try {
      const frameDocument = framesRef.current[index]?.contentDocument;
      if (!frameDocument || boundFrameDocumentsRef.current.has(frameDocument)) return;
      boundFrameDocumentsRef.current.add(frameDocument);
      frameDocument.addEventListener("pointerdown", () => {
        setActiveIndex(index);
        setKeyboardActive(true);
        setQuickCode("");
        setQuickStatus("idle");
      }, { passive: true });
      frameDocument.addEventListener("keydown", (event) => quickKeyHandlerRef.current(event));
    } catch {}
  }, []);

  useEffect(() => {
    const receive = (event: MessageEvent<unknown>) => {
      if (event.origin !== window.location.origin) return;
      const index = framesRef.current.findIndex((frame) => frame?.contentWindow === event.source);
      if (index < 0) return;
      const message = event.data as { type?: unknown; ticker?: unknown; name?: unknown } | null;
      if (message?.type !== "hanstock-battle-open-kline") return;
      const ticker = typeof message.ticker === "string" ? message.ticker.trim().toUpperCase() : "";
      if (!/^[0-9A-Z]{2,12}$/.test(ticker)) return;
      const name = typeof message.name === "string" && message.name.trim() ? message.name.trim().slice(0, 40) : ticker;
      updateStock(index, { ticker, name });
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [updateStock]);

  const visibleCount = panelStates.filter((state) => state === "open").length;
  const alteredLayout = maximizedIndex !== null || panelStates.some((state) => state !== "open");

  return <main className="four-kline-shell">
    {keyboardActive && <aside className={`kline-quick-switch${quickStatus === "error" ? " is-error" : ""}`} aria-live="polite">
      <span>第 {activeIndex + 1} 格 · 鍵盤換股</span>
      <form role="search" onSubmit={(event) => {
        event.preventDefault();
        if (!quickCode.trim() || quickStatus === "loading") return;
        void resolveQuickCode(quickCode.trim(), activeIndex);
      }}>
        <input
          autoFocus
          type="search"
          value={quickCode}
          onChange={(event) => { setQuickCode(event.target.value.slice(0, 40)); setQuickStatus("idle"); }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            setQuickCode("");
            setQuickStatus("idle");
            setKeyboardActive(false);
          }}
          placeholder="直接輸入股票代號或名稱"
          aria-label={`第 ${activeIndex + 1} 格直接輸入股票代號或名稱`}
          autoComplete="off"
          enterKeyHint="go"
        />
        <button type="submit" disabled={!quickCode.trim() || quickStatus === "loading"}>{quickStatus === "loading" ? "搜尋中" : "換股"}</button>
      </form>
      <small>{quickStatus === "loading" ? "搜尋中…" : quickStatus === "error" ? "找不到股票；可修改代號再按 Enter" : "Enter 確認 · Backspace 刪除 · Esc 取消"}</small>
    </aside>}
    <header className="four-kline-toolbar">
      <button className="four-kline-back" onClick={() => window.history.length > 1 ? window.history.back() : window.location.assign("/")} aria-label="返回上一頁">‹</button>
      <div><strong>四格 K 線</strong><span>四個固定視窗 · 預設五分鐘 K · 自動記住四檔股票</span></div>
      {alteredLayout && <nav className="four-kline-dock" aria-label="已隱藏或關閉的 K 線">
        {panelStates.map((state, index) => state === "open" ? null : <button key={index} type="button" className={state} onClick={() => setPanelState(index, "open")} title={`恢復 ${stocks[index].ticker} ${stocks[index].name}`}><b>{index + 1}</b><span>{state === "minimized" ? "已隱藏" : "已關閉"}</span></button>)}
        <button type="button" className="restore-all" onClick={restoreAll}>恢復四格</button>
      </nav>}
      <button className="four-kline-refresh" onClick={() => window.location.reload()}>↻ 全部更新</button>
    </header>
    <section className={`four-kline-grid visible-${visibleCount}${maximizedIndex !== null ? " has-maximized" : ""}`} aria-label="四格五分鐘 K 線">
      {stocks.map((stock, index) => <GridKlineCard key={`${index}:${stock.ticker}`} index={index} stock={stock} frameRef={(node) => { framesRef.current[index] = node; }} onFrameLoad={() => bindFrameKeyboard(index)} onSelect={(next) => updateStock(index, next)} onActivate={() => { setActiveIndex(index); setKeyboardActive(true); setQuickCode(""); setQuickStatus("idle"); }} keyboardActive={keyboardActive && activeIndex === index} panelState={panelStates[index]} maximized={maximizedIndex === index} onMinimize={() => setPanelState(index, "minimized")} onMaximize={() => toggleMaximize(index)} onClose={() => setPanelState(index, "closed")} />)}
      {visibleCount === 0 && <div className="four-kline-empty"><strong>四個 K 線視窗都已隱藏</strong><button type="button" onClick={restoreAll}>恢復四格 K 線</button></div>}
    </section>
  </main>;
}
