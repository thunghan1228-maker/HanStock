import { index, integer, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";



export const dailyForceTotals = sqliteTable(
  "daily_force_totals",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    ticker: text("ticker").notNull(),
    tradeDate: text("trade_date").notNull(),
    netVolume: integer("net_volume").notNull(),
    barCount: integer("bar_count").notNull(),
    sourceInterval: text("source_interval").notNull(),
    lastBarAt: text("last_bar_at").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [uniqueIndex("daily_force_ticker_date_idx").on(table.ticker, table.tradeDate)],
);

export const intradayForceBars = sqliteTable(
  "intraday_force_bars",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    ticker: text("ticker").notNull(),
    tradeDate: text("trade_date").notNull(),
    interval: text("interval").notNull(),
    barTime: text("bar_time").notNull(),
    netVolume: integer("net_volume").notNull(),
    mainTickCount: integer("main_tick_count").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("intraday_force_ticker_interval_time_idx").on(table.ticker, table.interval, table.barTime),
  ],
);

export const earlySellSignals = sqliteTable(
  "early_sell_signals",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    tradeDate: text("trade_date").notNull(),
    ticker: text("ticker").notNull(),
    name: text("name").notNull(),
    kind: text("kind").notNull(),
    label: text("label").notNull(),
    barTs: integer("bar_ts").notNull(),
    price: real("price").notNull(),
    note: text("note").notNull(),
    createdAt: integer("created_at").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("early_sell_signal_unique_idx").on(table.tradeDate, table.ticker, table.kind, table.barTs),
    index("early_sell_signal_date_time_idx").on(table.tradeDate, table.barTs),
  ],
);

export const adminUsers = sqliteTable(
  "admin_users",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    email: text("email").notNull(),
    username: text("username"),
    displayName: text("display_name"),
    passwordHash: text("password_hash"),
    passwordSalt: text("password_salt"),
    passwordIterations: integer("password_iterations"),
    isActive: integer("is_active", { mode: "boolean" }).notNull().default(true),
    failedLoginCount: integer("failed_login_count").notNull().default(0),
    lockedUntil: integer("locked_until"),
    createdAt: integer("created_at").notNull(),
    lastLoginAt: integer("last_login_at"),
    passwordUpdatedAt: integer("password_updated_at"),
  },
  (table) => [
    uniqueIndex("admin_users_email_idx").on(table.email),
    uniqueIndex("admin_users_username_idx").on(table.username),
  ],
);

export const adminSessions = sqliteTable(
  "admin_sessions",
  {
    tokenHash: text("token_hash").primaryKey(),
    adminUserId: integer("admin_user_id").notNull(),
    createdAt: integer("created_at").notNull(),
    expiresAt: integer("expires_at").notNull(),
    lastSeenAt: integer("last_seen_at").notNull(),
  },
  (table) => [index("admin_sessions_user_expiry_idx").on(table.adminUserId, table.expiresAt)],
);

export const adminSetupTokens = sqliteTable("admin_setup_tokens", {
  tokenHash: text("token_hash").primaryKey(),
  createdAt: integer("created_at").notNull(),
  expiresAt: integer("expires_at").notNull(),
  usedAt: integer("used_at"),
});

export const adminSettings = sqliteTable("admin_settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
  updatedAt: integer("updated_at").notNull(),
  updatedBy: text("updated_by").notNull(),
});

export const adminAuditLogs = sqliteTable(
  "admin_audit_logs",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    adminEmail: text("admin_email").notNull(),
    action: text("action").notNull(),
    target: text("target").notNull(),
    details: text("details").notNull(),
    createdAt: integer("created_at").notNull(),
  },
  (table) => [index("admin_audit_created_at_idx").on(table.createdAt)],
);

export const tdccWeeklySnapshots = sqliteTable(
  "tdcc_weekly_snapshots",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    dataDate: text("data_date").notNull(),
    ticker: text("ticker").notNull(),
    largeHolderPct: real("large_holder_pct").notNull(),
    updatedAt: integer("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("tdcc_weekly_date_ticker_idx").on(table.dataDate, table.ticker),
    index("tdcc_weekly_ticker_date_idx").on(table.ticker, table.dataDate),
  ],
);

export const watchlistSyncState = sqliteTable("watchlist_sync_state", {
  userEmail: text("user_email").primaryKey(),
  payload: text("payload").notNull(),
  revision: integer("revision").notNull().default(1),
  primaryDeviceId: text("primary_device_id").notNull(),
  updatedByDeviceId: text("updated_by_device_id").notNull(),
  updatedByDeviceKind: text("updated_by_device_kind").notNull(),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
});

export const intradayTrackingSyncState = sqliteTable("intraday_tracking_sync_state", {
  userEmail: text("user_email").primaryKey(),
  payload: text("payload").notNull(),
  revision: integer("revision").notNull().default(1),
  updatedByDeviceId: text("updated_by_device_id").notNull(),
  updatedByDeviceKind: text("updated_by_device_kind").notNull(),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
});

export const weeklyChipHistory = sqliteTable("weekly_chip_history", {
  weekEndDate: text("week_end_date").primaryKey(),
  comparedWeekEndDate: text("compared_week_end_date"),
  payloadGzipBase64: text("payload_gzip_base64").notNull(),
  summaryJson: text("summary_json").notNull(),
  savedAt: integer("saved_at").notNull(),
}, (table) => [index("weekly_chip_history_saved_idx").on(table.savedAt)]);

export const weeklyChipPrices = sqliteTable("weekly_chip_prices", {
  weekEndDate: text("week_end_date").notNull(),
  ticker: text("ticker").notNull(),
  closePrice: real("close_price").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("weekly_chip_price_week_ticker_idx").on(table.weekEndDate, table.ticker),
  index("weekly_chip_prices_ticker_idx").on(table.ticker, table.weekEndDate),
]);

export const brokerBranchWeekly = sqliteTable("broker_branch_weekly", {
  weekEndDate: text("week_end_date").notNull(),
  ticker: text("ticker").notNull(),
  netAmount: real("net_amount").notNull(),
  concentration: real("concentration").notNull(),
  activeBranches: integer("active_branches").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("broker_branch_weekly_date_ticker_idx").on(table.weekEndDate, table.ticker),
  index("broker_branch_weekly_ticker_date_idx").on(table.ticker, table.weekEndDate),
]);

export const brokerBranchDaily = sqliteTable("broker_branch_daily", {
  tradeDate: text("trade_date").notNull(),
  ticker: text("ticker").notNull(),
  netAmount: real("net_amount").notNull(),
  concentration: real("concentration").notNull(),
  activeBranches: integer("active_branches").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("broker_branch_daily_date_ticker_idx").on(table.tradeDate, table.ticker),
  index("broker_branch_daily_ticker_date_idx").on(table.ticker, table.tradeDate),
]);

export const weeklyMainForceHistory = sqliteTable("weekly_main_force_history", {
  weekEndDate: text("week_end_date").notNull(),
  ticker: text("ticker").notNull(),
  institutionalScore: real("institutional_score").notNull(),
  brokerBranchScore: real("broker_branch_score").notNull(),
  tdccLargeHolderScore: real("tdcc_large_holder_score").notNull(),
  compositeScore: real("composite_score").notNull(),
  label: text("label").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("weekly_main_force_date_ticker_idx").on(table.weekEndDate, table.ticker),
  index("weekly_main_force_ticker_date_idx").on(table.ticker, table.weekEndDate),
]);

export const technicalMarketDailySnapshots = sqliteTable("technical_market_daily_snapshots", {
  tradeDate: text("trade_date").primaryKey(),
  payloadJson: text("payload_json").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [index("technical_market_daily_updated_idx").on(table.updatedAt)]);

export const riverRadarDaily = sqliteTable("river_radar_daily", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  dataDate: text("data_date").notNull(),
  stockCode: text("stock_code").notNull(),
  market: text("market").notNull(),
  score: real("score").notNull(),
  status: text("status").notNull(),
  side: text("side").notNull(),
  payloadJson: text("payload_json").notNull(),
  engineVersion: text("engine_version").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("river_radar_daily_date_code_idx").on(table.dataDate, table.stockCode),
  index("river_radar_daily_date_score_idx").on(table.dataDate, table.score),
]);

export const riverRadarConfig = sqliteTable("river_radar_config", {
  configKey: text("config_key").primaryKey(),
  configJson: text("config_json").notNull(),
  engineVersion: text("engine_version").notNull(),
  updatedAt: integer("updated_at").notNull(),
});

export const riverRadarIntraday = sqliteTable("river_radar_intraday", {
  id: integer("id").primaryKey({ autoIncrement: true }), tradeDate: text("trade_date").notNull(), stockCode: text("stock_code").notNull(), barTs: integer("bar_ts").notNull(), score: real("score").notNull(), side: text("side").notNull(), payloadJson: text("payload_json").notNull(), updatedAt: integer("updated_at").notNull(),
}, (table) => [uniqueIndex("river_radar_intraday_date_code_idx").on(table.tradeDate, table.stockCode), index("river_radar_intraday_date_score_idx").on(table.tradeDate, table.score)]);

export const riverRadarSignals = sqliteTable("river_radar_signals", {
  id: integer("id").primaryKey({ autoIncrement: true }), tradeDate: text("trade_date").notNull(), stockCode: text("stock_code").notNull(), direction: text("direction").notNull(), barTs: integer("bar_ts").notNull(), score: real("score").notNull(), payloadJson: text("payload_json").notNull(), updatedAt: integer("updated_at").notNull(),
}, (table) => [uniqueIndex("river_radar_signal_date_code_direction_idx").on(table.tradeDate, table.stockCode, table.direction), index("river_radar_signal_date_time_idx").on(table.tradeDate, table.barTs)]);

export const riverRadarStrategySignals = sqliteTable("river_radar_strategy_signals", {
  id: integer("id").primaryKey({ autoIncrement: true }), tradeDate: text("trade_date").notNull(), stockCode: text("stock_code").notNull(), strategyKind: text("strategy_kind").notNull(), direction: text("direction").notNull(), barTs: integer("bar_ts").notNull(), score: real("score").notNull(), payloadJson: text("payload_json").notNull(), updatedAt: integer("updated_at").notNull(),
}, (table) => [uniqueIndex("river_strategy_signal_date_code_kind_idx").on(table.tradeDate, table.stockCode, table.strategyKind), index("river_strategy_signal_date_time_idx").on(table.tradeDate, table.barTs)]);

export const technicalMarketStockIndicators = sqliteTable("technical_market_stock_indicators", {
  code: text("code").primaryKey(),
  market: text("market").notNull(),
  dataDate: text("data_date").notNull(),
  payloadJson: text("payload_json").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  index("technical_market_stock_market_date_idx").on(table.market, table.dataDate),
  index("technical_market_stock_updated_idx").on(table.updatedAt),
]);

export const monthlyRevenueSnapshots = sqliteTable("monthly_revenue_snapshots", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  revenueMonth: text("revenue_month").notNull(),
  stockCode: text("stock_code").notNull(),
  name: text("name").notNull(),
  market: text("market").notNull(),
  revenue: integer("revenue").notNull(),
  previousMonthRevenue: integer("previous_month_revenue"),
  previousYearRevenue: integer("previous_year_revenue"),
  momPct: real("mom_pct"),
  yoyPct: real("yoy_pct"),
  sourcePublishedDate: text("source_published_date").notNull(),
  firstObservedAt: integer("first_observed_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  uniqueIndex("monthly_revenue_month_code_idx").on(table.revenueMonth, table.stockCode),
  index("monthly_revenue_code_month_idx").on(table.stockCode, table.revenueMonth),
]);

export const monthlyRevenueSignals = sqliteTable("monthly_revenue_signals", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  revenueMonth: text("revenue_month").notNull(),
  stockCode: text("stock_code").notNull(),
  name: text("name").notNull(),
  market: text("market").notNull(),
  signalKind: text("signal_kind").notNull(),
  revenue: integer("revenue").notNull(),
  previousMonthRevenue: integer("previous_month_revenue"),
  previousYearRevenue: integer("previous_year_revenue"),
  momPct: real("mom_pct"),
  yoyPct: real("yoy_pct"),
  comparisonRevenue: integer("comparison_revenue").notNull(),
  comparisonMonth: text("comparison_month").notNull(),
  historyMonths: integer("history_months").notNull(),
  sourcePublishedDate: text("source_published_date").notNull(),
  firstObservedAt: integer("first_observed_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
  active: integer("active", { mode: "boolean" }).notNull().default(true),
}, (table) => [
  uniqueIndex("monthly_revenue_signal_unique_idx").on(table.revenueMonth, table.stockCode, table.signalKind),
  index("monthly_revenue_signal_observed_idx").on(table.firstObservedAt),
  index("monthly_revenue_signal_month_kind_idx").on(table.revenueMonth, table.signalKind),
]);

export const monthlyRevenueSyncState = sqliteTable("monthly_revenue_sync_state", {
  revenueMonth: text("revenue_month").primaryKey(),
  sourcePublishedDate: text("source_published_date").notNull(),
  coverageTotal: integer("coverage_total").notNull(),
  coverageTwse: integer("coverage_twse").notNull(),
  coverageTpex: integer("coverage_tpex").notNull(),
  sourcesJson: text("sources_json").notNull(),
  checkedAt: integer("checked_at").notNull(),
  completedAt: integer("completed_at").notNull(),
});

export const fundamentalRiverQueries = sqliteTable("fundamental_river_queries", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  queryDate: text("query_date").notNull(),
  ticker: text("ticker").notNull(),
  name: text("name").notNull(),
  market: text("market").notNull(),
  groupName: text("group_name").notNull(),
  queryCount: integer("query_count").notNull().default(1),
  lastQueriedAt: integer("last_queried_at").notNull(),
}, (table) => [
  uniqueIndex("fundamental_river_query_date_ticker_idx").on(table.queryDate, table.ticker),
  index("fundamental_river_query_date_count_idx").on(table.queryDate, table.queryCount),
]);

export const fundamentalRiverSnapshots = sqliteTable("fundamental_river_snapshots", {
  ticker: text("ticker").primaryKey(),
  name: text("name").notNull(),
  market: text("market").notNull(),
  groupName: text("group_name").notNull(),
  reportPeriod: text("report_period").notNull(),
  ttmEps: real("ttm_eps"),
  waterline: real("waterline"),
  position: text("position").notNull(),
  distancePct: real("distance_pct"),
  close: real("close"),
  maScore: real("ma_score"),
  pe: real("pe"),
  pb: real("pb"),
  updatedAt: integer("updated_at").notNull(),
  reportChangedAt: integer("report_changed_at").notNull(),
}, (table) => [
  index("fundamental_river_report_changed_idx").on(table.reportChangedAt),
  index("fundamental_river_position_ma_idx").on(table.position, table.maScore),
]);
