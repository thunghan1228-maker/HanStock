CREATE TABLE `monthly_revenue_signals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`revenue_month` text NOT NULL,
	`stock_code` text NOT NULL,
	`name` text NOT NULL,
	`market` text NOT NULL,
	`signal_kind` text NOT NULL,
	`revenue` integer NOT NULL,
	`previous_month_revenue` integer,
	`previous_year_revenue` integer,
	`mom_pct` real,
	`yoy_pct` real,
	`comparison_revenue` integer NOT NULL,
	`comparison_month` text NOT NULL,
	`history_months` integer NOT NULL,
	`source_published_date` text NOT NULL,
	`first_observed_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	`active` integer DEFAULT true NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `monthly_revenue_signal_unique_idx` ON `monthly_revenue_signals` (`revenue_month`,`stock_code`,`signal_kind`);--> statement-breakpoint
CREATE INDEX `monthly_revenue_signal_observed_idx` ON `monthly_revenue_signals` (`first_observed_at`);--> statement-breakpoint
CREATE INDEX `monthly_revenue_signal_month_kind_idx` ON `monthly_revenue_signals` (`revenue_month`,`signal_kind`);--> statement-breakpoint
CREATE TABLE `monthly_revenue_snapshots` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`revenue_month` text NOT NULL,
	`stock_code` text NOT NULL,
	`name` text NOT NULL,
	`market` text NOT NULL,
	`revenue` integer NOT NULL,
	`previous_month_revenue` integer,
	`previous_year_revenue` integer,
	`mom_pct` real,
	`yoy_pct` real,
	`source_published_date` text NOT NULL,
	`first_observed_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `monthly_revenue_month_code_idx` ON `monthly_revenue_snapshots` (`revenue_month`,`stock_code`);--> statement-breakpoint
CREATE INDEX `monthly_revenue_code_month_idx` ON `monthly_revenue_snapshots` (`stock_code`,`revenue_month`);--> statement-breakpoint
CREATE TABLE `monthly_revenue_sync_state` (
	`revenue_month` text PRIMARY KEY NOT NULL,
	`source_published_date` text NOT NULL,
	`coverage_total` integer NOT NULL,
	`coverage_twse` integer NOT NULL,
	`coverage_tpex` integer NOT NULL,
	`sources_json` text NOT NULL,
	`checked_at` integer NOT NULL,
	`completed_at` integer NOT NULL
);
