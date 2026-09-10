CREATE TABLE `intraday_force_bars` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`ticker` text NOT NULL,
	`trade_date` text NOT NULL,
	`interval` text NOT NULL,
	`bar_time` text NOT NULL,
	`net_volume` integer NOT NULL,
	`main_tick_count` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `intraday_force_ticker_interval_time_idx` ON `intraday_force_bars` (`ticker`,`interval`,`bar_time`);