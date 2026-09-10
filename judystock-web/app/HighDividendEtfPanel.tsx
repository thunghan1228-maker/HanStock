"use client";

import { useEffect, useMemo, useState } from "react";

type EtfRow = {
  code: string;
  name: string;
  exchange: "twse" | "tpex";
  category: string;
  annualYield: number;
  distributionMonths: number[];
  nextDistributionDate: string | null;
  nextDistributionAmount: number | null;
  price: number | null;
};
type Payload = { ok?: boolean; updatedAt?: string; universeCount?: number; rows?: EtfRow[]; message?: string | null };
type Props = { onOpenKline: (code: string, name: string) => void };

const STORAGE_KEY = "hanstock.highDividendEtf.excluded.v1";

function monthsLabel(months: number[]) {
  if (months.length >= 10) return "每月配息";
  if (!months.length) return "待資料";
  return `${months.join("、")} 月`;
}

function taipeiTime(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}

function nextDistributionLabel(row: EtfRow) {
  if (!row.nextDistributionDate) return <><b>待公告</b><small>日期與金額</small></>;
  return <><b>{row.nextDistributionDate.replaceAll("-", "/")}</b><small>{row.nextDistributionAmount === null ? "金額待公告" : `每單位 ${row.nextDistributionAmount.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")} 元`}</small></>;
}

export default function HighDividendEtfPanel({ onOpenKline }: Props) {
  const [rows, setRows] = useState<EtfRow[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [updatedAt, setUpdatedAt] = useState<string>();
  const [universeCount, setUniverseCount] = useState(0);
  const [sort, setSort] = useState<"desc" | "asc">("desc");
  const [editing, setEditing] = useState(false);
  const [showExcluded, setShowExcluded] = useState(false);
  const [excluded, setExcluded] = useState<string[]>([]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
      if (Array.isArray(saved)) setExcluded(saved.filter((code) => typeof code === "string"));
    } catch { /* ignore invalid device preference */ }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let loading = false;
    let hasData = false;
    const load = async () => {
      if (!active || loading) return;
      loading = true;
      try {
        const response = await fetch("/api/high-dividend-etfs", {
          cache: "default",
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
          headers: { Accept: "application/json" },
        });
        const payload = await response.json() as Payload;
        if (!payload.ok) throw new Error(payload.message || "資料暫時無法更新");
        if (!active) return;
        setRows(Array.isArray(payload.rows) ? payload.rows : []);
        setUpdatedAt(payload.updatedAt);
        setUniverseCount(Number(payload.universeCount) || 0);
        setStatus("ready");
        hasData = true;
      } catch (error) {
        if (!active || controller.signal.aborted || hasData) return;
        setMessage(error instanceof Error ? error.message : "資料暫時無法更新");
        setStatus("error");
      } finally {
        loading = false;
      }
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    void load();
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      controller.abort();
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const excludedSet = useMemo(() => new Set(excluded), [excluded]);
  const visibleRows = useMemo(() => rows
    .filter((row) => !excludedSet.has(row.code))
    .sort((a, b) => sort === "desc" ? b.annualYield - a.annualYield : a.annualYield - b.annualYield), [rows, excludedSet, sort]);
  const excludedRows = useMemo(() => rows.filter((row) => excludedSet.has(row.code)), [rows, excludedSet]);

  function saveExcluded(next: string[]) {
    setExcluded(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  return (
    <article className="high-dividend-etf-panel" aria-labelledby="high-dividend-etf-title">
      <header className="high-dividend-etf-head">
        <div><span className="eyebrow">HIGH DIVIDEND ETF</span><h2 id="high-dividend-etf-title">年殖利率 7% 以上 ETF</h2><p>全市場 ETF，含股票、美股、美債、投資級債及非投資級債。</p></div>
        <div className="high-dividend-etf-tools">
          <button type="button" onClick={() => setSort((value) => value === "desc" ? "asc" : "desc")}>殖利率 {sort === "desc" ? "高到低 ▼" : "低到高 ▲"}</button>
          <button type="button" className={editing ? "active" : ""} onClick={() => setEditing((value) => !value)}>{editing ? "完成編輯" : "編輯名單"}</button>
        </div>
      </header>
      <div className="high-dividend-etf-meta" aria-live="polite">
        <span><i />{status === "loading" ? "正在掃描全市場 ETF…" : status === "error" ? message : `完整符合 ${rows.length} 檔（全市場 ${universeCount} 檔）${excludedRows.length > 0 ? `｜此裝置顯示 ${visibleRows.length} 檔` : ""}`}</span>
        <small>近 12 個月配息殖利率｜更新 {taipeiTime(updatedAt)}</small>
      </div>
      {excludedRows.length > 0 && <div className="high-dividend-etf-excluded">
        <button type="button" onClick={() => setShowExcluded((value) => !value)}>此裝置已排除 {excludedRows.length} 檔 {showExcluded ? "收合" : "管理"}</button>
        {showExcluded && <div>{excludedRows.map((row) => <button type="button" key={row.code} onClick={() => saveExcluded(excluded.filter((code) => code !== row.code))}>恢復 {row.code} {row.name}</button>)}<button type="button" onClick={() => saveExcluded([])}>全部恢復</button></div>}
      </div>}
      <div className="high-dividend-etf-table">
        <div className="high-dividend-etf-row is-head"><span>排名</span><span>代號／名稱</span><span>年殖利率</span><span>配息月份</span><span>最近預定配息</span><span>成交價</span><span>ETF 類型</span>{editing && <span>編輯</span>}</div>
        {visibleRows.map((row, index) => <div className="high-dividend-etf-row" key={row.code}>
          <span>{index + 1}</span>
          <button type="button" onClick={() => onOpenKline(row.code, row.name)} title="開啟完整 K 線"><b>{row.code}</b><strong>{row.name}</strong></button>
          <strong className="etf-yield">{row.annualYield.toFixed(2)}%</strong>
          <span className="etf-months">{monthsLabel(row.distributionMonths)}</span>
          <span className="etf-next-distribution">{nextDistributionLabel(row)}</span>
          <strong className="etf-price">{row.price === null ? "—" : row.price.toFixed(2)}</strong>
          <span className="etf-type">{row.category}</span>
          {editing && <button type="button" className="etf-remove" onClick={() => saveExcluded([...new Set([...excluded, row.code])])}>排除</button>}
        </div>)}
        {status === "ready" && visibleRows.length === 0 && <div className="high-dividend-etf-empty">目前沒有符合條件且未被排除的 ETF。</div>}
        {status === "loading" && <div className="high-dividend-etf-empty">正在讀取殖利率與配息月份…</div>}
        {status === "error" && <div className="high-dividend-etf-empty is-error">{message}</div>}
      </div>
      <footer>年殖利率以近 12 個月配息合計與最新成交價估算；配息月份依近 12 個月實際紀錄顯示。最近預定配息採證交所／櫃買中心已公告的發放日與每單位金額。點代號或名稱可開啟 K 線。</footer>
    </article>
  );
}
