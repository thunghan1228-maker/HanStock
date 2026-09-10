"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { BattleRuntimeSettings, ChipWeightKey } from "../../lib/battle-settings";
import { DEFAULT_BATTLE_SETTINGS } from "../../lib/battle-settings";
import styles from "./admin.module.css";

type ServiceStatus = {
  key: string;
  label: string;
  ok: boolean;
  status: number;
  dataDate: string;
  rowCount: number | null;
  elapsedMs: number;
  message: string;
};

type StorageStatus = Record<string, { count: number; latestDate: string | null }>;
type AuditRow = {
  id: number;
  adminEmail: string;
  action: string;
  target: string;
  details: string;
  createdAt: number;
};
type StatusPayload = {
  ok: boolean;
  checkedAt: string;
  services: ServiceStatus[];
  storage: StorageStatus;
  audit: AuditRow[];
};

const weightRows: Array<{ key: ChipWeightKey; label: string }> = [
  { key: "main", label: "主力" },
  { key: "foreign", label: "外資" },
  { key: "trust", label: "投信" },
  { key: "etf", label: "ETF 持股" },
  { key: "dealer", label: "自營自買" },
  { key: "hedge", label: "自營避險" },
];

const storageLabels: Record<string, string> = {
  dailyForceTotals: "主力每日彙總",
  intradayForceBars: "一分／五分主力 K",
  earlySellSignals: "隔日沖盤中訊號",
};

const actionLabels: Record<string, string> = {
  "settings.update": "更新戰鬥版參數",
  "data.refresh": "手動更新資料",
};

function formatTaipei(value: string | number | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function moneyLabel(value: number) {
  if (value >= 100_000_000) return `${value / 100_000_000} 億元`;
  if (value >= 10_000) return `${value / 10_000} 萬元`;
  return `${value} 元`;
}

export default function AdminDashboard({ adminName, adminUsername }: { adminName: string; adminUsername: string }) {
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [settings, setSettings] = useState<BattleRuntimeSettings>(DEFAULT_BATTLE_SETTINGS);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true);
    setError("");
    try {
      const response = await fetch("/api/admin/status", { cache: "no-store" });
      const payload = await response.json() as StatusPayload & { message?: string };
      if (!response.ok) throw new Error(payload.message ?? "狀態檢查失敗");
      setStatus(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "狀態檢查失敗");
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const response = await fetch("/api/admin/settings", { cache: "no-store" });
      const payload = await response.json() as { ok: boolean; settings?: BattleRuntimeSettings; message?: string };
      if (!response.ok || !payload.settings) throw new Error(payload.message ?? "參數讀取失敗");
      setSettings(payload.settings);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "參數讀取失敗");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void Promise.all([loadStatus(), loadSettings()]);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadSettings, loadStatus]);

  const saveSettings = async () => {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/admin/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const payload = await response.json() as { ok: boolean; settings?: BattleRuntimeSettings; message?: string };
      if (!response.ok || !payload.settings) throw new Error(payload.message ?? "設定儲存失敗");
      setSettings(payload.settings);
      setMessage("設定已儲存，戰鬥版前台會在下一次讀取時採用新參數。");
      await loadStatus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "設定儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  const refreshData = async (target: string) => {
    setRefreshing(target);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/admin/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });
      const payload = await response.json() as { ok: boolean; results?: Array<{ ok: boolean }>; message?: string };
      if (!response.ok) throw new Error(payload.message ?? "更新失敗");
      const failed = payload.results?.filter((item) => !item.ok).length ?? 0;
      setMessage(failed ? `更新已完成，其中 ${failed} 項資料仍需稍後重試。` : "資料更新完成。");
      await loadStatus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新失敗");
    } finally {
      setRefreshing(null);
    }
  };

  const logout = async () => {
    try {
      await fetch("/api/admin/session", { method: "DELETE" });
    } finally {
      window.location.assign("/admin/login");
    }
  };

  const healthyCount = status?.services.filter((item) => item.ok).length ?? 0;
  const weightTotal = useMemo(
    () => Object.values(settings.chipWeights).reduce((total, value) => total + value, 0),
    [settings.chipWeights],
  );

  const updateThreshold = (key: keyof BattleRuntimeSettings["daytradeThresholds"], value: number) => {
    setSettings((current) => ({
      ...current,
      daytradeThresholds: { ...current.daytradeThresholds, [key]: value },
    }));
  };

  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <div>
          <span className={styles.eyebrow}>HANSTOCK BATTLE CONTROL</span>
          <h1>盤中戰鬥版管理後台</h1>
          <p>行情、盤後籌碼、疑似隔日沖、三角收斂與永久資料統一管理</p>
        </div>
        <div className={styles.adminIdentity}>
          <span>管理員</span>
          <strong>{adminName}</strong>
          <small>管理帳號：{adminUsername}</small>
          <nav className={styles.adminLinks}>
            <Link href="/admin/account">修改帳號與密碼</Link>
            <Link href="/">返回戰鬥版</Link>
            <button type="button" onClick={() => void logout()}>登出</button>
          </nav>
        </div>
      </header>

      {(message || error) && <div className={error ? styles.errorBanner : styles.successBanner}>{error || message}</div>}

      <section className={styles.summaryGrid} aria-label="系統總覽">
        <article><span>服務狀態</span><strong>{healthyCount}/{status?.services.length ?? 6}</strong><small>{loadingStatus ? "檢查中" : healthyCount === status?.services.length ? "全部正常" : "部分待檢查"}</small></article>
        <article><span>最後檢查</span><strong className={styles.compactValue}>{formatTaipei(status?.checkedAt)}</strong><small>台灣時間</small></article>
        <article><span>參數版本</span><strong>全站</strong><small>後台設定會套用到前台</small></article>
        <article><span>操作保護</span><strong>已啟用</strong><small>僅管理員可更新</small></article>
      </section>


      <section className={styles.panel}>
        <header className={styles.panelHeader}>
          <div><span>DATA OPERATIONS</span><h2>資料更新與狀態</h2><p>資料未到齊時會保留原始紀錄，不會以空白覆蓋舊資料。</p></div>
          <div className={styles.headerActions}>
            <button type="button" className={styles.secondaryButton} onClick={() => void loadStatus()} disabled={loadingStatus}>{loadingStatus ? "檢查中" : "重新檢查"}</button>
            <button type="button" onClick={() => void refreshData("all")} disabled={Boolean(refreshing)}>{refreshing === "all" ? "全部更新中" : "全部立即更新"}</button>
          </div>
        </header>
        <div className={styles.serviceGrid}>
          {(status?.services ?? []).map((service) => (
            <article className={styles.serviceCard} key={service.key}>
              <div><i className={service.ok ? styles.okDot : styles.badDot} /><strong>{service.label}</strong><span>{service.ok ? "正常" : "待檢查"}</span></div>
              <dl>
                <div><dt>資料日／時間</dt><dd>{formatTaipei(service.dataDate)}</dd></div>
                <div><dt>笔数</dt><dd>{service.rowCount ?? "—"}</dd></div>
                <div><dt>讀取</dt><dd>{(service.elapsedMs / 1000).toFixed(1)} 秒</dd></div>
              </dl>
              <p>{service.message}</p>
              <button type="button" onClick={() => void refreshData(service.key)} disabled={Boolean(refreshing)}>{refreshing === service.key ? "更新中" : "立即更新"}</button>
            </article>
          ))}
          {!status && <div className={styles.loadingCard}>正在讀取戰鬥版資料狀態…</div>}
        </div>
      </section>

      <section className={styles.twoColumns}>
        <div className={styles.panel}>
          <header className={styles.panelHeader}><div><span>CHIP WEIGHTS</span><h2>籌碼加權參數</h2><p>儲存後作為所有裝置的預設值，前台臨時拖動不會改掉後台設定。</p></div><strong className={weightTotal === 100 ? styles.totalOk : styles.totalWarn}>合計 {weightTotal}%</strong></header>
          <div className={styles.weightList}>
            {weightRows.map((item) => (
              <label key={item.key}>
                <span>{item.label}</span>
                <input type="range" min="0" max="100" step="1" value={settings.chipWeights[item.key]} onChange={(event) => setSettings((current) => ({ ...current, chipWeights: { ...current.chipWeights, [item.key]: Number(event.target.value) } }))} />
                <strong>{settings.chipWeights[item.key]}%</strong>
              </label>
            ))}
          </div>
          <label className={styles.inlineField}><span>盤後籌碼自動更新</span><input type="number" min="1" max="60" value={settings.chipAutoRefreshMinutes} onChange={(event) => setSettings((current) => ({ ...current, chipAutoRefreshMinutes: Number(event.target.value) }))} /><em>分鐘</em></label>
        </div>

        <div className={styles.panel}>
          <header className={styles.panelHeader}><div><span>DAY-TRADE FILTER</span><h2>疑似隔日沖精選門檻</h2><p>只影響「精選名單」，查看全部仍保留每一筆原始紀錄。</p></div></header>
          <div className={styles.formGrid}>
            <label><span>最低成交金額</span><input type="number" step="10000000" value={settings.daytradeThresholds.turnoverAmount} onChange={(event) => updateThreshold("turnoverAmount", Number(event.target.value))} /><small>{moneyLabel(settings.daytradeThresholds.turnoverAmount)}</small></label>
            <label><span>最低大單淨買金額</span><input type="number" step="10000000" value={settings.daytradeThresholds.netLargeAmount} onChange={(event) => updateThreshold("netLargeAmount", Number(event.target.value))} /><small>{moneyLabel(settings.daytradeThresholds.netLargeAmount)}</small></label>
            <label><span>最低淨買占比</span><input type="number" min="0" max="100" step="0.5" value={settings.daytradeThresholds.netBuyRate} onChange={(event) => updateThreshold("netBuyRate", Number(event.target.value))} /><small>%</small></label>
            <label><span>最低疑似分数</span><input type="number" min="0" max="100" step="1" value={settings.daytradeThresholds.suspicionScore} onChange={(event) => updateThreshold("suspicionScore", Number(event.target.value))} /><small>分</small></label>
            <label><span>強勢大單最低漲幅</span><input type="number" min="-10" max="10" step="0.5" value={settings.daytradeThresholds.strongDayChangePct} onChange={(event) => updateThreshold("strongDayChangePct", Number(event.target.value))} /><small>%</small></label>
            <label><span>最低尾盘集中度</span><input type="number" min="0" max="100" step="0.5" value={settings.daytradeThresholds.strongLateBuyConcentration} onChange={(event) => updateThreshold("strongLateBuyConcentration", Number(event.target.value))} /><small>%</small></label>
          </div>
        </div>
      </section>

      <div className={styles.saveBar}>
        <span>設定修改後，請按右側按鈕才會正式套用。</span>
        <button type="button" className={styles.secondaryButton} onClick={() => setSettings(DEFAULT_BATTLE_SETTINGS)} disabled={saving}>恢复建议值</button>
        <button type="button" onClick={() => void saveSettings()} disabled={saving}>{saving ? "儲存中" : "儲存並套用"}</button>
      </div>

      <section className={styles.twoColumns}>
        <div className={styles.panel}>
          <header className={styles.panelHeader}><div><span>PERMANENT STORAGE</span><h2>永久資料保存</h2><p>盤中主力與訊號保存在戰鬥版資料庫，跨日與重新部署不會消失。</p></div></header>
          <div className={styles.storageList}>
            {Object.entries(status?.storage ?? {}).map(([key, value]) => <article key={key}><span>{storageLabels[key] ?? key}</span><strong>{value.count.toLocaleString("zh-TW")}</strong><small>最新資料日 {value.latestDate ?? "—"}</small></article>)}
          </div>
        </div>
        <div className={styles.panel}>
          <header className={styles.panelHeader}><div><span>AUDIT LOG</span><h2>最近操作紀錄</h2><p>記錄參數修改與手動更新，不顯示密碼或 Token。</p></div></header>
          <div className={styles.auditList}>
            {(status?.audit ?? []).map((row) => <article key={row.id}><i /><div><strong>{actionLabels[row.action] ?? row.action}</strong><span>{row.target}</span></div><time>{formatTaipei(row.createdAt)}</time></article>)}
            {status?.audit.length === 0 && <p className={styles.emptyText}>尚無後台操作紀錄。</p>}
          </div>
        </div>
      </section>
    </main>
  );
}
