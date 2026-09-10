"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import styles from "../auth.module.css";

export default function SetupForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("正在驗證設定連結…");
  const [valid, setValid] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const nextToken = new URLSearchParams(window.location.search).get("token") ?? "";
    void fetch(`/api/admin/setup?token=${encodeURIComponent(nextToken)}`, { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json() as { ok?: boolean; message?: string };
        if (!response.ok || !data.ok) throw new Error(data.message || "設定連結無效");
        setValid(true);
        setMessage("設定連結有效，請建立你自己的管理員帳號與密碼。");
      })
      .catch((error) => setMessage(error instanceof Error ? error.message : "設定連結無效"));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    try {
      const token = new URLSearchParams(window.location.search).get("token") ?? "";
      const response = await fetch("/api/admin/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, username, password, confirmPassword }),
      });
      const data = await response.json() as { ok?: boolean; message?: string };
      if (!response.ok || !data.ok) throw new Error(data.message || "設定失敗");
      setMessage("帳號設定完成，正在進入管理後台…");
      window.location.assign("/admin");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "設定失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className={styles.shell}>
      <section className={styles.card}>
        <span className={styles.eyebrow}>ONE-TIME ADMIN SETUP</span>
        <h1>首次設定管理員</h1>
        <p className={styles.description}>這個頁面只供首次建立帳密使用。密碼只會保存安全驗證值，網站不會顯示你的真實密碼。</p>
        <div className={valid ? styles.success : styles.message}>{message}</div>
        {valid ? (
          <form className={styles.form} onSubmit={submit}>
            <label>
              新管理員帳號
              <input autoComplete="username" autoFocus value={username} onChange={(event) => setUsername(event.target.value)} required />
              <small>3～32 個英文字母、數字、點、底線或連字號。</small>
            </label>
            <label>
              新管理員密碼
              <input autoComplete="new-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={10} required />
              <small>至少 10 個字元，請使用只有你知道的密碼。</small>
            </label>
            <label>
              再輸入一次密碼
              <input autoComplete="new-password" type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={10} required />
            </label>
            <button className={styles.primary} disabled={loading} type="submit">{loading ? "設定中…" : "建立管理員帳號"}</button>
          </form>
        ) : null}
        <div className={styles.links}><Link href="/admin/login">前往登入頁</Link><Link href="/">返回戰鬥版</Link></div>
      </section>
    </main>
  );
}
