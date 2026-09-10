"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

export default function KlinePage() {
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [returnTo, setReturnTo] = useState("/");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setTicker(params.get("ticker") ?? "");
    setName(params.get("name") ?? "");
    const back = params.get("returnTo");
    if (back && back.startsWith("/")) setReturnTo(back);
  }, []);

  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        padding: 24,
        textAlign: "center",
        background: "var(--bg)",
        color: "var(--text)",
      }}
    >
      <strong style={{ fontSize: 20 }}>
        {ticker ? `${ticker}${name ? ` ${name}` : ""}｜K 線圖` : "K 線圖"}
      </strong>
      <p style={{ color: "var(--muted)", maxWidth: 420, lineHeight: 1.6 }}>
        K 線圖功能目前未提供。
      </p>
      <Link
        href={returnTo}
        style={{
          padding: "8px 20px",
          borderRadius: 8,
          background: "var(--panel-2)",
          color: "var(--text)",
          textDecoration: "none",
        }}
      >
        ← 返回
      </Link>
    </main>
  );
}
