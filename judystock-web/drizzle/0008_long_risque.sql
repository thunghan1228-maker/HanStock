CREATE TABLE `weekly_chip_history` (
	`week_end_date` text PRIMARY KEY NOT NULL,
	`compared_week_end_date` text,
	`payload_gzip_base64` text NOT NULL,
	`summary_json` text NOT NULL,
	`saved_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `weekly_chip_history_saved_idx` ON `weekly_chip_history` (`saved_at`);--> statement-breakpoint
CREATE TABLE `weekly_chip_prices` (
	`week_end_date` text NOT NULL,
	`ticker` text NOT NULL,
	`close_price` real NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `weekly_chip_price_week_ticker_idx` ON `weekly_chip_prices` (`week_end_date`,`ticker`);--> statement-breakpoint
CREATE INDEX `weekly_chip_prices_ticker_idx` ON `weekly_chip_prices` (`ticker`,`week_end_date`);