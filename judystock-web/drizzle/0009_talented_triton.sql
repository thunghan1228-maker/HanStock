CREATE TABLE `broker_branch_weekly` (
	`week_end_date` text NOT NULL,
	`ticker` text NOT NULL,
	`net_amount` real NOT NULL,
	`concentration` real NOT NULL,
	`active_branches` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `broker_branch_weekly_date_ticker_idx` ON `broker_branch_weekly` (`week_end_date`,`ticker`);--> statement-breakpoint
CREATE INDEX `broker_branch_weekly_ticker_date_idx` ON `broker_branch_weekly` (`ticker`,`week_end_date`);