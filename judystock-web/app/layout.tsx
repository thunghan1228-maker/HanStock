import type { Metadata, Viewport } from "next";
import "./globals.css";
import "./stock-screener-advanced.css";
import { PwaRegister } from "./pwa-register";
import { WatchlistCloudSync } from "./watchlist-cloud-sync";
import { IntradayTrackingCloudSync } from "./intraday-tracking-cloud-sync";

export const metadata: Metadata = {
  title: "HanStock 盤中戰鬥版",
  description: "HanStock 盤中族群、個股與主力大單快速決策介面。",
  applicationName: "HanStock 盤中戰鬥版",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "HanStock",
  },
  formatDetection: {
    telephone: false,
  },
  other: {
    "codex-preview": "development",
    "mobile-web-app-capable": "yes",
  },
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    shortcut: "/favicon.svg",
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  minimumScale: 0.5,
  maximumScale: 5,
  userScalable: true,
  viewportFit: "cover",
  themeColor: "#111419",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@100..900&family=Geist+Mono:wght@100..900&display=swap"
        />
        <link
          rel="modulepreload"
          as="script"
          href="/api/kline-runtime?rev=20260908-force-gap-stability-v40&asset=%2Fassets%2Findex-CCs-RpRr.js"
        />
        <link
          rel="preload"
          as="style"
          href="/kline-assets/index-CC6d_WMB.css"
        />
      </head>
      <body className="antialiased">
        <PwaRegister />
        <WatchlistCloudSync />
        <IntradayTrackingCloudSync />
        {children}
      </body>
    </html>
  );
}
