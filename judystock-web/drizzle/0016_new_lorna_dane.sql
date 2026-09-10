CREATE TABLE `technical_market_stock_indicators` (
	`code` text PRIMARY KEY NOT NULL,
	`market` text NOT NULL,
	`data_date` text NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `technical_market_stock_market_date_idx` ON `technical_market_stock_indicators` (`market`,`data_date`);--> statement-breakpoint
CREATE INDEX `technical_market_stock_updated_idx` ON `technical_market_stock_indicators` (`updated_at`);