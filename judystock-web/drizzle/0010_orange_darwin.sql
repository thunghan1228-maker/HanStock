CREATE TABLE `weekly_main_force_history` (
	`week_end_date` text NOT NULL,
	`ticker` text NOT NULL,
	`institutional_score` real NOT NULL,
	`broker_branch_score` real NOT NULL,
	`tdcc_large_holder_score` real NOT NULL,
	`composite_score` real NOT NULL,
	`label` text NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `weekly_main_force_date_ticker_idx` ON `weekly_main_force_history` (`week_end_date`,`ticker`);--> statement-breakpoint
CREATE INDEX `weekly_main_force_ticker_date_idx` ON `weekly_main_force_history` (`ticker`,`week_end_date`);