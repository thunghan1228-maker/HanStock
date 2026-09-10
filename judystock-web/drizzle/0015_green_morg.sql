CREATE TABLE `river_radar_intraday` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`trade_date` text NOT NULL,
	`stock_code` text NOT NULL,
	`bar_ts` integer NOT NULL,
	`score` real NOT NULL,
	`side` text NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `river_radar_intraday_date_code_idx` ON `river_radar_intraday` (`trade_date`,`stock_code`);--> statement-breakpoint
CREATE INDEX `river_radar_intraday_date_score_idx` ON `river_radar_intraday` (`trade_date`,`score`);--> statement-breakpoint
CREATE TABLE `river_radar_signals` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`trade_date` text NOT NULL,
	`stock_code` text NOT NULL,
	`direction` text NOT NULL,
	`bar_ts` integer NOT NULL,
	`score` real NOT NULL,
	`payload_json` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `river_radar_signal_date_code_direction_idx` ON `river_radar_signals` (`trade_date`,`stock_code`,`direction`);--> statement-breakpoint
CREATE INDEX `river_radar_signal_date_time_idx` ON `river_radar_signals` (`trade_date`,`bar_ts`);