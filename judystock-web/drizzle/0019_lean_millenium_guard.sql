CREATE TABLE `fundamental_river_queries` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`query_date` text NOT NULL,
	`ticker` text NOT NULL,
	`name` text NOT NULL,
	`market` text NOT NULL,
	`group_name` text NOT NULL,
	`query_count` integer DEFAULT 1 NOT NULL,
	`last_queried_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `fundamental_river_query_date_ticker_idx` ON `fundamental_river_queries` (`query_date`,`ticker`);--> statement-breakpoint
CREATE INDEX `fundamental_river_query_date_count_idx` ON `fundamental_river_queries` (`query_date`,`query_count`);--> statement-breakpoint
CREATE TABLE `fundamental_river_snapshots` (
	`ticker` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`market` text NOT NULL,
	`group_name` text NOT NULL,
	`report_period` text NOT NULL,
	`ttm_eps` real,
	`waterline` real,
	`position` text NOT NULL,
	`distance_pct` real,
	`close` real,
	`ma_score` real,
	`pe` real,
	`pb` real,
	`updated_at` integer NOT NULL,
	`report_changed_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `fundamental_river_report_changed_idx` ON `fundamental_river_snapshots` (`report_changed_at`);--> statement-breakpoint
CREATE INDEX `fundamental_river_position_ma_idx` ON `fundamental_river_snapshots` (`position`,`ma_score`);