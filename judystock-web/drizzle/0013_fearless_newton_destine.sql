CREATE TABLE `technical_market_daily_snapshots` (
	`trade_date` text PRIMARY KEY NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `technical_market_daily_updated_idx` ON `technical_market_daily_snapshots` (`updated_at`);