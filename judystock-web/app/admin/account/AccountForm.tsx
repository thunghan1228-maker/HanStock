"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import styles from "../auth.module.css";

export default function AccountForm({ currentUsername }: { currentUsername: string }) {
  const [newUsername, setNewUsername] = useState(currentUsername);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setOk(false);
    setMessage("");
    try {
      const response = await fetch("/api/admin/account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ newUsername, currentPassword, newPassword, confirmPassword }),
      });
      const data = await response.json() as { ok?: boolean; message?: string; username?: string };
      if (!response.ok || !data.ok) throw new Error(data.message || "修改失敗");
      setOk(true);
      setMessage("管理員帳號設定已更新。");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "修改失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className={styles.shell}>
      <section className={styles.card}>
        <span className={styles.eyebrow}>ADMIN ACCOUNT</span>
        <h1>修改管理員帳密</h1>
        <p className={styles.description}>可修改登入帳號或密碼。為了安全，儲存前必須輸入目前密碼。</p>
        <form className={styles.form} onSubmit={submit}>
          <label>
            管理員帳號
            <input autoComplete="username" value={newUsername} onChange={(event) => setNewUsername(event.target.value)} required />
            <small>3～32 個英文字母、數字、點、底線或連字號。</small>
          </label>
          <label>
            目前密碼
            <input autoComplete="current-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
          </label>
          <div className={styles.divider} />
          <label>
            新密碼（不修改可留空）
            <input autoComplete="new-password" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={newPassword ? 10 : undefined} />
          </label>
          <label>
            再輸入一次新密碼
            <input autoComplete="new-password" type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
          </label>
          {message ? <div className={ok ? styles.success : styles.error}>{message}</div> : null}
          <button className={styles.primary} disabled={loading} type="submit">{loading ? "儲存中…" : "儲存帳號設定"}</button>
        </form>
        <div className={styles.links}><Link href="/admin">返回管理後台</Link><Link href="/">返回戰鬥版</Link></div>
      </section>
    </main>
  );
}
