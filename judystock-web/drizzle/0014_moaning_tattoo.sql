CREATE TABLE `river_radar_config` (
	`config_key` text PRIMARY KEY NOT NULL,
	`config_json` text NOT NULL,
	`engine_version` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `river_radar_daily` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`data_date` text NOT NULL,
	`stock_code` text NOT NULL,
	`market` text NOT NULL,
	`score` real NOT NULL,
	`status` text NOT NULL,
	`side` text NOT NULL,
	`payload_json` text NOT NULL,
	`engine_version` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `river_radar_daily_date_code_idx` ON `river_radar_daily` (`data_date`,`stock_code`);--> statement-breakpoint
CREATE INDEX `river_radar_daily_date_score_idx` ON `river_radar_daily` (`data_date`,`score`);