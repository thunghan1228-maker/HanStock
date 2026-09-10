CREATE TABLE `tdcc_weekly_snapshots` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`data_date` text NOT NULL,
	`ticker` text NOT NULL,
	`large_holder_pct` real NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `tdcc_weekly_date_ticker_idx` ON `tdcc_weekly_snapshots` (`data_date`,`ticker`);--> statement-breakpoint
CREATE INDEX `tdcc_weekly_ticker_date_idx` ON `tdcc_weekly_snapshots` (`ticker`,`data_date`);