CREATE TABLE `river_radar_strategy_signals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`trade_date` text NOT NULL,
	`stock_code` text NOT NULL,
	`strategy_kind` text NOT NULL,
	`direction` text NOT NULL,
	`bar_ts` integer NOT NULL,
	`score` real NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `river_strategy_signal_date_code_kind_idx` ON `river_radar_strategy_signals` (`trade_date`,`stock_code`,`strategy_kind`);--> statement-breakpoint
CREATE INDEX `river_strategy_signal_date_time_idx` ON `river_radar_strategy_signals` (`trade_date`,`bar_ts`);