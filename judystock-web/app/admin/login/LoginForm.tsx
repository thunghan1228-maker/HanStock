"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import styles from "../auth.module.css";

function safeReturnTo() {
  const value = new URLSearchParams(window.location.search).get("return_to") ?? "/admin";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/admin";
}

export default function LoginForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setMessage("");
    try {
      const response = await fetch("/api/admin/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json() as { ok?: boolean; message?: string };
      if (!response.ok || !data.ok) throw new Error(data.message || "登入失敗");
      window.location.assign(safeReturnTo());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "登入失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className={styles.shell}>
      <section className={styles.card}>
        <span className={styles.eyebrow}>HANSTOCK BATTLE ADMIN</span>
        <h1>戰鬥版管理後台</h1>
        <p className={styles.description}>使用戰鬥版獨立管理員帳號與密碼登入，不需要 ChatGPT 帳號。</p>
        <form className={styles.form} onSubmit={submit}>
          <label>
            管理員帳號
            <input autoComplete="username" autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required />
          </label>
          <label>
            管理員密碼
            <span className={styles.passwordRow}>
              <input autoComplete="current-password" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} required />
              <button type="button" onClick={() => setShowPassword((value) => !value)}>{showPassword ? "隱藏" : "顯示"}</button>
            </span>
          </label>
          {message ? <div className={styles.error}>{message}</div> : null}
          <button className={styles.primary} disabled={loading} type="submit">{loading ? "登入中…" : "登入管理後台"}</button>
        </form>
        <div className={styles.links}><Link href="/">返回戰鬥版</Link><span /></div>
      </section>
    </main>
  );
}
