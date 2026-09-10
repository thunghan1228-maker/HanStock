CREATE TABLE `daily_force_totals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`ticker` text NOT NULL,
	`trade_date` text NOT NULL,
	`net_volume` integer NOT NULL,
	`bar_count` integer NOT NULL,
	`source_interval` text NOT NULL,
	`last_bar_at` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `daily_force_ticker_date_idx` ON `daily_force_totals` (`ticker`,`trade_date`);