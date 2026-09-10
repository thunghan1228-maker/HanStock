"use client";

import { memo } from "react";
import { AfterHoursMiniChart } from "./AfterHoursMiniChart";

export const WatchlistMiniChart = memo(function WatchlistMiniChart({
  ticker,
  name,
  onOpen,
}: {
  ticker: string;
  name: string;
  onOpen: (ticker: string, name: string) => void;
}) {
  return <AfterHoursMiniChart ticker={ticker} name={name} onOpen={onOpen} />;
});
