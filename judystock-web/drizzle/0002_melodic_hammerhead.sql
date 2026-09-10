CREATE TABLE `early_sell_signals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`trade_date` text NOT NULL,
	`ticker` text NOT NULL,
	`name` text NOT NULL,
	`kind` text NOT NULL,
	`label` text NOT NULL,
	`bar_ts` integer NOT NULL,
	`price` real NOT NULL,
	`note` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `early_sell_signal_unique_idx` ON `early_sell_signals` (`trade_date`,`ticker`,`kind`,`bar_ts`);--> statement-breakpoint
CREATE INDEX `early_sell_signal_date_time_idx` ON `early_sell_signals` (`trade_date`,`bar_ts`);